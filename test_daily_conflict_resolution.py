from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import pandas as pd
import market_data as md

class DailyConflictResolutionTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame([
            {'datetime':pd.Timestamp('2026-09-01'),'open':10.,'high':12.,'low':9.,'close':11.},
            {'datetime':pd.Timestamp('2026-09-02'),'open':11.,'high':13.,'low':10.,'close':12.},
            {'datetime':pd.Timestamp('2026-09-02'),'open':11.,'high':14.,'low':10.,'close':13.},
            {'datetime':pd.Timestamp('2026-09-03'),'open':12.,'high':14.,'low':11.,'close':13.}])
    def response(self, rows):
        return SimpleNamespace(status_code=200,json=lambda:{'meta':{'symbol':'XAU/USD','interval':'1day'},'values':rows})
    def row(self,close='12',high='13'):
        return {'datetime':'2026-09-02','open':'11','high':high,'low':'10','close':close}
    def test_unique_date_query_recovers_history_without_changing_prices(self):
        original=self.frame()
        with patch('market_data.requests.get',return_value=self.response([self.row()])) as get:
            actual,dates=md.verify_conflicting_daily_candles('fixture-key',original)
        self.assertEqual(dates,['2026-09-02']);self.assertEqual(len(actual),3)
        self.assertEqual(actual.loc[actual.datetime==pd.Timestamp('2026-09-02'),'close'].iloc[0],12.)
        self.assertEqual(len(original),4)
        params=get.call_args.kwargs['params']
        self.assertEqual(params['start_date'],'2026-09-02T00:00:00');self.assertEqual(params['end_date'],'2026-09-02T23:59:59');self.assertNotIn('outputsize',params)
    def test_ambiguous_date_response_cannot_choose_first_or_last(self):
        frame=self.frame()
        with patch('market_data.requests.get',return_value=self.response([self.row(),self.row('13','14')])):
            actual,dates=md.verify_conflicting_daily_candles('fixture-key',frame)
        self.assertFalse(dates);pd.testing.assert_frame_equal(actual,frame)
    def test_new_third_price_cannot_replace_conflicting_candidates(self):
        frame=self.frame()
        with patch('market_data.requests.get',return_value=self.response([self.row('11.5')])):
            actual,dates=md.verify_conflicting_daily_candles('fixture-key',frame)
        self.assertFalse(dates);pd.testing.assert_frame_equal(actual,frame)
    def test_wrong_symbol_and_invalid_prices_are_rejected(self):
        for payload in [{'meta':{'symbol':'EUR/USD'},'values':[self.row()]},{'values':[self.row('nan')]},{'values':[self.row('20','13')]}]:
            with self.subTest(payload=payload),patch('market_data.requests.get',return_value=SimpleNamespace(status_code=200,json=lambda:payload)):
                frame=self.frame();actual,dates=md.verify_conflicting_daily_candles('fixture-key',frame)
                self.assertFalse(dates);pd.testing.assert_frame_equal(actual,frame)
    def test_quota_failure_preserves_conflict(self):
        with patch('market_data.requests.get',return_value=SimpleNamespace(status_code=429,json=lambda:{'message':'api_key=never-print'})):
            frame=self.frame();actual,dates=md.verify_conflicting_daily_candles('fixture-key',frame)
        self.assertFalse(dates);pd.testing.assert_frame_equal(actual,frame)
    def test_full_loader_uses_verified_date_and_retains_latest_suffix(self):
        md._time_series.clear()
        frame=self.frame();rows=frame.to_dict('records')
        for row in rows:row['datetime']=row['datetime'].isoformat()
        with patch('market_data.requests.get',side_effect=[self.response(rows),self.response([self.row()])]):
            actual,note=md.time_series('integration-fixture','1day')
        self.assertEqual(len(actual),3);self.assertEqual(actual.datetime.iloc[-1],pd.Timestamp('2026-09-03'));self.assertIn('doğrulandı',note)

if __name__=='__main__':unittest.main()
