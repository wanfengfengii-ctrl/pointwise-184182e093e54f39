import unittest
from contextlib import closing

from show.store import open_control_store
from tests.support import Cluster, sample, scenario


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.cluster = self.enterContext(Cluster())
        self.cluster.seed()
        self.cluster.open()

    def report_count(self):
        with closing(open_control_store(self.cluster.control_db)) as db:
            return db.execute('SELECT count(*) FROM reports').fetchone()[0]

    def test_missing_frame(self):
        operations = sample('operations')
        for cue in operations['cues']:
            self.cluster.cue(**cue)
        self.cluster.close_show()
        result = self.cluster.report()
        expected = sample('expected-report')
        self.assertEqual(result, expected)
        trial = self.cluster.panel('left', 'GET', '/shows/matinee/rehearsal')
        self.assertEqual(trial['frame'], 'L3')
        downstream = self.cluster.control('GET', '/reports/report-matinee')
        self.assertEqual(downstream, expected)
        self.assertEqual(self.report_count(), 1)
        scenario('v7', operations, expected, result, expected, downstream,
                 ['panel/rehearse.py', 'panel/receipts.py', 'review/intake.py', 'review/coverage.py', 'review/report.py'],
                 '用实际交付顺序手列四条首执。从左右固定片单分别去掉正式播过的画面，左 L3 保留')

    def test_actual_order_and_readable_empty_side(self):
        first = self.cluster.cue('left', 'out-of-order-1', 'L2')
        second = self.cluster.cue('left', 'out-of-order-2', 'L1')
        third = self.cluster.cue('left', 'out-of-order-3', 'L2')
        self.assertEqual([first['sequence'], second['sequence'], third['sequence']], [1, 2, 3])
        self.cluster.close_show()
        right = self.cluster.panel('right', 'GET', '/shows/matinee/log')
        self.assertEqual(right, {'show': 'matinee', 'release': 'edition-1', 'side': 'right', 'receipts': []})
        result = self.cluster.report()
        self.assertEqual(result['sides']['left'], {'played': [first, second, third], 'missing': ['L3']})
        self.assertEqual(result['sides']['right'], {'played': [], 'missing': ['R1', 'R2']})

    def test_unreadable_side(self):
        premature = self.cluster.report(expect=409)
        self.assertEqual(premature['error'], 'pending')
        self.assertEqual(self.report_count(), 0)
        left = self.cluster.cue('left', 'off-air-left', 'L1')
        right = self.cluster.cue('right', 'off-air-right', 'R2')
        self.cluster.close_show()
        self.cluster.stop('right')
        pending = self.cluster.report(expect=409)
        self.assertEqual(pending['error'], 'pending')
        self.assertIn('right', pending['message'])
        self.assertEqual(self.report_count(), 0)
        self.cluster.control('GET', '/reports/report-matinee', expect=404)
        self.assertEqual(self.cluster.log('left'), [left])
        self.cluster.start('right')
        result = self.cluster.report()
        self.assertEqual(result['sides']['right'], {'played': [right], 'missing': ['R1']})
        self.assertEqual(result['sides']['left'], {'played': [left], 'missing': ['L2', 'L3']})
        self.assertEqual(self.report_count(), 1)

    def test_late_cue_after_close(self):
        original_cue = self.cluster.cue('left', 'original', 'L1', hold=True)
        original = self.cluster.panel('left', 'POST', '/display', original_cue)
        held = self.cluster.cue('left', 'held', 'L2', hold=True)
        self.assertEqual(self.cluster.log('left'), [original])
        self.assertEqual(self.cluster.close_show()['phase'], 'closed')
        report = self.cluster.report()
        self.assertEqual(report['sides']['left'], {'played': [original], 'missing': ['L2', 'L3']})
        error = self.cluster.panel('left', 'POST', '/display', held, expect=409)
        self.assertEqual(error['error'], 'cue_rejected')
        self.cluster.cue('left', 'late', 'L2', expect=409)
        self.cluster.panel('left', 'GET', '/shows/matinee/receipts/held', expect=404)
        self.cluster.restart('left')
        self.cluster.restart('control')
        self.assertEqual(self.cluster.control('GET', '/shows/matinee')['phase'], 'closed')
        self.assertEqual(self.cluster.panel('left', 'POST', '/display', original_cue), original)
        self.assertEqual(self.cluster.panel('left', 'GET', '/shows/matinee/receipts/original'), original)
        self.assertEqual(self.cluster.log('left'), [original])
        self.assertEqual(self.cluster.current('left')['frame'], 'L1')
        self.assertEqual(self.cluster.report(), report)
        self.assertEqual(self.cluster.control('GET', '/reports/report-matinee'), report)
        self.assertEqual(self.report_count(), 1)
