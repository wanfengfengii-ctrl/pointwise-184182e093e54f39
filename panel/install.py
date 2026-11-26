import json
from dataclasses import dataclass

from art.models import Conflict, Side
from edition.packages import SidePackage
from show.session import ShowPhase


@dataclass(frozen=True)
class InstalledPackage:
    show: str
    release: str
    side: Side
    frames: tuple[str, ...]
    meters: dict[str, int]


def load_installation(db, show):
    row = db.execute('SELECT * FROM installations WHERE show_id = ?', (show,)).fetchone()
    if row is None:
        raise KeyError(f'no installation for show {show}')
    return InstalledPackage(row['show_id'], row['release_id'], Side(row['side']),
                            tuple(json.loads(row['frames'])), json.loads(row['meters']))


def install_package(db, session, side, package: SidePackage):
    side = Side(side)
    if session.release != package.release or side != package.side:
        raise Conflict('package does not match the show and side')
    candidate = InstalledPackage(session.show, package.release, side, package.frames, package.meters)
    try:
        original = load_installation(db, session.show)
    except KeyError:
        if session.phase != ShowPhase.PREPARING:
            raise Conflict('first installation requires a preparing show')
        db.execute('INSERT INTO installations VALUES (?, ?, ?, ?, ?)', (
            candidate.show, candidate.release, candidate.side,
            json.dumps(candidate.frames, ensure_ascii=False), json.dumps(candidate.meters, ensure_ascii=False),
        ))
        return candidate
    if original != candidate:
        raise Conflict('show already has a different installation')
    return original


def require_installed_frame(db, show, release, side, frame):
    try:
        package = load_installation(db, show)
    except KeyError as exc:
        raise Conflict('show has no installation on this side') from exc
    if (package.release, package.side) != (release, side):
        raise Conflict('installation does not match the release and side')
    if frame not in package.frames:
        raise Conflict('frame does not belong to this installed side playlist')
    return package
