from contextlib import closing

from art.models import Side
from edition.packages import load_release, package_for_side
from review.coverage import compute_side_coverage
from review.history import ReportSnapshot, load_report, save_report
from review.intake import ReportPending, collect_display_logs
from show.session import load_session
from show.store import control_transaction, open_control_store


def finalize_report(path, show, read_log):
    report_id = f'report-{show}'
    with closing(open_control_store(path)) as db:
        session = load_session(db, show)
        try:
            return load_report(db, report_id)
        except KeyError:
            release = load_release(db, session.release)

    logs = collect_display_logs(session, read_log)
    sides = {}
    for side in Side:
        try:
            sides[side] = compute_side_coverage(package_for_side(release, side), logs.sides[side])
        except ValueError as exc:
            raise ReportPending(f'{side} official display log does not match the playlist') from exc
    snapshot = ReportSnapshot(report_id, session.show, session.release, sides)
    with control_transaction(path) as db:
        # Another caller can finish the same closed show during the two reads.
        try:
            return load_report(db, report_id)
        except KeyError:
            return save_report(db, snapshot)


def get_report(path, report):
    with closing(open_control_store(path)) as db:
        return load_report(db, report)
