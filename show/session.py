from dataclasses import dataclass
from enum import StrEnum

from art.models import Conflict, identifier
from edition.packages import load_release


class ShowPhase(StrEnum):
    PREPARING = 'preparing'
    OPEN = 'open'
    CLOSED = 'closed'


@dataclass(frozen=True)
class ShowSession:
    show: str
    release: str
    phase: ShowPhase


def initialize_sessions(db):
    db.execute('''CREATE TABLE IF NOT EXISTS shows (
        show_id TEXT PRIMARY KEY,
        release_id TEXT NOT NULL REFERENCES releases(release_id),
        phase TEXT NOT NULL CHECK(phase IN ('preparing', 'open', 'closed'))
    )''')


def create_session(db, show, release):
    show = identifier(show)
    package = load_release(db, release)
    db.execute('INSERT INTO shows VALUES (?, ?, ?) ON CONFLICT(show_id) DO NOTHING',
               (show, package.release, ShowPhase.PREPARING))
    session = load_session(db, show)
    if session.release != release:
        raise Conflict('show is already bound to a different release')
    return session


def load_session(db, show):
    row = db.execute('SELECT * FROM shows WHERE show_id = ?', (identifier(show),)).fetchone()
    if row is None:
        raise KeyError(f'show {show} is unknown')
    return ShowSession(row['show_id'], row['release_id'], ShowPhase(row['phase']))


def _move(db, show, before, after):
    session = load_session(db, show)
    if session.phase == after:
        return session
    if session.phase != before:
        raise Conflict(f'show cannot move from {session.phase} to {after}')
    db.execute('UPDATE shows SET phase = ? WHERE show_id = ?', (after, show))
    return ShowSession(show, session.release, after)


def mark_open(db, show):
    return _move(db, show, ShowPhase.PREPARING, ShowPhase.OPEN)


def mark_closed(db, show):
    return _move(db, show, ShowPhase.OPEN, ShowPhase.CLOSED)
