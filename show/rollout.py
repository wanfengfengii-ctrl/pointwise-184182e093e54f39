"""在线换版：安全交错规划、受理单签发与受理持久化。

受理单是自包含且经 HMAC 签名的凭据。展务台离线后，投影端仅凭受理单、
实显回执和本侧库内状态即可核验并续办，不依赖展务库可读。
"""
import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from functools import lru_cache

from art.models import Conflict, Side, identifier
from art.catalog import load_frame
from edition.packages import load_release
from show.session import ShowPhase, load_session
from show.store import control_transaction


RECEIPT_KIND = 'projection-rollout-step'
TICKET_VERSION = 1
# 受信任本机部署下的缺省共同密钥；可用 PROJECTION_ROLLOUT_KEY 覆盖。
_DEFAULT_KEY = 'pointwise-rollout-local-trust-v1'


class RolloutConflict(Conflict):
    code = 'conflict'


class RolloutPending(Conflict):
    code = 'pending'


class NoSafePath(Conflict):
    code = 'no_safe_path'


def rollout_key():
    return os.environ.get('PROJECTION_ROLLOUT_KEY') or _DEFAULT_KEY


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def sign_object(obj):
    token = dict(obj)
    token.pop('sig', None)
    signature = hmac.new(rollout_key().encode('utf-8'), canonical_bytes(token), hashlib.sha256).hexdigest()
    token['sig'] = signature
    return token


def authenticate(obj):
    """校验签名对象，返回去掉 sig 的原文；任何不可信情形报 conflict。"""
    if not isinstance(obj, dict):
        raise RolloutConflict('signed credential must be a JSON object')
    signature = obj.get('sig')
    if not isinstance(signature, str):
        raise RolloutConflict('credential is missing its signature')
    token = {key: value for key, value in obj.items() if key != 'sig'}
    expected = hmac.new(rollout_key().encode('utf-8'), canonical_bytes(token), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise RolloutConflict('credential signature does not match the shared rollout key')
    return token


def node(side, step):
    return {'side': str(side), 'step': step}


def plan_interleaving(start_frames, lists, measurements, windows):
    """返回一条走得完的安全交错路径；不存在时返回 None。

    start_frames: {'left': frame, 'right': frame}
    lists: {'left': [frame...], 'right': [frame...]}（step 1 对应下标 0）
    measurements: frame -> {point: int}
    windows: point -> (low, high)
    路径元素为 {'side': 'left'|'right', 'step': 本侧步骤号}。
    """
    points = tuple(sorted(windows))
    left_frames = lists[Side.LEFT]
    right_frames = lists[Side.RIGHT]
    n_left, n_right = len(left_frames), len(right_frames)

    def shown(side, count):
        playlist = left_frames if side == Side.LEFT else right_frames
        if count == 0:
            return start_frames[side]
        return playlist[count - 1]

    def compliant(i, j):
        left_values = measurements[shown(Side.LEFT, i)]
        right_values = measurements[shown(Side.RIGHT, j)]
        for point in points:
            total = left_values[point] + right_values[point]
            low, high = windows[point]
            if not low <= total <= high:
                return False
        return True

    @lru_cache(maxsize=None)
    def completable(i, j):
        if not compliant(i, j):
            return False
        if i == n_left and j == n_right:
            return True
        # 固定的左右优先顺序只决定指定哪一条可完成走法，不影响可行性判定。
        if i < n_left and completable(i + 1, j):
            return True
        if j < n_right and completable(i, j + 1):
            return True
        return False

    if not completable(0, 0):
        return None

    path = []
    i = j = 0
    while i < n_left or j < n_right:
        if i < n_left and completable(i + 1, j):
            i += 1
            path.append(node(Side.LEFT, i))
        elif j < n_right and completable(i, j + 1):
            j += 1
            path.append(node(Side.RIGHT, j))
        else:  # pragma: no cover - 由 completable 保证不会发生
            return None
    return path


def initialize_rollouts(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rollouts (
        show_id TEXT PRIMARY KEY REFERENCES shows(show_id),
        rollout_id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL,
        left_steps TEXT NOT NULL,
        right_steps TEXT NOT NULL,
        ticket TEXT NOT NULL
    )''')


def show_has_rollout(db, show):
    return db.execute('SELECT 1 FROM rollouts WHERE show_id = ?', (show,)).fetchone() is not None


def load_rollout_row(db, show):
    return db.execute('SELECT * FROM rollouts WHERE show_id = ?', (identifier(show),)).fetchone()


def _string_list(value, label):
    if not isinstance(value, list) or not value:
        raise ValueError(f'{label} must be a non-empty array of frame numbers')
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f'{label} entries must be frame number strings')
        result.append(identifier(item))
    return result


def _panel_current(read_current, side, show):
    try:
        current = read_current(side, show)
    except Exception as exc:  # 读取失败与“尚未实显”区分不开时按待办处理
        if isinstance(exc, RolloutPending):
            raise
        raise RolloutPending(f'{side} panel current display cannot be read') from exc
    if not isinstance(current, dict):
        raise RolloutPending(f'{side} panel has no official display yet')
    frame = current.get('frame')
    sequence = current.get('sequence')
    if not isinstance(frame, str) or type(sequence) is not int or sequence < 1:
        raise RolloutPending(f'{side} side has not completed an official display yet')
    return frame, sequence


def accept_rollout(path, show, payload, read_current):
    request_id = identifier(payload['request'])
    requested_left = _string_list(payload['left'], 'left')
    requested_right = _string_list(payload['right'], 'right')

    with control_transaction(path) as db:
        session = load_session(db, show)
        existing = load_rollout_row(db, session.show)
        if existing is not None:
            ticket = json.loads(existing['ticket'])
            same_request = existing['request_id'] == request_id
            same_lists = (json.loads(existing['left_steps']) == requested_left
                          and json.loads(existing['right_steps']) == requested_right)
            if same_request and same_lists:
                return ticket
            raise RolloutConflict('show already has a different accepted rollout request')

        if session.phase != ShowPhase.OPEN:
            raise RolloutConflict(f'rollout acceptance requires an open show, found {session.phase}')

        left_start = _panel_current(read_current, Side.LEFT, session.show)
        right_start = _panel_current(read_current, Side.RIGHT, session.show)
        starts = {Side.LEFT: left_start[0], Side.RIGHT: right_start[0]}
        start_sequences = {Side.LEFT: left_start[1], Side.RIGHT: right_start[1]}

        release = load_release(db, session.release)
        windows = {point: (reading.low, reading.high) for point, reading in release.meters.items()}
        point_names = tuple(sorted(windows))

        lists = {Side.LEFT: requested_left, Side.RIGHT: requested_right}
        measurements = {}

        def use_frame(frame_id, expected_side):
            try:
                frame = load_frame(db, frame_id)
            except KeyError as exc:
                raise ValueError(f'frame {frame_id} is not registered') from exc
            if frame.side != expected_side:
                raise ValueError(f'frame {frame_id} is not registered on the {expected_side} side')
            values = dict(frame.measurements)
            if tuple(sorted(values)) != point_names:
                raise ValueError(f'frame {frame_id} must carry exactly the release meter points')
            measurements[frame.frame] = values
            return frame.frame

        use_frame(starts[Side.LEFT], Side.LEFT)
        use_frame(starts[Side.RIGHT], Side.RIGHT)
        for frame_id in requested_left:
            use_frame(frame_id, Side.LEFT)
        for frame_id in requested_right:
            use_frame(frame_id, Side.RIGHT)

        start_frames = {Side.LEFT: starts[Side.LEFT], Side.RIGHT: starts[Side.RIGHT]}
        safe_path = plan_interleaving(start_frames, lists, measurements, windows)
        if safe_path is None:
            raise NoSafePath('no compliant interleaving can finish both rollout lists in order')

        rollout_id = 'rollout-' + uuid.uuid4().hex
        ticket = {
            'v': TICKET_VERSION,
            'rollout': rollout_id,
            'show': session.show,
            'request': request_id,
            'release': session.release,
            'start': {
                Side.LEFT.value: {'frame': starts[Side.LEFT], 'sequence': start_sequences[Side.LEFT]},
                Side.RIGHT.value: {'frame': starts[Side.RIGHT], 'sequence': start_sequences[Side.RIGHT]},
            },
            'left': requested_left,
            'right': requested_right,
            'points': list(point_names),
            'windows': {point: [low, high] for point, (low, high) in windows.items()},
            'measurements': measurements,
            'path': safe_path,
            'next': safe_path[0],
        }
        ticket = sign_object(ticket)
        db.execute('INSERT INTO rollouts VALUES (?, ?, ?, ?, ?, ?)', (
            session.show, rollout_id, request_id,
            json.dumps(requested_left, ensure_ascii=False),
            json.dumps(requested_right, ensure_ascii=False),
            json.dumps(ticket, ensure_ascii=False),
        ))
        return ticket
