"""投影端在线换版办理。

只依赖随请求交来的签名受理单、前序实显回执和本侧 SQLite 状态；
不读取展务监听或展务库，因此展务台离线、展务库不可读时仍可续办。
"""
import json
from contextlib import closing

from art.models import Side
from panel.ledger import open_panel_ledger, panel_transaction, read_screen_state, write_screen_state
from show.rollout import (
    RECEIPT_KIND,
    RolloutConflict,
    RolloutPending,
    authenticate,
    sign_object,
)


def initialize_rollout_steps(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rollout_steps (
        rollout_id TEXT NOT NULL,
        show_id TEXT NOT NULL,
        side TEXT NOT NULL,
        step INTEGER NOT NULL CHECK(step > 0),
        response TEXT NOT NULL,
        PRIMARY KEY (rollout_id, side, step)
    )''')


def _stored_step(db, rollout_id, side, step):
    row = db.execute(
        'SELECT response FROM rollout_steps WHERE rollout_id = ? AND side = ? AND step = ?',
        (rollout_id, str(side), step),
    ).fetchone()
    return json.loads(row['response']) if row is not None else None


def read_step_response(panel_path, side, rollout_id, step):
    side = Side(side)
    try:
        step = int(step)
    except (TypeError, ValueError) as exc:
        raise KeyError('rollout step is not available') from exc
    if step < 1:
        raise KeyError('rollout step is not available')
    # 纯查询：只读打开，绝不写屏、不产生回执。
    with closing(open_panel_ledger(panel_path)) as db:
        response = _stored_step(db, rollout_id, side, step)
    if response is None:
        raise KeyError(f'rollout {rollout_id} side {side} step {step} has not been performed')
    return response


def verify_ticket(raw_ticket):
    # authenticate 对任何不是完整签名对象的“受理单”一律按伪造/改写处理（conflict）。
    ticket = authenticate(raw_ticket)
    for key in ('rollout', 'show', 'start', 'left', 'right', 'path', 'next'):
        if key not in ticket:
            raise RolloutConflict('rollout ticket is missing required verification material')
    if ticket.get('v') != 1 or not isinstance(ticket['path'], list) or not ticket['path']:
        raise RolloutConflict('rollout ticket has an invalid planned path')
    return ticket


def _authenticate_receipt(raw_receipt, ticket):
    if raw_receipt is None:
        return None
    receipt = authenticate(raw_receipt)
    if receipt.get('kind') != RECEIPT_KIND:
        # 普通首执或其他凭据不能充当换版步执。
        raise RolloutConflict('credential is not a rollout step receipt')
    if receipt.get('rollout') != ticket['rollout'] or receipt.get('show') != ticket['show']:
        raise RolloutConflict('receipt belongs to a different show or rollout')
    if receipt.get('side') not in (Side.LEFT.value, Side.RIGHT.value):
        raise RolloutConflict('receipt has an invalid side binding')
    if type(receipt.get('step')) is not int or receipt['step'] < 1:
        raise RolloutConflict('receipt has an invalid step binding')
    return receipt


def _side_key(side):
    return side.value if isinstance(side, Side) else str(side)


def _frame_before(ticket, side_key, step):
    if step == 1:
        return ticket['start'][side_key]['frame']
    return ticket[side_key][step - 2]


def _state_is_safe(ticket, path_index):
    """按受理单指定路径核对落步后所有测点仍然合规。"""
    windows = ticket.get('windows')
    measurements = ticket.get('measurements')
    if not windows or not measurements:
        return True
    counts = {Side.LEFT.value: 0, Side.RIGHT.value: 0}
    for item in ticket['path'][:path_index + 1]:
        counts[item['side']] += 1

    def frame_for(side_key, count):
        if count == 0:
            return ticket['start'][side_key]['frame']
        return ticket[side_key][count - 1]

    for point, bounds in windows.items():
        low, high = bounds
        total = (measurements[frame_for(Side.LEFT.value, counts[Side.LEFT.value])][point]
                 + measurements[frame_for(Side.RIGHT.value, counts[Side.RIGHT.value])][point])
        if not low <= total <= high:
            return False
    return True


def advance_rollout(panel_path, expected_side, body):
    expected_side = Side(expected_side)
    if not isinstance(body, dict):
        raise ValueError('advance request must be an object')
    if type(body.get('step')) is not int or isinstance(body.get('step'), bool) or body['step'] < 1:
        raise ValueError('step must be a positive integer counted from 1 on this side')
    step = body['step']
    ticket = verify_ticket(body.get('ticket'))
    side_key = _side_key(expected_side)

    if step > len(ticket[side_key]):
        raise RolloutConflict('step is outside the accepted side rollout list')

    path = ticket['path']
    path_index = None
    for index, item in enumerate(path):
        if item.get('side') == side_key and item.get('step') == step:
            path_index = index
            break
    if path_index is None:
        raise RolloutConflict('ticket path does not contain the requested side step')

    with panel_transaction(panel_path) as db:
        original = _stored_step(db, ticket['rollout'], side_key, step)
        if original is not None:
            # 失答、重投、重开：已办步只领回原响应，前序凭据可省略，
            # 也不校验随重投附带的凭据（它不会被当作任何新实显的证据）。
            return original

        supplied = _authenticate_receipt(body.get('receipt'), ticket)
        predecessor = path[path_index - 1] if path_index > 0 else None

        def local_predicate():
            if predecessor is None or predecessor['side'] != side_key:
                return False
            return _stored_step(db, ticket['rollout'], side_key, predecessor['step']) is not None

        proven_locally = local_predicate()
        if predecessor is not None and not proven_locally:
            if supplied is None:
                raise RolloutPending('the required predecessor display receipt has not been provided')
            if (supplied['side'], supplied['step']) != (predecessor['side'], predecessor['step']):
                # 同单但只证明了较早（或另一个）步骤，不足以推进本步。
                raise RolloutPending('supplied receipt does not prove the required predecessor step')
            expected_receipt_frame = ticket[predecessor['side']][predecessor['step'] - 1]
            expected_receipt_sequence = (
                ticket['start'][predecessor['side']]['sequence'] + predecessor['step']
            )
            if (supplied['frame'], supplied['sequence']) != (expected_receipt_frame, expected_receipt_sequence):
                raise RolloutConflict('receipt frame or sequence does not match the accepted rollout')

        expected_before_frame = _frame_before(ticket, side_key, step)
        expected_before_sequence = ticket['start'][side_key]['sequence'] + step - 1
        actual_frame, actual_sequence = read_screen_state(db, ticket['show'])
        if (actual_frame, actual_sequence) != (expected_before_frame, expected_before_sequence):
            raise RolloutConflict('panel screen state is not bound to the accepted rollout start and steps')

        target_frame = ticket[side_key][step - 1]
        sequence = expected_before_sequence + 1

        next_node = path[path_index + 1] if path_index + 1 < len(path) else None
        receipt = sign_object({
            'kind': RECEIPT_KIND,
            'v': 1,
            'rollout': ticket['rollout'],
            'show': ticket['show'],
            'release': ticket.get('release'),
            'side': side_key,
            'step': step,
            'frame': target_frame,
            'sequence': sequence,
            'next': next_node,
        })
        response = {
            'rollout': ticket['rollout'],
            'show': ticket['show'],
            'side': side_key,
            'step': step,
            'frame': target_frame,
            'sequence': sequence,
            'receipt': receipt,
            'next': next_node,
        }
        if not _state_is_safe(ticket, path_index):
            raise RolloutConflict('planned rollout state fails the permitted measurement windows')

        write_screen_state(db, ticket['show'], target_frame, sequence)
        db.execute('INSERT INTO rollout_steps VALUES (?, ?, ?, ?, ?)', (
            ticket['rollout'], ticket['show'], side_key, step,
            json.dumps(response, ensure_ascii=False),
        ))
        return response
