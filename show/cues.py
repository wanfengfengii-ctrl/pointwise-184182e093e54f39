from contextlib import closing, contextmanager
from dataclasses import dataclass

from art.models import Conflict, Side, identifier
from edition.packages import load_release, package_for_side
from show.rollout import show_has_rollout
from show.session import ShowPhase, load_session
from show.store import control_transaction, open_control_store


class CueRejected(Conflict):
    code = 'cue_rejected'


@dataclass(frozen=True)
class OrdinaryCue:
    show: str
    release: str
    side: Side
    request: str
    frame: str

    def __post_init__(self):
        for value in (self.show, self.release, self.request, self.frame):
            identifier(value)
        object.__setattr__(self, 'side', Side(self.side))


def _require_open(session):
    if session.phase != ShowPhase.OPEN:
        raise CueRejected(f'new displays require an open show, found {session.phase}')


def prepare_cue(path, show, side, request, frame):
    side = Side(side)
    with closing(open_control_store(path)) as db:
        session = load_session(db, show)
        _require_open(session)
        package = package_for_side(load_release(db, session.release), side)
        cue = OrdinaryCue(show, session.release, side, request, frame)
        if cue.frame not in package.frames:
            raise CueRejected('frame is outside this side playlist')
        return cue


@contextmanager
def ordinary_display_gate(path, cue):
    # Keep the phase lock until the caller's panel transaction exits.
    with control_transaction(path) as db:
        session = load_session(db, cue.show)
        _require_open(session)
        if cue.release != session.release:
            raise CueRejected('cue release differs from the show release')
        if show_has_rollout(db, cue.show):
            # 已受理换版：拦住普通新切画。原有普通首执在进入此锁前已按原执复领。
            raise CueRejected('show has an accepted rollout; ordinary new displays are suspended until close')
        yield session
