import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from art.models import Conflict, Side


def open_panel_ledger(path, *, create=False):
    mode = 'rwc' if create else 'rw'
    db = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode={mode}', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


@contextmanager
def panel_transaction(path, *, create=False):
    with closing(open_panel_ledger(path, create=create)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        yield db


def initialize_panel_ledger(path, side):
    side = Side(side)
    with panel_transaction(path, create=True) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS panel_identity (
            slot INTEGER PRIMARY KEY CHECK(slot = 1), side TEXT NOT NULL
        )''')
        db.execute('INSERT INTO panel_identity VALUES (1, ?) ON CONFLICT(slot) DO NOTHING', (side,))
        saved = db.execute('SELECT side FROM panel_identity WHERE slot = 1').fetchone()['side']
        if side != saved:
            raise Conflict('database belongs to the other panel')
        db.execute('''CREATE TABLE IF NOT EXISTS installations (
            show_id TEXT PRIMARY KEY, release_id TEXT NOT NULL, side TEXT NOT NULL,
            frames TEXT NOT NULL, meters TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rehearsals (
            show_id TEXT PRIMARY KEY REFERENCES installations(show_id),
            frame_id TEXT NOT NULL, attempt INTEGER NOT NULL CHECK(attempt > 0)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS screen_state (
            show_id TEXT PRIMARY KEY REFERENCES installations(show_id),
            frame_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence > 0)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS receipts (
            show_id TEXT NOT NULL REFERENCES installations(show_id),
            request_id TEXT NOT NULL, release_id TEXT NOT NULL, side TEXT NOT NULL,
            frame_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence > 0),
            PRIMARY KEY(show_id, request_id), UNIQUE(show_id, sequence)
        )''')


def read_screen_state(db, show):
    row = db.execute('SELECT frame_id, sequence FROM screen_state WHERE show_id = ?', (show,)).fetchone()
    if row is None:
        return None, 0
    return row['frame_id'], row['sequence']


def write_screen_state(db, show, frame, sequence):
    db.execute('''INSERT INTO screen_state VALUES (?, ?, ?) ON CONFLICT(show_id) DO UPDATE SET
        frame_id = excluded.frame_id, sequence = excluded.sequence''', (show, frame, sequence))
