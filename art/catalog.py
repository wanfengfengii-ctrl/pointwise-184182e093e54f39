import json

from art.measurements import normalize_measurements
from art.models import Conflict, Frame, Side, identifier


class FrameConflict(Conflict):
    code = 'frame_conflict'


def initialize_catalog(db):
    db.execute('''CREATE TABLE IF NOT EXISTS frames (
        frame_id TEXT PRIMARY KEY, side TEXT NOT NULL,
        content TEXT NOT NULL, measurements TEXT NOT NULL
    )''')


def register_frame(db, frame, side, content, measurements):
    frame = identifier(frame)
    side = Side(side)
    if not isinstance(content, str):
        raise ValueError('frame content must be text')
    candidate = Frame(frame, side, content, normalize_measurements(measurements))
    inserted = db.execute('''INSERT INTO frames VALUES (?, ?, ?, ?)
        ON CONFLICT(frame_id) DO NOTHING''', (
        frame, side, content, json.dumps(candidate.measurements, ensure_ascii=False),
    ))
    if inserted.rowcount == 1:
        return candidate
    original = load_frame(db, frame)
    if candidate != original:
        raise FrameConflict('frame number already names another original')
    return original


def load_frame(db, frame):
    row = db.execute('SELECT * FROM frames WHERE frame_id = ?', (identifier(frame),)).fetchone()
    if row is None:
        raise KeyError(f'frame {frame} is unknown')
    return Frame(row['frame_id'], Side(row['side']), row['content'],
                 normalize_measurements(json.loads(row['measurements'])))
