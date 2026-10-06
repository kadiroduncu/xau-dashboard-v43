import unittest
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import patch
from official_calendar import bea_events, fed_events, fred_events, legacy_events, official_calendar, fetch_json
from risk_engine import news_reasons, load_config


class OfficialCalendarTests(unittest.TestCase):
    def test_fed_utf8_bom(self):
        response=SimpleNamespace(status_code=200, content=b'\xef\xbb\xbf{"events": []}',
                                 json=lambda: (_ for _ in ()).throw(ValueError('BOM')))
        with patch('official_calendar.requests.get',return_value=response):
            self.assertEqual(fetch_json('https://www.federalreserve.gov/json/calendar.json'), {'events':[]})

    def test_bea_dedup_and_timezone_required(self):
        date='2026-10-29T12:30:00+00:00'
        self.assertEqual(len(bea_events({'Personal Income and Outlays':{'release_dates':[date,date]}})),1)
        with self.assertRaises(ValueError):
            bea_events({'Personal Income and Outlays':{'release_dates':['2026-10-29T12:30:00']}})

    def test_fed_dst_and_actual_published_clock(self):
        rows=[dict(type='FOMC',title='FOMC Meeting',time='2:00 p.m.',month=m,days=d)
              for m,d in [('2026-10','28'),('2026-12','9')]]
        events=fed_events({'events':rows})
        self.assertEqual(events[0]['time'],'2026-10-28T18:00:00+00:00')
        self.assertEqual(events[1]['time'],'2026-12-09T19:00:00+00:00')
        rows[0]['time']=''
        with self.assertRaises(ValueError): fed_events({'events':rows})

    def test_date_only_window_spans_dst_without_fake_time(self):
        e=fred_events({'release_dates':[{'release_id':10,'date':'2026-11-01'}]},10,'CPI')[0]
        self.assertEqual(e['window_start'],'2026-11-01T04:00:00+00:00')
        self.assertEqual(e['window_end'],'2026-11-02T05:00:00+00:00')
        self.assertEqual(legacy_events([e]),[])
        cal=dict(complete=True,coverage_start='2026-10-30T00:00:00Z',coverage_end='2026-11-04T00:00:00Z',events=[e])
        reasons=news_reasons(cal,datetime(2026,11,2,4,tzinfo=timezone.utc),True,load_config())
        self.assertIn('NEWS_TIME_UNKNOWN:CPI',reasons)
        self.assertEqual(news_reasons(cal,datetime(2026,11,2,8,tzinfo=timezone.utc),True,load_config()),[])

    def test_legacy_conversion_does_not_mutate_aware_events(self):
        e=dict(event='PCE',time='2026-10-29T08:30:00-04:00')
        self.assertEqual(legacy_events([e])[0]['time'],'2026-10-29 12:30:00')
        self.assertIn('-04:00',e['time'])

    def test_missing_source_never_certifies_empty_calendar(self):
        with patch('official_calendar.fetch_json',side_effect=ValueError('apikey=secret')):
            cal,error=official_calendar('secret',datetime(2026,10,6,tzinfo=timezone.utc))
        self.assertFalse(cal['complete']); self.assertEqual(len(cal['source_errors']),4)
        self.assertNotIn('secret',error)

    def test_complete_sources_and_expired_schedule(self):
        bea={'Personal Income and Outlays':{'release_dates':['2026-09-30T12:30:00Z','2026-10-29T12:30:00Z']}}
        fed={'events':[dict(type='FOMC',title='FOMC Meeting',time='2:00 p.m.',month='2026-09',days='16'),
                       dict(type='FOMC',title='FOMC Minutes',time='2:00 p.m.',month='2026-10',days='7')]}
        # Must cover the ENTIRE requested horizon, not only today's date.
        fed['events'].append(dict(type='FOMC',title='FOMC Meeting',time='2:00 p.m.',month='2026-10',days='28'))
        def payload(rid):
            return {'release_dates':[{'release_id':rid,'date':d} for d in ['2026-09-04','2026-11-06']]}
        now=datetime(2026,10,6,tzinfo=timezone.utc)
        with patch('official_calendar.fetch_json',side_effect=[bea,fed,payload(50),payload(10)]):
            cal,error=official_calendar('test',now)
        self.assertTrue(cal['complete']); self.assertIsNone(error)
        self.assertEqual(len(cal['events']),1)
        bea['Personal Income and Outlays']['release_dates']=bea['Personal Income and Outlays']['release_dates'][:1]
        with patch('official_calendar.fetch_json',side_effect=[bea,fed,payload(50),payload(10)]):
            cal,error=official_calendar('test',now)
        self.assertFalse(cal['complete']); self.assertIn('BEA',error)

    def test_fred_wrong_release_and_truncation_rejected(self):
        with self.assertRaises(ValueError): fred_events({'release_dates':[{'release_id':50,'date':'2026-10-02'}]},10,'CPI')
        with self.assertRaises(ValueError): fred_events({'count':2,'release_dates':[{'release_id':10,'date':'2026-10-02'}]},10,'CPI')
