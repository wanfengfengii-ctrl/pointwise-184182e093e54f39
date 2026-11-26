import json
from dataclasses import asdict, dataclass

from art.models import Side, identifier
from panel.receipts import DisplayReceipt
from review.coverage import SideCoverage


@dataclass(frozen=True)
class ReportSnapshot:
    report: str
    show: str
    release: str
    sides: dict[Side, SideCoverage]

    def payload(self):
        return asdict(self)


def initialize_reports(db):
    db.execute('''CREATE TABLE IF NOT EXISTS reports (
        report_id TEXT PRIMARY KEY,
        show_id TEXT NOT NULL UNIQUE REFERENCES shows(show_id),
        payload TEXT NOT NULL
    )''')


def save_report(db, report: ReportSnapshot):
    db.execute('INSERT INTO reports VALUES (?, ?, ?)',
               (report.report, report.show, json.dumps(report.payload(), ensure_ascii=False)))
    return report


def load_report(db, report):
    row = db.execute('SELECT payload FROM reports WHERE report_id = ?', (identifier(report),)).fetchone()
    if row is None:
        raise KeyError(f'report {report} is not finalized')
    payload = json.loads(row['payload'])
    sides = {}
    for side, coverage in payload['sides'].items():
        played = tuple(DisplayReceipt.from_payload(receipt) for receipt in coverage['played'])
        sides[Side(side)] = SideCoverage(played, tuple(coverage['missing']))
    return ReportSnapshot(payload['report'], payload['show'], payload['release'], sides)
