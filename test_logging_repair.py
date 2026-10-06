"""Regressions for production NumPy scalars, sampled jumps and partial analysis."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest
from risk_panel import local_snapshot
from risk_engine import evaluate, load_config
from setup_store import SetupStore


def sample():
    now=datetime.now(timezone.utc)
    def frame(minutes, count):
        end=now.replace(second=0,microsecond=0)-timedelta(minutes=now.minute % minutes + minutes)
        return pd.DataFrame([dict(datetime=end-timedelta(minutes=(count-i-1)*minutes),
                                  open=4100.,high=4101.,low=4099.,close=4100.+(i%2)*.1) for i in range(count)])
    data=dict(sides=['SHORT'],price=4100.,obstacles=[np.float64(4101.),np.float64(4080.)],regime=np.float64(.45),
              macro_bias='HEADWIND',m1_frame=frame(1,100),m5_frame=frame(5,100),
              calendar=dict(observed_at=now.isoformat(),complete=True,events=[],
                            coverage_start=(now-timedelta(days=1)).isoformat(),coverage_end=(now+timedelta(days=1)).isoformat()))
    legacy=dict(tradeable=True,allowed_sides=['SHORT'],channels={k:('GREEN','Clear') for k in ['JUMP','COT','GVZ','NEWS']},obstacles=data['obstacles'])
    return local_snapshot(data,now,load_config()),legacy


class LoggingRepairTests(unittest.TestCase):
    def test_numpy_conditions_get_credit_and_real_snapshot_records(self):
        snapshot,legacy=sample()
        self.assertIs(snapshot['quality_evidence']['regime'],True)
        decision=evaluate(snapshot,legacy)
        self.assertEqual(decision['quality_components']['regime'],2)
        self.assertEqual(decision['quality_components']['macro'],2)
        # Nested legacy values can still be NumPy scalars; the audit writer handles them.
        snapshot['_legacy']={'tradeable':np.bool_(True),'count':np.int64(3)}
        db=SetupStore(':memory:')
        try:
            db.record(snapshot,decision)
            saved=json.loads(db.db.execute('SELECT snapshot FROM decisions').fetchone()[0])
            self.assertIs(saved['_legacy']['tradeable'],True)
            self.assertEqual(saved['_legacy']['count'],3)
        finally: db.close()

    def test_missing_quote_does_not_erase_level_analysis_or_award_room(self):
        snapshot,legacy=sample();decision=evaluate(snapshot,legacy)
        self.assertIn('INVALID_ENTRY_QUOTE',decision['reasons'])
        self.assertNotIn('INVALID_SETUP_LEVELS',decision['reasons'])
        self.assertNotIn('TAIL_RISK_HIGH',decision['reasons'])
        self.assertNotEqual(decision['level_state'],'UNKNOWN')
        self.assertEqual(decision['quality_components']['room'],0)
        self.assertFalse(decision['room_checked']);self.assertFalse(decision['tradeable'])

    def test_infinite_jump_preserves_veto_and_logs_explicit_tag(self):
        snapshot,legacy=sample()
        for bar in snapshot['m1']:bar['close']=4100.
        snapshot['m1'][-1].update(close=4101.,high=4102.)
        decision=evaluate(snapshot,legacy)
        self.assertIn('M1_JUMP',decision['reasons'])
        db=SetupStore(':memory:')
        try:
            db.record(snapshot,decision)
            saved=json.loads(db.db.execute('SELECT decision FROM decisions').fetchone()[0])
            self.assertEqual(saved['measurements']['m1_jump_sigma'],{'nonfinite':'inf'})
        finally:db.close()

    def test_streamlit_panel_logs_on_repeat_runs_with_numpy_inputs(self):
        code='''
from datetime import datetime, timezone
import numpy as np
from risk_panel import run_risk_panel
run_risk_panel(dict(tradeable=True,allowed_sides=['SHORT'],channels={},obstacles=[np.float64(4101.)]),
    dict(sides=['SHORT'],price=4100.,obstacles=[np.float64(4101.)],regime=np.float64(.4),macro_bias='HEADWIND'))
'''
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ',{'XAU_SETUP_DB':str(Path(temp)/'setups.sqlite')}):
            app=AppTest.from_string(code).run()
            app.run()
            self.assertEqual(len(app.exception),0)
            self.assertFalse(any('SETUP_LOG_UNAVAILABLE' in e.value for e in app.error))
            self.assertTrue(any('Setup kayıtları:' in e.value for e in app.caption))
            db=SetupStore(Path(temp)/'setups.sqlite')
            try:self.assertGreaterEqual(db.summary(load_config())['decisions'],2)
            finally:db.close()

    def test_recording_failure_is_visible_and_blocks(self):
        code='''
from risk_panel import run_risk_panel
run_risk_panel(dict(tradeable=False,allowed_sides=[],channels={},obstacles=[]),{})
'''
        with patch('risk_panel.SetupStore',side_effect=OSError('test storage failure')):
            app=AppTest.from_string(code).run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any('Setup kaydı başarısız (OSError)' in e.value for e in app.warning))
            self.assertTrue(any('Setup kayıt sistemi çalışmıyor' in e.value for e in app.error))
