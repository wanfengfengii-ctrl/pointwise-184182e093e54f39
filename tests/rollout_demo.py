"""在线换版实战演示与清单核验。

demo() 全部通过真实环回 HTTP 完成：
1. 直切拒收（左先得 6、右先得 14，受理 409 no_safe_path，屏态不动）；
2. dead_end 扣留右5回包、重开投影端、查步取执，并在展务台离线时跨侧续办；
3. long 跟随 next 走到左3右7 的最终屏态，恢复展务后关场、原片单收班。

`python3 -B -m tests.rollout_demo --verify` 以断言方式跑同一流程，不符即非零退出。
"""
import sys

from tests.rollout_flow import (
    ROLLOUT_SAMPLE,
    accept,
    advance,
    advance_body,
    drop_advance_reply,
    get_step,
    open_case,
    seed_frames,
)
from tests.support import Cluster, scenario


def _seam(frame_id, side):
    return ROLLOUT_SAMPLE and next(
        f['measurements']['中缝'] for f in ROLLOUT_SAMPLE['frames'] if f['frame'] == frame_id)


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


def _line(text):
    print(text, flush=True)


def _current_pair(cluster, show):
    left = cluster.current('left', show)
    right = cluster.current('right', show)
    return left, right


def demo_direct(cluster):
    setup = open_case(cluster, 'direct', show='demo-direct')
    before = _current_pair(cluster, setup['show'])
    left_direct = _seam('LX3', 'left') + _seam('RX3', 'right')
    right_direct = _seam('LX7', 'left') + _seam('RX7', 'right')
    _line(f'[direct] 直切首步中缝：左切LX3 -> {left_direct}（许可9-11），右切RX7 -> {right_direct}（许可9-11）')
    error = accept(cluster, setup, expect=409)
    _check(error['error'] == 'no_safe_path', error)
    _line('[direct] 直切拒绝：POST /shows/demo-direct/rollout -> HTTP 409 error=no_safe_path'
          '（两侧直切均越界，无合规完整交错，不出单）')
    after = _current_pair(cluster, setup['show'])
    _check(before == after, 'rejected direct request moved the screen')
    _line(f'[direct] 起始屏态与端内序号保持不动：{[(c["frame"], c["sequence"]) for c in after]}')
    scenario('rollout-direct', setup['case']['request'],
             {'http': 409, 'error': 'no_safe_path', 'left_first_seam': left_direct,
              'right_first_seam': right_direct},
             {'http': 409, 'error': error['error'], 'left_first_seam': left_direct,
              'right_first_seam': right_direct},
             {'before': before}, {'after': after},
             ['show/rollout.py', 'panel/advance.py'],
             '两种直切首步分别得6和14，均越[9,11]窗；无完整交错即 no_safe_path，不出单不改屏')


def demo_dead_end_offline(cluster):
    setup = open_case(cluster, 'dead_end', show='demo-dead-end')
    ticket = accept(cluster, setup)
    _check(ticket['next'] == {'side': 'right', 'step': 1}, ticket['next'])
    _line(f"[dead_end] 受理单 {ticket['rollout']} 指定唯一走法 右3(RX3) -> 右5(RX5) -> 左5(LX5)")

    r3 = advance(cluster, 'right', ticket, 1)
    _line('[dead_end] 右3 已显，中缝 6+3=9')

    blocked = advance(cluster, 'left', ticket, 1, r3['receipt'], expect=409)
    _check(blocked['error'] == 'pending', blocked)
    _line('[dead_end] 左5 仅持右3原执 -> HTTP 409 pending（所需前序是右5），左屏保持 LX6/seq1')

    # 扣留右5回包：请求已提交且事务持久，但响应丢失。
    body = advance_body(ticket, 2, r3['receipt'])
    saved = drop_advance_reply(cluster, 'right', body, ticket['rollout'], 2)
    _line('[dead_end] 右5 已提交但回包被扣留；轮询查步确认该步已持久生效')

    # 展务台离线（停止监听），随后重开右侧投影端并从原步查询取执。
    cluster.stop('control')
    try:
        cluster.restart('right')
        queried = get_step(cluster, 'right', ticket['rollout'], 2)
        _check(queried == saved, 'step query did not return the original response')
        _line('[offline] 展务台已停止；重开右端后 GET .../steps/%s/2 取回右5原执（查步不出屏）'
              % ticket['rollout'])

        # 展务台仍离线，左端凭右5真实回执跨侧续办。
        done = advance(cluster, 'left', ticket, 1, queried['receipt'])
        _check(done['frame'] == 'LX5' and done['next'] is None, done)
        left, right = _current_pair(cluster, setup['show'])
        _check((left['frame'], right['frame']) == ('LX5', 'RX5'), (left, right))
        _line(f'[offline] 断台跨侧续办成功，最终中缝 5+5=10，屏态：左{left["frame"]}/seq{left["sequence"]} '
              f'右{right["frame"]}/seq{right["sequence"]}')

        # 失答重投已办步只领回原响应。
        replay = advance(cluster, 'right', ticket, 2)
        _check(replay == saved, 'replay did not return the original step response')
    finally:
        cluster.start('control')
    _line('[offline] 展务台恢复')

    closed = cluster.control('POST', f"/shows/{setup['show']}/close", {})
    _check(closed['phase'] == 'closed', closed)
    report = cluster.control('POST', f"/shows/{setup['show']}/report", {})
    _check(all(not side['missing'] for side in report['sides'].values()), report)
    _line('[dead_end] 换版走完后关场成功，原片单收班报告两侧均无漏播')
    scenario('rollout-dead-end-offline',
             {'order': ['right#1 RX3', 'right#2 RX5', 'left#1 LX5'], 'withheld': 'right#2'},
             {'final': {'left': left['frame'], 'right': right['frame']},
              'seams': [9, 11, 10], 'control': 'stopped-during-cross-side-finish',
              'step_query_recovered_receipt': queried == saved, 'close': closed['phase']},
             {'final': {'left': 'LX5', 'right': 'RX5'}, 'seams': [9, 11, 10],
              'control': 'stopped-during-cross-side-finish',
              'step_query_recovered_receipt': True, 'close': 'closed'},
             {'report_missing': {k: v['missing'] for k, v in report['sides'].items()}},
             {'report_missing': {'left': [], 'right': []}},
             ['panel/advance.py', 'show/rollout.py', 'show/close.py'],
             '扣留右5回包后重开端查步取执，展务台离线时左端凭右5回执跨侧办完；左5仅持右3执为 pending')


def demo_long_final(cluster):
    setup = open_case(cluster, 'long', show='demo-long')
    ticket = accept(cluster, setup)
    _line(f"[long] 受理单 next={ticket['next']}，清单 左{ticket['left']} 右{ticket['right']}")

    nxt = ticket['next']
    previous = None
    steps = 0
    cluster.stop('control')
    try:
        while nxt is not None:
            side, step = nxt['side'], nxt['step']
            receipt_arg = previous if previous is not None else ...
            response = advance(cluster, side, ticket, step, receipt_arg)
            previous = response['receipt']
            nxt = response['next']
            steps += 1
            left, right = _current_pair(cluster, setup['show'])
            total = (_seam(left['frame'], 'left') + _seam(right['frame'], 'right'))
            _check(9 <= total <= 11, f'seam {total} out of window at step {steps}')
            _line(f'[long] 第{steps}步 {side}#{step} -> {response["frame"]} '
                  f'(seq{response["sequence"]})，当前组合 {left["frame"]}+{right["frame"]} 中缝={total}')
    finally:
        cluster.start('control')

    left, right = _current_pair(cluster, setup['show'])
    _check((left['frame'], right['frame']) == ('LX3', 'RX7'), (left, right))
    _check((left['sequence'], right['sequence']) == (5, 5), (left, right))
    _line(f'[long] 断台全程续办 {steps} 步后到达目标：左3 {left["frame"]} / 右7 {right["frame"]}，'
          f'序次 {left["sequence"]}/{right["sequence"]}')
    closed = cluster.control('POST', f"/shows/{setup['show']}/close", {})
    _check(closed['phase'] == 'closed', closed)
    report = cluster.control('POST', f"/shows/{setup['show']}/report", {})
    _line('[long] 关场与原片单收班照常：'
          + ', '.join(f'{side}漏播{data["missing"]}' for side, data in report['sides'].items()))
    scenario('rollout-long-final', setup['case']['request'],
             {'left': 'LX3', 'right': 'RX7', 'left_sequence': 5, 'right_sequence': 5,
              'steps': steps, 'control_stopped': True},
             {'left': left['frame'], 'right': right['frame'],
              'left_sequence': left['sequence'], 'right_sequence': right['sequence'],
              'steps': steps, 'control_stopped': True},
             {'missing': {k: v['missing'] for k, v in report['sides'].items()}},
             {'missing': {'left': [], 'right': []}},
             ['show/rollout.py', 'panel/advance.py'],
             '断开展务台后凭受理单与逐幅回执跟随 next 续办，八步中缝均在[9,11]，到达左3右7')


def demo_cue_interlock(cluster):
    # 严格按 ROLLOUT：先扣留普通提示，再受理，再直投旧提示。
    setup = open_case(cluster, 'long', show='demo-interlock')
    held = cluster.cue('left', 'held-old-cue', setup['case']['start']['left'],
                       show=setup['show'], hold=True)
    accept(cluster, setup)
    rejected = cluster.panel('left', 'POST', '/display', held, expect=409)
    _check(rejected['error'] == 'cue_rejected', rejected)
    _line('[interlock] 先扣留普通提示再受理；受理后直投旧提示 -> HTTP 409 cue_rejected，首次实显被拦')


def showcase():
    with Cluster() as cluster:
        seed_frames(cluster)
        demo_direct(cluster)
    with Cluster() as cluster:
        seed_frames(cluster)
        demo_cue_interlock(cluster)
    with Cluster() as cluster:
        seed_frames(cluster)
        demo_dead_end_offline(cluster)
    with Cluster() as cluster:
        seed_frames(cluster)
        demo_long_final(cluster)
    _line('ROLLOUT_DEMO result=PASS 直切拒收、扣留回执后的断台续办、左3右7最终屏态均经真实 HTTP 验证')


def verify():
    showcase()
    _line('ROLLOUT_VERIFY total=rollout-http result=PASS')
    return 0


def demo():
    # 清单 demo 聚焦在线换版；原 V1 演示仍可用 `python3 -B -m tests.support` 运行
    # （该入口在原报告之后也会串联本演练）。
    showcase()


if __name__ == '__main__':
    if '--verify' in sys.argv[1:]:
        raise SystemExit(verify())
    demo()
