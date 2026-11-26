from dataclasses import dataclass

from art.models import Conflict, Side
from panel.receipts import DisplayReceipt
from show.session import ShowPhase, ShowSession


class ReportPending(Conflict):
    code = 'pending'


@dataclass(frozen=True)
class ClosedDisplayLogs:
    show: str
    release: str
    sides: dict[Side, tuple[DisplayReceipt, ...]]


def _verified_log(payload, show, release, side):
    if not isinstance(payload, dict) or set(payload) != {'show', 'release', 'side', 'receipts'}:
        raise ValueError('display log envelope is incomplete')
    if (payload['show'], payload['release'], payload['side']) != (show, release, side):
        raise ValueError('display log does not belong to this show, release and side')
    if not isinstance(payload['receipts'], list):
        raise ValueError('display log receipts must be an array')
    receipts = tuple(DisplayReceipt.from_payload(item) for item in payload['receipts'])
    for sequence, receipt in enumerate(receipts, start=1):
        if (receipt.show, receipt.release, receipt.side, receipt.sequence) != (show, release, side, sequence):
            raise ValueError('display receipt identity or sequence does not match the log')
    if len({receipt.request for receipt in receipts}) != len(receipts):
        raise ValueError('display log repeats a request number')
    return receipts


def collect_display_logs(session: ShowSession, read_log):
    if session.phase != ShowPhase.CLOSED:
        raise ReportPending('close the show before reconciling displays')
    logs = {}
    for side in Side:
        try:
            logs[side] = _verified_log(read_log(side, session.show), session.show, session.release, side)
        except (OSError, ValueError) as exc:
            raise ReportPending(f'{side} official display log is unavailable or mismatched') from exc
    return ClosedDisplayLogs(session.show, session.release, logs)
