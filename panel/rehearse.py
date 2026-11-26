from dataclasses import dataclass

from art.models import Conflict, Side, identifier
from panel.install import load_installation, require_installed_frame
from show.session import ShowPhase


@dataclass(frozen=True)
class RehearsalEvidence:
    show: str
    release: str
    side: Side
    frame: str
    attempt: int

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {'show', 'release', 'side', 'frame', 'attempt'}:
            raise ValueError('expected a complete rehearsal record')
        attempt = payload['attempt']
        if type(attempt) is not int or attempt < 1:
            raise ValueError('invalid rehearsal attempt')
        return cls(identifier(payload['show']), identifier(payload['release']), Side(payload['side']),
                   identifier(payload['frame']), attempt)


def rehearse_frame(db, session, side, release, frame):
    release, frame = identifier(release), identifier(frame)
    if release != session.release:
        raise Conflict('rehearsal release differs from the show release')
    package = require_installed_frame(db, session.show, release, side, frame)
    try:
        original = read_rehearsal(db, session.show)
    except KeyError:
        original = None
    if original is not None and original.frame == frame:
        return original
    if session.phase != ShowPhase.PREPARING:
        raise Conflict('a new rehearsal requires a preparing show')
    attempt = 1 if original is None else original.attempt + 1
    db.execute('''INSERT INTO rehearsals VALUES (?, ?, ?) ON CONFLICT(show_id) DO UPDATE SET
        frame_id = excluded.frame_id, attempt = excluded.attempt''', (session.show, frame, attempt))
    return RehearsalEvidence(session.show, release, package.side, frame, attempt)


def read_rehearsal(db, show):
    package = load_installation(db, show)
    row = db.execute('SELECT frame_id, attempt FROM rehearsals WHERE show_id = ?', (show,)).fetchone()
    if row is None:
        raise KeyError(f'no rehearsal for show {show}')
    return RehearsalEvidence(show, package.release, package.side, row['frame_id'], row['attempt'])
