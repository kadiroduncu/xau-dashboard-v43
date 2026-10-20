from types import SimpleNamespace
from unittest.mock import patch
import unittest
from datetime import datetime, timezone
import market_data as md

class ProviderQuotaTests(unittest.TestCase):
    def setUp(self):md._PROVIDER_COOLDOWNS.clear()
    def tearDown(self):md._PROVIDER_COOLDOWNS.clear()
    def response(self,message):return SimpleNamespace(status_code=429,json=lambda:{'code':429,'message':message})
    def test_daily_exhaustion_blocks_all_intervals_until_next_utc_day(self):
        now=datetime(2026,10,20,12,tzinfo=timezone.utc).timestamp()
        with patch('market_data.time.time',return_value=now),patch('market_data.requests.get',return_value=self.response('Daily API credit limit reached')) as get:
            md.provider_get({'apikey':'test-secret','interval':'1day'})
            with self.assertRaises(md.ProviderCooldown):md.provider_get({'apikey':'test-secret','interval':'1min'})
            self.assertEqual(get.call_count,1)
        self.assertNotIn('test-secret',str(md._PROVIDER_COOLDOWNS))
        until=next(iter(md._PROVIDER_COOLDOWNS.values()))[0]
        self.assertEqual(datetime.fromtimestamp(until,timezone.utc).isoformat(),'2026-10-21T00:00:02+00:00')
        with patch('market_data.time.time',return_value=until+1),patch('market_data.requests.get',return_value=SimpleNamespace(status_code=200,json=lambda:{'values':[{}]})) as get:
            md.provider_get({'apikey':'test-secret','interval':'1min'})
            self.assertEqual(get.call_count,1)
    def test_minute_limit_does_not_disable_all_day(self):
        with patch('market_data.time.time',return_value=120.5),patch('market_data.requests.get',return_value=self.response('API credits per minute exhausted')):
            md.provider_get({'apikey':'minute-test'})
        self.assertEqual(next(iter(md._PROVIDER_COOLDOWNS.values()))[0],182)
    def test_one_accounts_quota_does_not_block_other_account(self):
        with patch('market_data.requests.get',return_value=self.response('Daily limit reached')) as get:
            md.provider_get({'apikey':'first'})
            md.provider_get({'apikey':'second'})
            self.assertEqual(get.call_count,2)
