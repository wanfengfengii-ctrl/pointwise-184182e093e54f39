"""在线换版演练/核验共用的 HTTP 流程辅助。"""
import json
from http.client import HTTPConnection
from time import monotonic, sleep

from show.transport import exchange
from tests.support import sample


ROLLOUT_SAMPLE = sample('rollout')


def case_data(case_id):
    return ROLLOUT_SAMPLE['cases'][case_id]


def seed_frames(cluster):
    for frame in ROLLOUT_SAMPLE['frames']:
        cluster.control('POST', '/frames', frame)


def open_case(cluster, case_id, show=None, ordinary=True):
    """按 ROLLOUT 演练约定建单幅固定发行、装版试映、开场并各办一次普通实显。"""
    case = case_data(case_id)
    show = show or f'rollout-{case_id.replace("_", "-")}'
    slug = case_id.replace('_', '-')
    draft = f'draft-{slug}'
    release = f'edition-{slug}'
    windows = case.get('windows', ROLLOUT_SAMPLE['windows'])
    start = case['start']
    cluster.control('PUT', f'/drafts/{draft}',
                    {'left': [start['left']], 'right': [start['right']], 'windows': windows})
    cluster.control('POST', '/releases', {'release': release, 'draft': draft})
    cluster.control('POST', '/shows', {'show': show, 'release': release})
    evidence = []
    for side in ('left', 'right'):
        cluster.panel(side, 'POST', f'/shows/{show}/install', {'release': release})
        evidence.append(cluster.panel(
            side, 'POST', f'/shows/{show}/rehearse',
            {'release': release, 'frame': start[side]}))
    cluster.control('POST', f'/shows/{show}/open', {'evidence': evidence})
    ordinary_receipts = {}
    if ordinary:
        for side in ('left', 'right'):
            ordinary_receipts[side] = cluster.control(
                'POST', f'/shows/{show}/cues/{side}',
                {'request': f'ordinary-{case_id}-{side}', 'frame': start[side]})
    return {'show': show, 'release': release, 'case': case, 'windows': windows,
            'ordinary': ordinary_receipts}


def accept(cluster, setup, body=None, *, expect=200):
    payload = body if body is not None else setup['case']['request']
    return cluster.control('POST', f"/shows/{setup['show']}/rollout", payload, expect=expect)


def advance_body(ticket, step, receipt=...):
    body = {'ticket': ticket, 'step': step}
    if receipt is not ...:
        body['receipt'] = receipt
    return body


def advance(cluster, side, ticket, step, receipt=..., *, expect=200):
    return cluster.panel(side, 'POST', '/advance',
                         advance_body(ticket, step, receipt), expect=expect)


def raw_advance(cluster, side, body):
    return exchange(cluster.ports[side], 'POST', f'/panels/{side}/advance', body)


def get_step(cluster, side, rollout_id, step, *, expect=200):
    return cluster.panel(side, 'GET', f'/steps/{rollout_id}/{step}', expect=expect)


def drop_advance_reply(cluster, side, body, rollout_id, step, timeout=4.0):
    """提交换版步但不读回包；轮询原步查单确认事务已持久，再关闭裸连接。"""
    connection = HTTPConnection('127.0.0.1', cluster.ports[side], timeout=4)
    path = f'/panels/{side}/advance'
    try:
        connection.request('POST', path, json.dumps(body, ensure_ascii=False).encode('utf-8'),
                           {'Content-Type': 'application/json'})
        deadline = monotonic() + timeout
        query = f'/panels/{side}/steps/{rollout_id}/{step}'
        while monotonic() < deadline:
            status, result = exchange(cluster.ports[side], 'GET', query)
            if status == 200:
                return result
            if status != 404:
                raise AssertionError(f'step probe returned {status}: {result}')
            sleep(0.01)
        raise AssertionError('advance did not commit before the drop-reply deadline')
    finally:
        connection.close()


def frame_measurements():
    return {frame['frame']: frame['measurements'] for frame in ROLLOUT_SAMPLE['frames']}


def assert_current_compliant(testcase, cluster, setup, ticket):
    """读两端 current，按发行许可窗独立核算当前中缝等合计。"""
    raw = frame_measurements()
    left = cluster.current('left', setup['show'])
    right = cluster.current('right', setup['show'])
    windows = setup['windows']
    for point, bounds in windows.items():
        total = raw[left['frame']][point] + raw[right['frame']][point]
        low, high = bounds
        testcase.assertLessEqual(low, total, f'{point} too low at {left["frame"]}+{right["frame"]}')
        testcase.assertLessEqual(total, high, f'{point} too high at {left["frame"]}+{right["frame"]}')
    return left, right


def follow_path(testcase, cluster, setup, ticket, *, on_step=None):
    """跟随返回的 next 走完整张受理单，逐步核验顺序、序次与当下合规。"""
    nxt = ticket['next']
    previous_receipt = None
    responses = []
    seen = {'left': 0, 'right': 0}
    while nxt is not None:
        side, step = nxt['side'], nxt['step']
        seen[side] += 1
        testcase.assertEqual(step, seen[side])
        receipt_arg = previous_receipt if previous_receipt is not None else ...
        response = advance(cluster, side, ticket, step, receipt_arg)
        responses.append(response)
        previous_receipt = response['receipt']
        nxt = response['next']
        testcase.assertEqual(response['frame'], ticket[side][step - 1])
        testcase.assertEqual(response['sequence'], ticket['start'][side]['sequence'] + step)
        assert_current_compliant(testcase, cluster, setup, ticket)
        if on_step is not None:
            on_step(response)
    testcase.assertIsNone(responses[-1]['next'])
    return responses
