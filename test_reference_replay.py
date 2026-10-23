import unittest
from reference_replay import replay_reference, merge_reference_rows, STEP

T = 1792555200000


def signal(side='LONG'):
    return dict(id=f'{T}:S3:{side}:RETEST', at='2026-10-21T04:02:06+00:00',
                direction=side, kind='RETEST', entry=100, target=110 if side=='LONG' else 90,
                invalidation=95 if side=='LONG' else 105)


def bar(offset=0, **kw):
    return dict(open_at=T+offset*STEP, close_at=T+(offset+1)*STEP,
                **(dict(open=100, high=104, low=96, close=101) | kw))


class ReferenceReplayTests(unittest.TestCase):
    def run_audit(self, bars, signals=None):
        return replay_reference({'bars_5min':bars, 'history':signals or [signal()]})

    def test_both_barriers_same_candle_are_ambiguous(self):
        r=self.run_audit([bar(high=111,low=94)])
        self.assertEqual(r['counts'],{'ambiguous':1});self.assertIsNone(r['target_rate'])

    def test_excludes_signal_candle_and_future_after_exit(self):
        r=self.run_audit([bar(-1,high=111,low=94),bar(high=111),bar(1,low=90)])
        self.assertEqual(r['counts'],{'target':1});self.assertEqual(r['hypothetical_gross_price_distance'],10)
        self.assertIsNone(r['net_pnl']);self.assertIsNone(r['max_drawdown'])

    def test_missing_bar_before_first_exit_is_incomplete(self):
        r=self.run_audit([bar(),bar(2,high=111)])
        self.assertEqual(r['counts'],{'incomplete':1})

    def test_no_post_signal_bars_is_not_a_loss(self):
        self.assertEqual(self.run_audit([bar(-1)])['counts'],{'incomplete':1})

    def test_stop_gap_uses_worse_open(self):
        r=self.run_audit([bar(open=93,high=94,low=92,close=93)])
        self.assertEqual(r['hypothetical_gross_price_distance'],-7)

    def test_short_geometry_and_deduplication(self):
        s=signal('SHORT');r=self.run_audit([bar(low=89,close=91)],[s,s])
        self.assertEqual(r['total'],1);self.assertEqual(r['counts'],{'target':1})

    def test_malformed_and_duplicate_candles_rejected(self):
        for bars in ([bar(low=102)],[bar(),bar()],[bar(high=float('nan'))]):
            with self.assertRaises(ValueError):self.run_audit(bars)

    def test_invalid_or_inverted_signal_is_excluded(self):
        self.assertEqual(self.run_audit([bar()],[signal()|{'invalidation':105}])['counts'],{'invalid':1})

    def test_rolling_window_does_not_erase_evidenced_outcome(self):
        old=self.run_audit([bar(high=111)])['rows']
        new=self.run_audit([bar(2)])
        self.assertTrue(new['partial_history'])
        self.assertEqual(new['bar_window_start'],T+2*STEP)
        self.assertEqual(merge_reference_rows(old,new['rows']),old)

    def test_conflicting_terminal_results_are_excluded(self):
        old=self.run_audit([bar(high=111)])['rows']
        new=self.run_audit([bar(low=94)])['rows']
        merged=merge_reference_rows(old,new)
        self.assertEqual(merged[0]['status'],'conflict')
        self.assertIsNone(merged[0]['gross_price_distance'])
        self.assertEqual(len(merged[0]['evidence']),2)

    def test_unresolved_missing_row_not_assumed_still_open(self):
        old=self.run_audit([bar()])['rows']
        self.assertEqual(merge_reference_rows(old,[])[0]['status'],'incomplete')
        with self.assertRaises(ValueError):merge_reference_rows([],old+old)

    def test_decimal_round_trip_is_not_conflicting_evidence(self):
        old=[dict(id='x',status='stop',gross_price_distance=-2.67234)]
        current=[dict(id='x',status='stop',gross_price_distance=-2.672339999999622)]
        self.assertEqual(merge_reference_rows(old,current),old)
        current[0]['gross_price_distance']=-2.67235
        self.assertEqual(merge_reference_rows(old,current)[0]['status'],'conflict')

if __name__=='__main__':unittest.main()
