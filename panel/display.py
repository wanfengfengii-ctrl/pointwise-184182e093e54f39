from contextlib import closing

from panel.install import load_installation, require_installed_frame
from panel.ledger import open_panel_ledger, panel_transaction, read_screen_state, write_screen_state
from panel.receipts import DisplayReceipt, find_receipt, save_receipt
from show.cues import CueRejected, ordinary_display_gate


def _original(db, cue):
    receipt = find_receipt(db, cue.show, cue.request)
    if receipt is not None:
        return receipt.require_original(cue.release, cue.side, cue.frame)
    return None


def _lookup(path, cue):
    with closing(open_panel_ledger(path)) as db:
        return _original(db, cue)


def display_frame(panel_path, control_path, side, cue):
    if cue.side != side:
        raise CueRejected('cue addresses the other side')
    original = _lookup(panel_path, cue)
    if original is not None:
        return original
    try:
        with ordinary_display_gate(control_path, cue):
            with panel_transaction(panel_path) as db:
                original = _original(db, cue)
                if original is not None:
                    return original
                require_installed_frame(db, cue.show, cue.release, side, cue.frame)
                _, previous = read_screen_state(db, cue.show)
                receipt = DisplayReceipt(cue.show, cue.release, side, cue.request, cue.frame, previous + 1)
                write_screen_state(db, cue.show, cue.frame, receipt.sequence)
                save_receipt(db, receipt)
                return receipt
    except CueRejected:
        # A concurrent copy may have committed immediately before the show closed.
        original = _lookup(panel_path, cue)
        if original is not None:
            return original
        raise


def read_current_display(db, show):
    package = load_installation(db, show)
    frame, sequence = read_screen_state(db, show)
    return {'show': show, 'release': package.release, 'side': package.side,
            'frame': frame, 'sequence': sequence}
