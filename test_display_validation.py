import ast
from pathlib import Path
import unittest
from display_validation import valid_price,position_error

class MissingPriceTests(unittest.TestCase):
    def test_missing_or_invalid_values_never_become_prices(self):
        for value in [None,0,-30,float('nan'),float('inf'),True,'4100']:
            self.assertFalse(valid_price(value))
        self.assertTrue(valid_price(4100.5))
    def test_valid_manual_positions_allowed(self):
        self.assertIsNone(position_error('LONG',4100,4110,4070))
        self.assertIsNone(position_error('SHORT',4100,4090,4130))
    def test_zero_negative_or_inverted_manual_records_rejected(self):
        for args in [('LONG',0,10,-30),('LONG',4100,4090,4070),('SHORT',4100,4110,4130),('LONG',4100,4110,None),('OTHER',4100,4110,4070)]:
            self.assertIsNotNone(position_error(*args))
    def test_no_zero_price_magnets_even_if_history_exists(self):
        tree=ast.parse(Path('xau_dashboard.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='compute_magnet_levels')
        env={'valid_price':valid_price}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'xau_dashboard.py','exec'),env)
        for price in (0,None,float('nan')):
            self.assertEqual(env['compute_magnet_levels'](object(),object(),price),[])

if __name__=='__main__':unittest.main()
