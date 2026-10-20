import ast
from pathlib import Path
from unittest.mock import patch
import unittest
import pandas as pd
import test_daily_conflict_resolution as fixtures
import market_data as md
from trend_filter import trend_filter

class HistoryPageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = fixtures.DailyConflictResolutionTests()
        self.frame = self.fixtures.frame()
        self.rows = self.frame.drop_duplicates('datetime').to_dict('records')
        for row in self.rows:
            row['datetime'] = row['datetime'].isoformat()

    def recover(self, rows):
        with patch('market_data.requests.get', return_value=self.fixtures.response(rows)):
            return md.verify_daily_history_page('fixture', self.frame)

    def test_complete_matching_page_resolves_conflict_without_merging_or_guessing(self):
        result, dates = self.recover(self.rows)
        self.assertEqual(dates, ['2026-09-02'])
        self.assertEqual(list(result.close), [11.,12.,13.])
        self.assertEqual(len(self.frame), 4)

    def test_missing_date_must_not_bridge_hole(self):
        result, dates = self.recover([self.rows[0],self.rows[2]])
        self.assertFalse(dates)
        pd.testing.assert_frame_equal(result,self.frame)

    def test_changed_unambiguous_price_rejects_entire_page(self):
        self.rows[0]['close']=10.5
        result, dates = self.recover(self.rows)
        self.assertFalse(dates)
        pd.testing.assert_frame_equal(result,self.frame)

    def test_still_ambiguous_page_preserves_veto(self):
        rows=self.frame.to_dict('records')
        for row in rows:row['datetime']=row['datetime'].isoformat()
        result,dates=self.recover(rows)
        self.assertFalse(dates)
        pd.testing.assert_frame_equal(result,self.frame)

    def test_loader_recovers_after_broken_single_date_endpoint(self):
        md._time_series.clear()
        original=self.frame.to_dict('records')
        for row in original:row['datetime']=row['datetime'].isoformat()
        with patch('market_data.requests.get', side_effect=[self.fixtures.response(original),self.fixtures.response([self.rows[-1]]),self.fixtures.response(self.rows)]):
            result,note=md.time_series('recovery-integration','1day')
        self.assertEqual(len(result),3)
        self.assertIn('doğrulandı',note)

    def test_insufficient_history_never_allows_either_direction(self):
        self.assertFalse(trend_filter(self.frame)['long_allowed'])
        self.assertFalse(trend_filter(None)['short_allowed'])
        tree=ast.parse(Path('xau_dashboard.py').read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='compute_trend_gate')
        namespace={}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'xau_dashboard.py','exec'),namespace)
        for frame in (None,self.frame):
            actual=namespace['compute_trend_gate'](frame)
            self.assertFalse(actual['long_allowed']);self.assertFalse(actual['short_allowed'])

if __name__=='__main__':unittest.main()
