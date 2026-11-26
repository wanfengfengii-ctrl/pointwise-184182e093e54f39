import unittest
from contextlib import closing

from panel.ledger import open_panel_ledger
from tests.support import Cluster, sample, scenario


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.cluster = self.enterContext(Cluster())
        self.release = self.cluster.seed()

    def test_mixed_rehearsal(self):
        another = self.cluster.control('POST', '/releases', {'release': 'edition-2'})
        target = self.cluster.prepare()
        other = self.cluster.prepare('evening', another['release'])
        cases = ([target[0]], [target[0], target[0]], [target[0], other[1]],
                 [target[0], dict(target[1], frame='R2')])
        for evidence in cases:
            with self.subTest(evidence=evidence):
                error = self.cluster.control('POST', '/shows/matinee/open', {'evidence': evidence}, expect=409)
                self.assertEqual(error['error'], 'not_ready')
                self.assertEqual(self.cluster.control('GET', '/shows/matinee')['phase'], 'preparing')
                self.assertEqual(self.cluster.log('left'), [])
                self.assertEqual(self.cluster.log('right'), [])
        self.assertEqual(self.cluster.control('POST', '/shows/matinee/open', {'evidence': target})['phase'], 'open')

    def test_wrong_package_and_trial_frame(self):
        self.cluster.control('POST', '/releases', {'release': 'edition-2'})
        self.cluster.control('POST', '/shows', {'show': 'matinee', 'release': 'edition-1'})
        self.cluster.control('POST', '/shows', {'show': 'matinee', 'release': 'edition-2'}, expect=409)
        self.cluster.panel('left', 'POST', '/shows/matinee/install', {'release': 'edition-2'}, expect=409)
        self.cluster.panel('left', 'GET', '/shows/matinee/current', expect=404)
        installed = self.cluster.panel('left', 'POST', '/shows/matinee/install', {'release': 'edition-1'})
        self.assertEqual(installed['frames'], ['L1', 'L2', 'L3'])
        self.cluster.panel('left', 'POST', '/shows/matinee/rehearse',
                           {'release': 'edition-1', 'frame': 'R1'}, expect=409)
        self.cluster.panel('left', 'GET', '/shows/matinee/rehearsal', expect=404)
        self.assertEqual(self.cluster.current('left')['sequence'], 0)

    def test_independent_cues(self):
        rehearsals = self.cluster.prepare()
        self.assertEqual(self.cluster.current('left')['frame'], None)
        self.assertEqual(self.cluster.current('right')['sequence'], 0)
        self.cluster.cue('left', 'left-1', 'L1', expect=409)
        cue = {'show': 'matinee', 'release': 'edition-1', 'side': 'left', 'request': 'left-1', 'frame': 'L1'}
        error = self.cluster.panel('left', 'POST', '/display', cue, expect=409)
        self.assertEqual(error['error'], 'cue_rejected')
        self.assertEqual(self.cluster.log('left'), [])
        self.cluster.control('POST', '/shows/matinee/open', {'evidence': rehearsals})
        first = self.cluster.cue('left', 'left-1', 'L1')
        second = self.cluster.cue('left', 'left-2', 'L2')
        right = self.cluster.cue('right', 'right-1', 'R1')
        self.assertEqual([(item['frame'], item['sequence']) for item in (first, second, right)],
                         [('L1', 1), ('L2', 2), ('R1', 1)])
        self.assertEqual(self.cluster.log('left'), [first, second])
        self.assertEqual(self.cluster.log('right'), [right])
        current = [self.cluster.current(side) for side in ('left', 'right')]
        self.assertEqual([(state['frame'], state['sequence']) for state in current], [('L2', 2), ('R1', 1)])
        raw = {entry['frame']: entry['measurements'] for entry in sample('frames')}
        seams = []
        for left in (first['frame'], second['frame']):
            values = {point: reading + raw[right['frame']][point] for point, reading in raw[left].items()}
            for point, total in values.items():
                low, high = sample('draft')['windows'][point]
                self.assertLessEqual(low, total)
                self.assertLessEqual(total, high)
            seams.append(values)
        self.assertEqual(seams, [{'中缝': 10, '上沿': 2}, {'中缝': 10, '上沿': 2}])

    def test_wrong_side_frame_on_direct_display(self):
        self.cluster.open()
        first = self.cluster.cue('left', 'left-1', 'L1')
        before = self.cluster.current('left')
        wrong = {'show': 'matinee', 'release': 'edition-1', 'side': 'left', 'frame': 'R1', 'request': 'next'}
        error = self.cluster.panel('left', 'POST', '/display', wrong, expect=409)
        self.assertEqual(error['error'], 'conflict')
        wrong_side = dict(wrong, side='right')
        self.cluster.panel('left', 'POST', '/display', wrong_side, expect=409)
        self.assertEqual(self.cluster.current('left'), before)
        self.assertEqual(self.cluster.log('left'), [first])
        self.assertEqual(self.cluster.cue('left', 'next', 'L2')['sequence'], 2)

    def test_lost_display_reply(self):
        self.cluster.open()
        cue = self.cluster.cue('left', 'lost', 'L1', hold=True)
        self.assertEqual(self.cluster.log('left'), [])
        self.cluster.drop_display_reply(cue)
        self.cluster.restart('left')
        receipt = self.cluster.panel('left', 'GET', '/shows/matinee/receipts/lost')
        expected = {'show': 'matinee', 'release': 'edition-1', 'side': 'left', 'request': 'lost',
                    'frame': 'L1', 'sequence': 1}
        self.assertEqual(receipt, expected)
        self.assertEqual(self.cluster.panel('left', 'POST', '/display', cue), expected)
        self.assertEqual(self.cluster.log('left'), [expected])
        current = self.cluster.current('left')
        expected_current = {'show': 'matinee', 'release': 'edition-1', 'side': 'left', 'frame': 'L1', 'sequence': 1}
        self.assertEqual(current, expected_current)
        scenario('v6', cue, expected, receipt, expected_current, current,
                 ['show/cues.py', 'panel/display.py', 'panel/ledger.py', 'panel/receipts.py'],
                 '空正式账只交付一次原提示，屏态和首执都必须为左侧第 1 次')
        later = self.cluster.cue('left', 'later', 'L2')
        self.assertEqual(self.cluster.panel('left', 'GET', '/shows/matinee/receipts/lost'), expected)
        self.assertEqual(self.cluster.panel('left', 'POST', '/display', cue), expected)
        error = self.cluster.panel('left', 'POST', '/display', dict(cue, frame='L2'), expect=409)
        self.assertEqual(error['error'], 'receipt_conflict')
        self.assertEqual(self.cluster.log('left'), [expected, later])
        self.assertEqual(self.cluster.current('left')['frame'], 'L2')

    def test_atomic_abort(self):
        self.cluster.open()
        previous = self.cluster.cue('left', 'before', 'L1')
        before = self.cluster.current('left')
        with closing(open_panel_ledger(self.cluster.panel_dbs['left'])) as db, db:
            db.execute("""CREATE TRIGGER deny_receipt BEFORE INSERT ON receipts
                WHEN NEW.request_id = 'interrupted' BEGIN
                SELECT RAISE(ABORT, 'receipt write interrupted'); END""")
        cue = self.cluster.cue('left', 'interrupted', 'L2', hold=True)
        error = self.cluster.panel('left', 'POST', '/display', cue, expect=503)
        self.assertEqual(error['error'], 'storage_error')
        self.cluster.restart('left')
        self.assertEqual(self.cluster.current('left'), before)
        self.assertEqual(self.cluster.log('left'), [previous])
        self.cluster.panel('left', 'GET', '/shows/matinee/receipts/interrupted', expect=404)
        with closing(open_panel_ledger(self.cluster.panel_dbs['left'])) as db, db:
            db.execute('DROP TRIGGER deny_receipt')
        self.cluster.drop_display_reply(cue)
        self.cluster.restart('left')
        receipt = self.cluster.panel('left', 'POST', '/display', cue)
        self.assertEqual((receipt['frame'], receipt['sequence']), ('L2', 2))
        self.assertEqual(self.cluster.log('left'), [previous, receipt])
        self.assertEqual((self.cluster.current('left')['frame'], self.cluster.current('left')['sequence']), ('L2', 2))
