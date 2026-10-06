import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import types
import streamlit as st
from live_feeds import parse_swissquote, record_quote, fetch_reference_quote
from risk_panel import local_snapshot
from risk_engine import evaluate, load_config
from fixtures import snapshot, legacy


class LiveFeedTests(unittest.TestCase):
    def payload(self, now):
        return [{'topo':{'platform':'AT','server':'AT'},'ts':now.timestamp()*1000,
                 'spreadProfilePrices':[{'spreadProfile':'standard','bid':100.,'ask':100.5},
                                        {'spreadProfile':'prime','bid':100.1,'ask':100.4}]}]

    def test_profile_is_pinned_no_tightest_spread_selection(self):
        now=datetime.now(timezone.utc);q=parse_swissquote(self.payload(now))
        self.assertEqual(q['bid'],100);self.assertEqual(q['ask'],100.5)
        self.assertEqual(datetime.fromisoformat(q['observed_at']),now)
        self.assertEqual(q['scope'],'REFERENCE_PAPER_ONLY')
        missing=self.payload(now);missing[0]['topo']['server']='another'
        with self.assertRaises(ValueError):parse_swissquote(missing)
        with self.assertRaises(ValueError):parse_swissquote(self.payload(now)*2)

    def test_malformed_prices_rejected(self):
        for bid,ask in [(101,100),(0,100),(100,float('nan'))]:
            p=self.payload(datetime.now(timezone.utc));p[0]['spreadProfilePrices'][0].update(bid=bid,ask=ask)
            with self.assertRaises(ValueError):parse_swissquote(p)

    def test_history_persists_and_deduplicates_without_lookahead(self):
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'quotes.sqlite'
            for i in range(21):
                when=now+timedelta(seconds=60*i)
                q=parse_swissquote(self.payload(when));q,e=record_quote(q,db,when)
                self.assertIsNone(e);self.assertEqual(len(q['spread_history']),i)
                duplicate,_=record_quote(q,db,when)
                self.assertEqual(len(duplicate['spread_history']),i)
                self.assertTrue(all(r['observed_at']<q['observed_at'] for r in q['spread_history']))
            q['ask']=101
            with self.assertRaises(ValueError):record_quote(q,db,when)

    def test_stale_future_quotes_do_not_seed_baseline(self):
        now=datetime.now(timezone.utc)
        for delta in (-91,6):
            with tempfile.TemporaryDirectory() as tmp:
                db=Path(tmp)/'quotes.sqlite';q=parse_swissquote(self.payload(now+timedelta(seconds=delta)))
                result,e=record_quote(q,db,now)
                self.assertIsNotNone(e);self.assertEqual(result['spread_history'],[])
                self.assertFalse(db.exists())

    def test_connected_quote_reaches_engine_and_spread_is_measured(self):
        s=snapshot();c=load_config();now=datetime.fromisoformat(s['as_of'])
        q=dict(s['quote'],source='Swissquote / AT / standard',scope='REFERENCE_PAPER_ONLY')
        local=local_snapshot({'quote':q},now,c)
        self.assertEqual(local['quote'],q)
        s['quote']=local['quote'];d=evaluate(s,legacy(),c)
        self.assertNotIn('MISSING:quote',d['reasons']);self.assertNotIn('INVALID_SPREAD_DATA',d['reasons'])
        self.assertIsNotNone(d['measurements']['spread_ratio'])
        # Reference price cannot invent missing macro confirmations.
        s.pop('yield10');d=evaluate(s,legacy(),c)
        self.assertFalse(d['tradeable']);self.assertIn('MISSING:yield10',d['reasons'])

    def test_fetch_failure_does_not_return_prior_success(self):
        st.cache_data.clear()
        with patch('live_feeds.requests.get',return_value=types.SimpleNamespace(status_code=403)):
            q,e=fetch_reference_quote()
        self.assertIsNone(q);self.assertIn('403',e)
        st.cache_data.clear()


class MacroFeedTests(unittest.TestCase):
    def rows(self, now, values=(100.,4.,5.)):
        from live_feeds import parse_macro, MACRO_SYMBOLS
        minute=int(now.timestamp()//60)*60
        data={'data':[{'s':s,'d':[s.split(':')[1],s,'index' if s=='TVC:DXY' else 'bond','streaming',v,minute,v,minute-60]} for s,v in zip(MACRO_SYMBOLS,values)]}
        rows=parse_macro(data)
        for row in rows.values():row['sampled_at']=now.isoformat()
        return rows

    def test_source_timestamp_not_receipt_and_no_daily_change(self):
        now=datetime(2026,10,6,12,0,25,tzinfo=timezone.utc)
        rows=self.rows(now)
        for row in rows.values():
            self.assertEqual(row['observed_at'],'2026-10-06T12:00:00+00:00')
            self.assertEqual(row['sampled_at'],now.isoformat())
            self.assertNotIn('change',row)

    def test_warmup_then_shared_measured_interval_and_units(self):
        from live_feeds import record_macro
        now=datetime(2026,10,6,12,0,25,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'macro.sqlite'
            first,error=record_macro(self.rows(now),db,now)
            self.assertIsNotNone(error);self.assertNotIn('change',first['dxy'])
            later=now+timedelta(seconds=310)
            result,error=record_macro(self.rows(later,(100.2,4.03,5.04)),db,later)
            self.assertIsNone(error)
            self.assertAlmostEqual(result['dxy']['change'],.2)
            self.assertAlmostEqual(result['yield2']['change'],3)
            self.assertAlmostEqual(result['yield10']['change'],4)
            self.assertEqual({v['change_period_seconds'] for v in result.values()},{310})
            self.assertEqual({v['sample_start'] for v in result.values()},{now.isoformat()})

    def test_old_future_observations_do_not_seed_baseline(self):
        from live_feeds import record_macro
        now=datetime(2026,10,6,12,0,25,tzinfo=timezone.utc)
        for delta in (-181,1):
            with tempfile.TemporaryDirectory() as tmp:
                db=Path(tmp)/'macro.sqlite';rows=self.rows(now)
                rows['TVC:US02Y']['observed_at']=(now+timedelta(seconds=delta)).isoformat()
                values,e=record_macro(rows,db,now)
                self.assertIsNotNone(e);self.assertNotIn('change',values['yield2'])
                self.assertFalse(db.exists())

    def test_no_baseline_across_data_gap(self):
        from live_feeds import record_macro
        now=datetime(2026,10,6,12,0,25,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'macro.sqlite';record_macro(self.rows(now),db,now)
            later=now+timedelta(minutes=15)
            values,e=record_macro(self.rows(later),db,later)
            self.assertIsNotNone(e);self.assertNotIn('change',values['dxy'])

    def test_missing_or_delayed_or_wrong_instrument_rejected(self):
        from live_feeds import parse_macro, MACRO_SYMBOLS
        now=datetime.now(timezone.utc);minute=int(now.timestamp()//60)*60
        data={'data':[{'s':s,'d':[s.split(':')[1],s,'index' if s=='TVC:DXY' else 'bond','streaming',100,minute,99,minute-60]} for s in MACRO_SYMBOLS]}
        for field,value in [(0,'UUP'),(2,'stock'),(3,'delayed_streaming_900')]:
            changed=copy.deepcopy(data);changed['data'][0]['d'][field]=value
            with self.assertRaises(ValueError):parse_macro(changed)
        with self.assertRaises(ValueError):parse_macro({'data':data['data'][:2]})

    def test_all_connected_fields_reach_snapshot(self):
        now=datetime.now(timezone.utc);rows=self.rows(now)
        values={row['key']:row for row in rows.values()}
        result=local_snapshot(values,now,load_config())
        self.assertTrue(all(k in result for k in ('dxy','yield2','yield10')))
        d=evaluate(result,legacy())
        self.assertFalse(d['tradeable'])
        self.assertIn('INVALID_CROSS_MARKET',d['reasons'])
        self.assertFalse(any('MISSING:'+k in d['reasons'] for k in ('dxy','yield2','yield10')))

if __name__=='__main__':unittest.main()
