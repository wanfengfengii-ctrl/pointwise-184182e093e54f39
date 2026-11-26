import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


def open_control_store(path, *, create=False):
    mode = 'rwc' if create else 'rw'
    db = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode={mode}', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


@contextmanager
def control_transaction(path, *, create=False):
    with closing(open_control_store(path, create=create)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        yield db
