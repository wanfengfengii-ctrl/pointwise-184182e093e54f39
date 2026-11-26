import json
import unittest
from contextlib import closing

from show.http import initialize_control
from show.store import open_control_store
from tests.support import Cluster, sample, scenario


class EditionTests(unittest.TestCase):
    def setUp(self):
        self.cluster = self.enterContext(Cluster())

    def counts(self):
        with closing(open_control_store(self.cluster.control_db)) as db:
            return tuple(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                         for table in ('releases', 'drafts', 'settings'))

    def test_fixed_edition(self):
        release = self.cluster.seed()
        expected = {
            '中缝': {'left': 7, 'right': 3, 'total': 10, 'low': 9, 'high': 11},
            '上沿': {'left': 0, 'right': 2, 'total': 2, 'low': 2, 'high': 2},
        }
        self.assertEqual(release['meters'], expected)
        self.assertEqual(release['playlists'], {'left': ['L1', 'L2', 'L3'], 'right': ['R1', 'R2']})
        original = self.cluster.control('GET', '/releases/edition-1')
        self.assertEqual(original, release)
        left = self.cluster.control('GET', '/releases/edition-1/panels/left')
        expected_left = {'release': 'edition-1', 'side': 'left', 'frames': ['L1', 'L2', 'L3'],
                         'meters': {'中缝': 7, '上沿': 0}}
        self.assertEqual(left, expected_left)
        scenario('v1', {'frames': sample('frames'), 'draft': sample('draft')}, expected, release['meters'],
                 expected_left, left, ['art/catalog.py', 'edition/proof.py', 'edition/seam.py', 'edition/packages.py'],
                 '按点名手算 7+3=10、0+2=2，并检查发行方交给左端的原有序片单')

    def test_frame_replay_and_invalid_meter_values(self):
        frame = sample('frames')[0]
        self.assertEqual(self.cluster.control('POST', '/frames', frame), frame)
        replay = dict(frame, measurements=[['上沿', 0], ['中缝', 7]])
        self.assertEqual(self.cluster.control('POST', '/frames', replay), frame)
        error = self.cluster.control('POST', '/frames', dict(frame, content='新正文'), expect=409)
        self.assertEqual(error['error'], 'frame_conflict')
        self.assertEqual(self.cluster.control('GET', '/frames/L1'), frame)
        for readings in ([['中缝', 7], ['中缝', 8]], {'中缝': None}, {'中缝': False},
                         {'中缝': -1}, {'中缝': 7.0}):
            with self.subTest(readings=readings):
                error = self.cluster.control('POST', '/frames', dict(frame, frame='bad', measurements=readings),
                                             expect=400)
                self.assertEqual((error['error'], error['point']), ('invalid_measurements', '中缝'))
        self.cluster.control('GET', '/frames/bad', expect=404)

    def test_release_snapshot_and_closed_endpoints(self):
        release = self.cluster.seed()
        draft = sample('draft')
        draft['left'] = ['L3', 'L2', 'L1']
        draft['windows'] = {'中缝': [10, 11], '上沿': [1, 2]}
        self.cluster.control('PUT', '/drafts/default', draft)
        revised = self.cluster.control('POST', '/releases', {'release': 'revised'})
        self.assertEqual(revised['playlists']['left'], ['L3', 'L2', 'L1'])
        self.assertEqual(revised['meters']['中缝']['total'], revised['meters']['中缝']['low'])
        self.assertEqual(revised['meters']['上沿']['total'], revised['meters']['上沿']['high'])
        self.assertEqual(self.cluster.control('POST', '/releases', {'release': 'revised'}), revised)
        self.cluster.control('POST', '/releases', {'release': 'edition-1'}, expect=409)
        self.assertEqual(self.cluster.control('GET', '/releases/edition-1'), release)
        self.assertEqual(self.counts()[0], 2)

    def test_wrong_side_unknown_and_empty_sequences(self):
        self.cluster.seed()
        for left in (['R1'], ['unknown'], []):
            with self.subTest(left=left):
                self.cluster.control('PUT', '/drafts/bad', dict(sample('draft'), left=left))
                error = self.cluster.control('POST', '/releases', {'release': 'blocked', 'draft': 'bad'}, expect=400)
                self.assertEqual((error['error'], error['side']), ('invalid_sequence', 'left'))
        self.cluster.control('GET', '/releases/blocked', expect=404)
        self.assertEqual(self.counts()[0], 1)

    def test_side_vector_mismatch(self):
        release = self.cluster.seed()
        changed = {'frame': 'vary', 'side': 'left', 'content': '另一个分镜', 'measurements': {'中缝': 8, '上沿': 0}}
        self.cluster.control('POST', '/frames', changed)
        draft = dict(sample('draft'), left=['L1', 'vary'])
        self.cluster.control('PUT', '/drafts/vary', draft)
        error = self.cluster.control('POST', '/releases', {'release': 'new', 'draft': 'vary'}, expect=400)
        self.assertEqual((error['error'], error['point'], error['frame']), ('side_vector_mismatch', '中缝', 'vary'))
        self.assertEqual(self.cluster.control('GET', '/releases/edition-1'), release)
        self.cluster.control('GET', '/releases/new', expect=404)
        self.assertEqual(self.counts()[0], 1)

    def test_seam_over_limit(self):
        self.cluster.seed()
        cases = (
            ('center-high', {'上沿': 2, '中缝': 5}, sample('draft')['windows'], '中缝', 12, [9, 11]),
            ('later-high', {'a-first': 3, '中缝': 3}, {'a-first': [2, 2], '中缝': [9, 9]}, 'a-first', 3, [2, 2]),
            ('center-low', {'上沿': 2, '中缝': 1}, sample('draft')['windows'], '中缝', 8, [9, 11]),
        )
        for name, meters, windows, point, total, bounds in cases:
            with self.subTest(name=name):
                left = ['L1', 'L2', 'L3']
                right = []
                if name == 'later-high':
                    left = ['ordered-left']
                    self.cluster.control('POST', '/frames', {'frame': left[0], 'side': 'left', 'content': '校样',
                                                             'measurements': {'a-first': 0, '中缝': 7}})
                    # 中缝 sorts after the ASCII point. Here only the later point fails.
                    meters = {'a-first': 2, '中缝': 5}
                    windows = {'a-first': [2, 2], '中缝': [9, 11]}
                    point, total, bounds = '中缝', 12, [9, 11]
                for sequence in (1, 2):
                    frame = f'{name}-{sequence}'
                    right.append(frame)
                    self.cluster.control('POST', '/frames', {'frame': frame, 'side': 'right',
                                                             'content': '越窗对照', 'measurements': meters})
                self.cluster.control('PUT', f'/drafts/{name}', {'left': left, 'right': right, 'windows': windows})
                error = self.cluster.control('POST', '/releases', {'release': name, 'draft': name}, expect=400)
                self.assertEqual((error['error'], error['point'], error['total'], error['low'], error['high']),
                                 ('seam_out_of_range', point, total, *bounds))
                self.cluster.control('GET', f'/releases/{name}', expect=404)
        self.assertEqual(self.counts()[0], 1)

    def test_missing_meter(self):
        original = self.cluster.seed()
        frame = {'frame': 'incomplete', 'side': 'left', 'content': '缺测分镜', 'measurements': {'中缝': 7}}
        self.cluster.control('POST', '/frames', frame)
        draft = dict(sample('draft'), left=['L1', 'incomplete'])
        self.cluster.control('PUT', '/drafts/incomplete', draft)
        error = self.cluster.control('POST', '/releases', {'release': 'incomplete', 'draft': 'incomplete'}, expect=400)
        self.assertEqual((error['error'], error['point'], error['frame']), ('missing_meter', '上沿', 'incomplete'))
        self.assertEqual(self.cluster.control('GET', '/releases/edition-1'), original)
        self.cluster.control('GET', '/releases/incomplete', expect=404)
        self.assertEqual(self.counts()[0], 1)

    def test_bad_window_and_extra_meter(self):
        self.cluster.seed()
        draft = sample('draft')
        error = self.cluster.control('PUT', '/drafts/bad-window', dict(draft, windows={'中缝': [11, 9]}), expect=400)
        self.assertEqual((error['error'], error['point']), ('invalid_window', '中缝'))
        self.cluster.control('PUT', '/drafts/empty-window', dict(draft, windows={}))
        error = self.cluster.control('POST', '/releases', {'release': 'empty-window', 'draft': 'empty-window'}, expect=400)
        self.assertEqual(error['error'], 'invalid_window')
        frame = {'frame': 'extra', 'side': 'left', 'content': '额外测点', 'measurements': {'中缝': 7, '上沿': 0, '额外': 0}}
        self.cluster.control('POST', '/frames', frame)
        self.cluster.control('PUT', '/drafts/extra', dict(draft, left=['extra']))
        error = self.cluster.control('POST', '/releases', {'release': 'extra', 'draft': 'extra'}, expect=400)
        self.assertEqual((error['error'], error['point']), ('unexpected_meter', '额外'))
        self.assertEqual(self.counts()[0], 1)

    def test_repeat_init(self):
        old = self.cluster.seed()
        changed = dict(sample('draft'), left=['L2', 'L1'], windows={'中缝': [10, 10], '上沿': [2, 2]})
        saved = self.cluster.control('PUT', '/drafts/default', changed)
        self.cluster.control('PUT', '/settings/site_name', {'value': '河畔巡展'})
        with closing(open_control_store(self.cluster.control_db)) as db, db:
            db.execute('DELETE FROM settings WHERE name = ?', ('default_draft',))
        self.assertEqual(self.counts(), (1, 1, 1))
        initialize_control(self.cluster.control_db)
        self.cluster.restart('control')
        self.assertEqual(self.cluster.control('GET', '/drafts/default'), saved)
        self.assertEqual(self.cluster.control('GET', '/settings'), {'site_name': '河畔巡展', 'default_draft': 'default'})
        self.assertEqual(self.counts(), (1, 1, 2))
        new = self.cluster.control('POST', '/releases', {'release': 'after-init'})
        self.assertEqual(new['playlists']['left'], ['L2', 'L1'])
        self.assertEqual(new['meters']['中缝'], {'left': 7, 'right': 3, 'total': 10, 'low': 10, 'high': 10})
        self.assertEqual(self.cluster.control('GET', '/releases/edition-1'), old)
