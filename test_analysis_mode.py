import unittest
from datetime import timedelta
from test_logging_repair import sample
from risk_panel import analysis_status, compact_execution_reasons
from risk_engine import evaluate, load_config, candidate_approved, timestamp


class AnalysisModeTests(unittest.TestCase):
    def assess(self,s,l):
        d=evaluate(s,l)
        return d,analysis_status(s,l,d,load_config())

    def test_useful_chart_candidate_never_approves_missing_execution(self):
        s,l=sample();d,a=self.assess(s,l)
        self.assertEqual(a['status'],'İZLEME ADAYI')
        self.assertEqual(a['side'],'SHORT')
        self.assertFalse(d['tradeable']);self.assertFalse(a['execution_approved'])
        self.assertFalse(candidate_approved('SHORT',4100,d))

    def test_near_obstacle_blocks_chart_target(self):
        s,l=sample();s['levels']['prices'].append(4099.)
        d,a=self.assess(s,l)
        self.assertEqual(a['status'],'BEKLE')
        self.assertTrue(any('hedefi önünde engel' in r for r in a['reasons']))
        self.assertFalse(d['tradeable'])

    def test_stale_candles_and_news_are_not_removed(self):
        s,l=sample()
        for b in s['m1']:b['closed_at']=(timestamp(b['closed_at'])-timedelta(hours=1)).isoformat()
        _,a=self.assess(s,l);self.assertEqual(a['status'],'VERİ EKSİK')
        s,l=sample();s['calendar']['events']=[{'country':'US','event':'CPI','time':s['as_of']}]
        _,a=self.assess(s,l);self.assertTrue(any(r.startswith('NEWS_WINDOW:') for r in a['reasons']))
        self.assertNotEqual(a['status'],'İZLEME ADAYI')

    def test_summary_only_deduplicates_derivative_connection_errors(self):
        raw=['MISSING:quote','INVALID_ENTRY_QUOTE','INVALID_SPREAD_DATA','MISSING:dxy','INVALID_CROSS_MARKET','ACCEPTANCE']
        compact=compact_execution_reasons(raw)
        self.assertEqual(compact,['MISSING:quote','MISSING:dxy','ACCEPTANCE'])
        self.assertEqual(len(raw),6)
