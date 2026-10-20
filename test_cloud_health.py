import copy
import unittest
from datetime import datetime,timezone,timedelta
from cloud_health import cloud_health

class CloudHealthTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,20,12,tzinfo=timezone.utc)
        self.state={'checked_at':self.now.isoformat(),'data_ready':True,'signal_engine_ready':True,
                    'series':{'5min':{'bars':[{'close_at':int((self.now-timedelta(minutes=5)).timestamp()*1000),'open':4100.,'high':4103.,'low':4099.,'close':4102.}]}},
                    'engine':{'green':{'direction':'LONG','kind':'BREAKOUT','entry':4102.,'target':4112.,'invalidation':4099.}}}
    def test_fresh_valid_state_preserves_candidate(self):
        self.assertTrue(cloud_health(self.state,self.now)['ready'])
        self.assertEqual(cloud_health(self.state,self.now)['green'],self.state['engine']['green'])
    def test_fresh_heartbeat_does_not_make_old_prices_current(self):
        self.state['series']['5min']['bars'][0]['close_at']-=3600000
        result=cloud_health(self.state,self.now)
        self.assertFalse(result['ready']);self.assertIsNone(result['green']);self.assertIn('M5 mumu eski',result['reason'])
    def test_bad_or_future_heartbeat_never_shows_green(self):
        for when in ['bad','2026-10-20T12:01:00+00:00','2026-10-20T12:00:00']:
            self.state['checked_at']=when
            self.assertFalse(cloud_health(self.state,self.now)['ready'])
    def test_malformed_payload_never_crashes(self):
        for value in [None,[],{}, {'checked_at':None}, {'checked_at':self.now.isoformat(),'series':[] }]:
            self.assertFalse(cloud_health(value,self.now)['ready'])
    def test_invalid_duplicate_or_future_prices_never_show_candidate(self):
        for case in ['nan','duplicate','future']:
            state=copy.deepcopy(self.state);bars=state['series']['5min']['bars']
            if case=='nan':bars[0]['close']=float('nan')
            if case=='duplicate':bars.append(copy.deepcopy(bars[0]))
            if case=='future':bars[0]['close_at']=int((self.now+timedelta(minutes=5)).timestamp()*1000)
            self.assertFalse(cloud_health(state,self.now)['ready'])
    def test_inverted_target_and_string_readiness_rejected(self):
        self.state['engine']['green']['target']=4098.
        self.assertFalse(cloud_health(self.state,self.now)['ready'])
        self.state['engine']['green']=None;self.state['data_ready']='true'
        self.assertFalse(cloud_health(self.state,self.now)['ready'])
    def test_wait_state_is_not_an_error_when_data_is_valid(self):
        self.state['engine']={'green':None,'reason':'Kapanış bekleniyor'}
        result=cloud_health(self.state,self.now)
        self.assertTrue(result['ready']);self.assertIsNone(result['green'])
        self.assertEqual(result['reason'],'Kapanış bekleniyor')

if __name__=='__main__':unittest.main()
