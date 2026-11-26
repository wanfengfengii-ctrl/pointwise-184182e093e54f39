from dataclasses import dataclass

from art.models import Conflict, Side, identifier


class ReceiptConflict(Conflict):
    code = 'receipt_conflict'


@dataclass(frozen=True)
class DisplayReceipt:
    show: str
    release: str
    side: Side
    request: str
    frame: str
    sequence: int

    def require_original(self, release, side, frame):
        if (self.release, self.side, self.frame) != (release, side, frame):
            raise ReceiptConflict('request number was already used with different display parameters')
        return self

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {'show', 'release', 'side', 'request', 'frame', 'sequence'}:
            raise ValueError('expected a complete display receipt')
        sequence = payload['sequence']
        if type(sequence) is not int or sequence < 1:
            raise ValueError('invalid display sequence')
        return cls(identifier(payload['show']), identifier(payload['release']), Side(payload['side']),
                   identifier(payload['request']), identifier(payload['frame']), sequence)


def _from_row(row):
    return DisplayReceipt(row['show_id'], row['release_id'], Side(row['side']), row['request_id'],
                          row['frame_id'], row['sequence'])


def find_receipt(db, show, request):
    row = db.execute('SELECT * FROM receipts WHERE show_id = ? AND request_id = ?',
                     (show, request)).fetchone()
    return _from_row(row) if row is not None else None


def save_receipt(db, receipt):
    db.execute('''INSERT INTO receipts (show_id, request_id, release_id, side, frame_id, sequence)
        VALUES (?, ?, ?, ?, ?, ?)''', (receipt.show, receipt.request, receipt.release,
                                       receipt.side, receipt.frame, receipt.sequence))


def read_display_log(db, show):
    rows = db.execute('SELECT * FROM receipts WHERE show_id = ? ORDER BY sequence', (show,))
    return tuple(_from_row(row) for row in rows)
