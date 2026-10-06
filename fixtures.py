from datetime import datetime, timezone, timedelta
import math


def snapshot():
    now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    iso = lambda t: t.isoformat()
    def bars(count, minutes):
        rows=[]
        for i in range(count):
            price = 100+0.1*math.sin(i)
            rows.append({'closed_at': iso(now-timedelta(minutes=minutes*(count-1-i))),
                         'open':price, 'high':price+1, 'low':price-1, 'close':price})
        return rows
    s={'as_of':iso(now), 'side':'SHORT', 'setup_level':101,
       'quote':{'observed_at':iso(now),'bid':100,'ask':100.5,
                'spread_history':[{'observed_at':iso(now-timedelta(seconds=30*(21-i))), 'spread':.5} for i in range(20)]},
       'm1':bars(62,1), 'm5':bars(22,5),
       'calendar':{'observed_at':iso(now), 'complete':True,
                   'coverage_start':iso(now-timedelta(days=1)), 'coverage_end':iso(now+timedelta(days=1)), 'events':[]},
       'levels':{'observed_at':iso(now), 'complete':True, 'prices':[80,101,120]},
       'quality_evidence':{'regime':True, 'macro':True, 'location':True}}
    for key in ('dxy','yield2','yield10'):
        s[key]={'observed_at':iso(now), 'change':0, 'change_period_seconds':300}
    return s


def legacy():
    return {'tradeable':True, 'allowed_sides':['SHORT','LONG'], 'hard_reasons':[],
            'suppression':False, 'obstacles':[], 'channels':{k:('GREEN','available') for k in ('JUMP','COT','GVZ','NEWS')}}
