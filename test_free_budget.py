from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
from pathlib import Path
import tempfile
import unittest
from free_budget import claim_request

class FreeBudgetTests(unittest.TestCase):
    def test_allowance_is_atomic_across_callers_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'budget.sqlite'
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(lambda _:claim_request('secret',limit=10,path=path),range(30)))
            self.assertEqual(sum(results),10)
            self.assertFalse(claim_request('secret',limit=10,path=path))
            self.assertNotIn(b'secret',path.read_bytes())
    def test_utc_reset_and_account_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'budget.sqlite';now=datetime(2026,10,20,23,59,tzinfo=timezone.utc)
            self.assertTrue(claim_request('one',1,path,now));self.assertFalse(claim_request('one',1,path,now))
            self.assertTrue(claim_request('two',1,path,now))
            self.assertTrue(claim_request('one',1,path,now+timedelta(minutes=1)))

if __name__=='__main__':unittest.main()

class FreeProfileIntegrationTests(unittest.TestCase):
    def test_network_is_not_used_when_local_budget_is_exhausted(self):
        from unittest.mock import patch
        import market_data as md
        md._PROVIDER_COOLDOWNS.clear()
        with patch('free_budget.claim_request',return_value=False),patch('market_data.requests.get') as network:
            with self.assertRaises(md.ProviderCooldown):md.provider_get({'apikey':'test-budget'})
        network.assert_not_called()
    def test_price_reuses_fresh_reference_observation_without_twelve_data_request(self):
        import ast
        from unittest.mock import patch
        from datetime import datetime,timezone,timedelta
        import live_feeds
        tree=ast.parse(Path('xau_dashboard.py').read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='get_price_snapshot')
        function.decorator_list=[]
        env={'datetime':datetime,'timezone':timezone,'FEED_CONFIG':{'reference_quote':{'max_age_seconds':90}}}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'xau_dashboard.py','exec'),env)
        now=datetime.now(timezone.utc)
        quote={'bid':4100.,'ask':4101.,'observed_at':now.isoformat()}
        with patch.object(live_feeds,'fetch_reference_quote',return_value=(quote,None)):
            self.assertEqual(env['get_price_snapshot'](),(4100.5,quote['observed_at']))
        quote['observed_at']=(now-timedelta(minutes=10)).isoformat()
        with patch.object(live_feeds,'fetch_reference_quote',return_value=(quote,None)):
            self.assertEqual(env['get_price_snapshot'](),(0,None))
