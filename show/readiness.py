from art.models import Conflict, Side
from panel.rehearse import RehearsalEvidence
from show.session import ShowPhase, load_session, mark_open
from show.store import control_transaction


class ShowNotReady(Conflict):
    code = 'not_ready'


def open_show(path, show, evidence, read_rehearsal):
    if not isinstance(evidence, list) or len(evidence) != len(Side):
        raise ShowNotReady('both rehearsal records are required')
    submitted = {}
    for item in evidence:
        try:
            record = RehearsalEvidence.from_payload(item)
        except (TypeError, ValueError) as exc:
            raise ShowNotReady('invalid rehearsal evidence') from exc
        if record.side in submitted:
            raise ShowNotReady('both rehearsal sides are required')
        submitted[record.side] = record

    with control_transaction(path) as db:
        session = load_session(db, show)
        if session.phase == ShowPhase.CLOSED:
            raise ShowNotReady('the show has already closed')
        for side, record in submitted.items():
            if (record.show, record.release) != (session.show, session.release):
                raise ShowNotReady('rehearsal belongs to a different show or release')
            try:
                stored = RehearsalEvidence.from_payload(read_rehearsal(side, show))
            except (OSError, TypeError, ValueError) as exc:
                raise ShowNotReady(f'{side} rehearsal cannot be read') from exc
            if stored != record:
                raise ShowNotReady(f'{side} rehearsal does not match the saved record')
        return mark_open(db, show)
