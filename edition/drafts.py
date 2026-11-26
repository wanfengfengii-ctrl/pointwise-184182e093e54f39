import json
from dataclasses import dataclass

from art.models import identifier
from edition.seam import MeterWindow, normalize_windows


DEFAULT_SETTINGS = {'site_name': '巡展', 'default_draft': 'default'}


@dataclass(frozen=True)
class ProofDraft:
    draft: str
    left: tuple[str, ...]
    right: tuple[str, ...]
    windows: dict[str, MeterWindow]

    def payload(self):
        return {
            'draft': self.draft,
            'left': self.left,
            'right': self.right,
            'windows': {point: [window.low, window.high] for point, window in self.windows.items()},
        }


def initialize_drafts(db):
    db.execute('CREATE TABLE IF NOT EXISTS drafts (draft_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.executemany('INSERT INTO settings VALUES (?, ?) ON CONFLICT(name) DO NOTHING',
                   DEFAULT_SETTINGS.items())
    default = ProofDraft('default', (), (), normalize_windows({'中缝': [9, 11], '上沿': [2, 2]}))
    db.execute('INSERT INTO drafts VALUES (?, ?) ON CONFLICT(draft_id) DO NOTHING',
               (default.draft, json.dumps(default.payload(), ensure_ascii=False)))


def _playlist(value):
    if not isinstance(value, (list, tuple)):
        raise ValueError('a playlist must be an array of frame numbers')
    return tuple(identifier(frame) for frame in value)


def save_draft(db, draft, left, right, windows):
    candidate = ProofDraft(identifier(draft), _playlist(left), _playlist(right), normalize_windows(windows))
    db.execute('''INSERT INTO drafts VALUES (?, ?)
        ON CONFLICT(draft_id) DO UPDATE SET payload = excluded.payload''',
        (candidate.draft, json.dumps(candidate.payload(), ensure_ascii=False)))
    return candidate


def load_draft(db, draft):
    row = db.execute('SELECT payload FROM drafts WHERE draft_id = ?', (identifier(draft),)).fetchone()
    if row is None:
        raise KeyError(f'draft {draft} is unknown')
    payload = json.loads(row['payload'])
    return ProofDraft(draft, tuple(payload['left']), tuple(payload['right']), normalize_windows(payload['windows']))


def load_settings(db):
    return {row['name']: row['value'] for row in db.execute('SELECT name, value FROM settings ORDER BY name')}


def save_setting(db, name, value):
    if name not in DEFAULT_SETTINGS:
        raise KeyError(f'setting {name} is unknown')
    if not isinstance(value, str) or not value.strip():
        raise ValueError('a setting value must be nonempty text')
    if name == 'default_draft':
        load_draft(db, value)
    db.execute('''INSERT INTO settings VALUES (?, ?)
        ON CONFLICT(name) DO UPDATE SET value = excluded.value''', (name, value))
    return {'name': name, 'value': value}
