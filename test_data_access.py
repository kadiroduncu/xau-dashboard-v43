from datetime import datetime,timezone,timedelta
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
from data_access import daily_context, reference_gold, audit_twelve

class AccessTests(unittest.TestCase):
    def setUp(self):st.cache_data.clear()
    def test_daily_dates_and_no_intraday_claim(self):
        date=(datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat()
        with patch('data_access._read',return_value=({'observations':[{'date':date,'value':'4.5'}]},None)):
            rows=daily_context('test')
        self.assertTrue(all(r['Tarih']==date and r['Sıklık']=='Günlük' for r in rows))
        self.assertIn('DXY değildir',rows[2]['Veri'])
    def test_missing_key_and_provider_error_do_not_become_zero(self):
        self.assertTrue(all(r['Değer'] is None for r in daily_context(None)))
        with patch('data_access._read',return_value=(None,'HTTP 403')):
            self.assertTrue(all(r['Değer'] is None for r in daily_context('bad')))
    def test_reference_is_explicitly_not_execution_quote(self):
        d=dict(symbol='XAU',currency='USD',price=4100,updatedAt=datetime.now(timezone.utc).isoformat())
        with patch('data_access._read',return_value=(d,None)):
            r=reference_gold()
        self.assertEqual(r['price'],4100);self.assertFalse(r['execution_quote']);self.assertNotIn('bid',r)
    def test_reference_stale_or_wrong_currency_rejected(self):
        d=dict(symbol='XAU',currency='USD',price=4100,updatedAt=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat())
        with patch('data_access._read',return_value=(d,None)):self.assertIsNone(reference_gold())
        st.cache_data.clear();d.update(currency='EUR',updatedAt=datetime.now(timezone.utc).isoformat())
        with patch('data_access._read',return_value=(d,None)):self.assertIsNone(reference_gold())
    def test_audit_does_not_expose_key_or_provider_error_body(self):
        response=SimpleNamespace(status_code=403,json=lambda:{'message':'test-secret'})
        with patch('data_access.requests.get',return_value=response):rows=audit_twelve('test-secret')
        self.assertNotIn('test-secret',str(rows));self.assertTrue(all(r['Sonuç']=='HTTP 403' for r in rows))
    def test_quote_without_bid_ask_is_reported_as_such(self):
        with patch('data_access._read',side_effect=[({'plan_limit':8},None),({'symbol':'XAU/USD','close':'4100','timestamp':1},None),({'data':[]},None),({'data':[]},None),({'data':[]},None)]):
            rows=audit_twelve('test')
        self.assertIn('bid/ask yok',rows[1]['Sonuç'])
        self.assertEqual(rows[2]['Sonuç'],'Eşleşen sembol bulunamadı')
