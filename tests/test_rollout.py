import unittest

from show.transport import exchange
from tests.rollout_flow import (
    ROLLOUT_SAMPLE,
    accept,
    advance,
    advance_body,
    assert_current_compliant,
    drop_advance_reply,
    follow_path,
    get_step,
    open_case,
    raw_advance,
    seed_frames,
)
from tests.support import Cluster


class RolloutCase(unittest.TestCase):
    def setUp(self):
        self.cluster = self.enterContext(Cluster())
        seed_frames(self.cluster)

    def setup(self, case_id, show=None, ordinary=True):
        return open_case(self.cluster, case_id, show=show, ordinary=ordinary)

    def currents(self, setup):
        return {side: self.cluster.current(side, setup['show']) for side in ('left', 'right')}


class TestAcceptancePlanning(RolloutCase):
    def test_direct_cuts_rejected_and_screens_untouched(self):
        setup = self.setup('direct')
        before = self.currents(setup)
        error = accept(self.cluster, setup, expect=409)
        self.assertEqual(error['error'], 'no_safe_path')
        # 两种直切首步的中缝分别为 3+3=6 和 7+7=14，均不在 [9,11]。
        frames = {f['frame']: f['measurements'] for f in ROLLOUT_SAMPLE['frames']}
        self.assertEqual(frames['LX3']['中缝'] + frames['RX3']['中缝'], 6)
        self.assertEqual(frames['LX7']['中缝'] + frames['RX7']['中缝'], 14)
        self.assertEqual(self.currents(setup), before)
        # 拒收不产生受理单：同请求再次仍按无安全路径拒收。
        again = accept(self.cluster, setup, expect=409)
        self.assertEqual(again['error'], 'no_safe_path')
        # 拒收不改变普通切画资格。
        cue = self.cluster.cue('left', 'after-reject', setup['case']['start']['left'],
                               show=setup['show'])
        self.assertEqual(cue['sequence'], 2)

    def test_mixed_points_require_one_common_order(self):
        setup = self.setup('mixed_points')
        error = accept(self.cluster, setup, expect=409)
        self.assertEqual(error['error'], 'no_safe_path')

    def test_pending_when_a_side_has_no_official_display(self):
        setup = self.setup('long', show='pending-show', ordinary=False)
        # 只给左侧办一次普通实显，右侧仍为 null/0。
        self.cluster.control(
            'POST', f"/shows/{setup['show']}/cues/left",
            {'request': 'only-left', 'frame': setup['case']['start']['left']})
        error = accept(self.cluster, setup, expect=409)
        self.assertEqual(error['error'], 'pending')

    def test_invalid_lists_are_bad_requests(self):
        setup = self.setup('long', show='invalid-show')
        valid = setup['case']['request']
        bad_bodies = [
            {'request': 'bad-empty', 'left': [], 'right': valid['right']},
            {'request': 'bad-missing', 'left': ['LX6'], 'right': valid['right']},  # missing tail? no: unknown below
            {'request': 'bad-unknown', 'left': ['NOPE'], 'right': valid['right']},
            {'request': 'bad-wrong-side', 'left': ['RX4'], 'right': valid['right']},
            {'request': 'bad-type', 'left': 'LX6', 'right': valid['right']},
            {'request': 'bad id', 'left': ['LX6'], 'right': valid['right']},
        ]
        # 第二项其实清单非空且画面有效，删除它，保留真正非法的构造。
        bad_bodies.pop(1)
        for body in bad_bodies:
            with self.subTest(body=body.get('request')):
                self.cluster.control('POST', f"/shows/{setup['show']}/rollout", body, expect=400)

    def test_acceptance_requires_open_show(self):
        # 准备一个合法单幅发行并建展次，但不开场、不办正式实显。
        direct = ROLLOUT_SAMPLE['cases']['direct']
        self.cluster.control('PUT', '/drafts/dx', {
            'left': [direct['start']['left']], 'right': [direct['start']['right']],
            'windows': ROLLOUT_SAMPLE['windows']})
        self.cluster.control('POST', '/releases', {'release': 'edition-x', 'draft': 'dx'})
        self.cluster.control('POST', '/shows', {'show': 'bare2', 'release': 'edition-x'})
        body = {'request': 'r', 'left': ['LX3'], 'right': ['RX7']}
        error = self.cluster.control('POST', '/shows/bare2/rollout', body, expect=409)
        self.assertIn(error['error'], ('conflict', 'pending'))


class TestAcceptanceTicket(RolloutCase):
    def test_ticket_shape_and_idempotent_reissue_conflict(self):
        setup = self.setup('long')
        ticket = accept(self.cluster, setup)
        self.assertTrue(ticket['rollout'].startswith('rollout-'))
        self.assertEqual(ticket['show'], setup['show'])
        self.assertEqual(set(ticket['start']), {'left', 'right'})
        for side in ('left', 'right'):
            self.assertEqual(set(ticket['start'][side]), {'frame', 'sequence'})
            self.assertEqual(ticket['start'][side]['sequence'], 1)
        self.assertIn(ticket['next'], ticket['path'][:1])
        # 同请求号同片单重投返回原单。
        self.assertEqual(accept(self.cluster, setup), ticket)
        # 另号再受理 -> conflict。
        other = dict(setup['case']['request'], request='exchange-other-number')
        error = accept(self.cluster, setup, other, expect=409)
        self.assertEqual(error['error'], 'conflict')
        # 同号异参 -> conflict。
        changed = dict(setup['case']['request'], right=['RX4', 'RX5', 'RX6'])
        error = accept(self.cluster, setup, changed, expect=409)
        self.assertEqual(error['error'], 'conflict')

    def test_reissue_original_after_close(self):
        setup = self.setup('long', show='reissue-close')
        ticket = accept(self.cluster, setup)
        follow_path(self, self.cluster, setup, ticket)
        self.cluster.control('POST', f"/shows/{setup['show']}/close", {})
        self.assertEqual(accept(self.cluster, setup), ticket)


class TestLongWalk(RolloutCase):
    def test_follow_next_to_left3_right7(self):
        setup = self.setup('long')
        ticket = accept(self.cluster, setup)
        responses = follow_path(self, self.cluster, setup, ticket)
        self.assertEqual(len(responses), 8)
        currents = self.currents(setup)
        self.assertEqual((currents['left']['frame'], currents['right']['frame']), ('LX3', 'RX7'))
        self.assertEqual((currents['left']['sequence'], currents['right']['sequence']), (5, 5))
        self.assertIsNone(responses[-1]['next'])
        # 走完后可以关场，原片单收班不受换版步影响。
        closed = self.cluster.control('POST', f"/shows/{setup['show']}/close", {})
        self.assertEqual(closed['phase'], 'closed')
        report = self.cluster.control('POST', f"/shows/{setup['show']}/report", {})
        self.assertEqual(report['sides']['left']['missing'], [])
        self.assertEqual(report['sides']['right']['missing'], [])


class TestDeadEndScript(RolloutCase):
    def test_unique_order_withheld_receipt_reopen_and_query(self):
        setup = self.setup('dead_end')
        ticket = accept(self.cluster, setup)
        # 唯一完整次序 右1(RX3)、右2(RX5)、左1(LX5)。
        self.assertEqual(ticket['next'], {'side': 'right', 'step': 1})
        r1 = advance(self.cluster, 'right', ticket, 1)
        self.assertEqual(r1['frame'], 'RX3')
        left_cur, right_cur = assert_current_compliant(self, self.cluster, setup, ticket)
        self.assertEqual((left_cur['frame'], right_cur['frame']), ('LX6', 'RX3'))
        # 左5 仅持右3原执不能动：所需前序是右5。
        blocked = advance(self.cluster, 'left', ticket, 1, r1['receipt'], expect=409)
        self.assertEqual(blocked['error'], 'pending')
        unchanged = self.cluster.current('left', setup['show'])
        self.assertEqual((unchanged['frame'], unchanged['sequence']), ('LX6', 1))
        # 未实际办理：查步 404，且失败尝试不产生原步响应。
        get_step(self.cluster, 'left', ticket['rollout'], 1, expect=404)
        # 扣留右5回包，确认落库后重开该端，从原步查询取执。
        body = advance_body(ticket, 2, r1['receipt'])
        saved = drop_advance_reply(self.cluster, 'right', body, ticket['rollout'], 2)
        self.assertEqual(saved['frame'], 'RX5')
        self.cluster.restart('right')
        queried = get_step(self.cluster, 'right', ticket['rollout'], 2)
        self.assertEqual(queried, saved)
        # 重投已办步（可省略前序凭据）只领回原响应。
        self.assertEqual(advance(self.cluster, 'right', ticket, 2), saved)
        # 凭右5原执办完左5。
        done = advance(self.cluster, 'left', ticket, 1, saved['receipt'])
        self.assertEqual(done['frame'], 'LX5')
        self.assertIsNone(done['next'])
        finals = self.currents(setup)
        self.assertEqual((finals['left']['frame'], finals['right']['frame']), ('LX5', 'RX5'))
        for side in ('left', 'right'):
            get_step(self.cluster, side, ticket['rollout'],
                     len(ticket[side]))

    def test_query_unknown_step_is_404_and_does_not_display(self):
        setup = self.setup('dead_end', show='query-only')
        ticket = accept(self.cluster, setup)
        before = self.currents(setup)
        error = get_step(self.cluster, 'left', ticket['rollout'], 1, expect=404)
        self.assertEqual(error['error'], 'not_found')
        self.assertEqual(self.currents(setup), before)


class TestBranch(RolloutCase):
    def test_only_one_candidate_wins(self):
        setup = self.setup('branch')
        ticket = accept(self.cluster, setup)
        self.assertEqual(ticket['next'], {'side': 'left', 'step': 1})
        l1 = advance(self.cluster, 'left', ticket, 1)
        self.assertEqual(l1['frame'], 'LX6')
        # 同一份左6原执同时送给两个候选后继。
        candidates = [('left', 2), ('right', 1)]
        results = {}
        for side, step in candidates:
            results[(side, step)] = raw_advance(
                self.cluster, side, advance_body(ticket, step, l1['receipt']))
        winners = [key for key, (status, _) in results.items() if status == 200]
        losers = [key for key, (status, _) in results.items() if status == 409]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(len(losers), 1, results)
        _, loser_body = results[losers[0]]
        self.assertEqual(loser_body['error'], 'pending')
        # 落选候选没有首次出屏：查该步为 404。
        loser_side, loser_step = losers[0]
        get_step(self.cluster, loser_side, ticket['rollout'], loser_step, expect=404)
        # 重复领取/重投左6原步不改变指定。
        self.assertEqual(advance(self.cluster, 'left', ticket, 1), l1)
        self.assertEqual(get_step(self.cluster, 'left', ticket['rollout'], 1), l1)
        # 沿指定 next 可走完全程。
        responses = follow_path(self, self.cluster, setup, ticket)
        self.assertIsNone(responses[-1]['next'])


class TestOrdinaryCueInterlock(RolloutCase):
    def test_held_ordinary_prompt_rejected_after_acceptance(self):
        setup = self.setup('long', show='interlock')
        start_left = setup['case']['start']['left']
        held = self.cluster.cue('left', 'held-prompt', start_left, show=setup['show'], hold=True)
        ticket = accept(self.cluster, setup)
        rejected = self.cluster.panel('left', 'POST', '/display', held, expect=409)
        self.assertEqual(rejected['error'], 'cue_rejected')
        # prepare 不是切画：受理后仍可准备新提示，但送达首次实显仍被展务锁拒绝。
        prepared = self.cluster.cue('right', 'prepared-only', setup['case']['start']['right'],
                                    show=setup['show'], hold=True)
        delivered = self.cluster.panel('right', 'POST', '/display', prepared, expect=409)
        self.assertEqual(delivered['error'], 'cue_rejected')
        # 新普通提示在展务入口（准备并派发）也被拦住。
        self.cluster.cue('right', 'brand-new', setup['case']['start']['right'],
                         show=setup['show'], expect=409)
        # 受理前已办的普通首执仍可直投复领。
        original_body = {'show': setup['show'], 'release': setup['release'], 'side': 'left',
                         'request': 'ordinary-long-left', 'frame': start_left}
        replayed = self.cluster.panel('left', 'POST', '/display', original_body)
        self.assertEqual(replayed['sequence'], 1)
        self.assertEqual(replayed['frame'], start_left)
        # 屏幕仍是受理起点。
        current = self.cluster.current('left', setup['show'])
        self.assertEqual((current['frame'], current['sequence']), (start_left, 1))
        self.assertTrue(ticket['rollout'])


class TestOfflineAndIdentity(RolloutCase):
    def test_control_stopped_cross_side_continuation_and_bad_binding(self):
        setup = self.setup('long', show='offline')
        ticket = accept(self.cluster, setup)
        self.cluster.stop('control')
        try:
            responses = follow_path(self, self.cluster, setup, ticket)
            self.assertEqual(len(responses), 8)
            # 错单凭据：另造一个展次的受理单与回执。
        finally:
            self.cluster.start('control')
        # 关场前未走完的另一展次应 pending。
        other = self.setup('dead_end', show='offline-other')
        other_ticket = accept(self.cluster, other)
        advance(self.cluster, 'right', other_ticket, 1)
        pending = self.cluster.control('POST', f"/shows/{other['show']}/close", {}, expect=409)
        self.assertEqual(pending['error'], 'pending')
        # 错展实执：long 的回执不能用于 dead_end 受理单。
        long_receipt = responses[0]['receipt']
        bad = advance(self.cluster, 'left', other_ticket, 1, long_receipt, expect=409)
        self.assertEqual(bad['error'], 'conflict')

    def test_tampered_and_forged_ticket_rejected(self):
        setup = self.setup('long', show='forgery')
        ticket = accept(self.cluster, setup)
        tampered = dict(ticket)
        tampered['left'] = ['LX3']
        error = advance(self.cluster, 'left', tampered, 1, ..., expect=409)
        self.assertEqual(error['error'], 'conflict')
        forged = {'rollout': ticket['rollout'], 'show': setup['show'], 'step': 1, 'sig': '00'}
        error = self.cluster.panel('left', 'POST', '/advance',
                                   {'ticket': forged, 'step': 1}, expect=409)
        self.assertEqual(error['error'], 'conflict')

    def test_ordinary_receipt_cannot_be_rollout_evidence(self):
        setup = self.setup('dead_end', show='ordinary-evidence')
        ticket = accept(self.cluster, setup)
        ordinary = setup['ordinary']['left']
        error = advance(self.cluster, 'left', ticket, 1, ordinary, expect=409)
        self.assertEqual(error['error'], 'conflict')
        # 缺少前序凭据时保持原屏。
        pending = self.cluster.panel('left', 'POST', '/advance',
                                     {'ticket': ticket, 'step': 1}, expect=409)
        self.assertEqual(pending['error'], 'pending')
        current = self.cluster.current('left', setup['show'])
        self.assertEqual((current['frame'], current['sequence']), ('LX6', 1))


if __name__ == '__main__':
    unittest.main()
