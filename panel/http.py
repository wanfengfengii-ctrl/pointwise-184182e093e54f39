import argparse
from contextlib import closing
from dataclasses import asdict
from functools import partial
from http.server import ThreadingHTTPServer

from art.models import Conflict, Side, identifier
from edition.packages import load_release, package_for_side
from panel.advance import advance_rollout, read_step_response
from panel.display import display_frame, read_current_display
from panel.install import install_package, load_installation
from panel.ledger import initialize_panel_ledger, open_panel_ledger, panel_transaction
from panel.receipts import find_receipt, read_display_log
from panel.rehearse import read_rehearsal, rehearse_frame
from show.cues import OrdinaryCue
from show.session import load_session
from show.store import control_transaction
from show.transport import JsonHandler, fields


class PanelRequestHandler(JsonHandler):
    def __init__(self, *args, panel_path, control_path, side):
        self.panel_path = panel_path
        self.control_path = control_path
        self.side = side
        super().__init__(*args)

    def dispatch(self, method, path, body):
        if len(path) < 3 or path[0] != 'panels':
            raise KeyError('unknown panel route')
        if Side(path[1]) != self.side:
            raise Conflict('request addresses the other panel')
        match method, path[2:]:
            case 'POST', ['advance']:
                fields(body, 'ticket', 'step', optional=('receipt',))
                return 200, advance_rollout(self.panel_path, self.side, body)
            case 'GET', ['steps', rollout, step_raw]:
                return 200, read_step_response(self.panel_path, self.side, rollout, step_raw)
            case 'POST', ['display']:
                fields(body, 'show', 'release', 'side', 'request', 'frame')
                cue = OrdinaryCue(**body)
                receipt = display_frame(self.panel_path, self.control_path, self.side, cue)
                return 200, asdict(receipt)
            case 'POST', ['shows', show, 'install']:
                fields(body, 'release')
                return 200, self._install(show, body['release'])
            case 'POST', ['shows', show, 'rehearse']:
                fields(body, 'release', 'frame')
                return 200, self._rehearse(show, body)
            case 'GET', ['shows', show, *resource]:
                return 200, self._read(identifier(show), resource)
            case _:
                raise KeyError('unknown panel route')

    def _install(self, show, release):
        with control_transaction(self.control_path) as control:
            session = load_session(control, show)
            if identifier(release) != session.release:
                raise Conflict('requested release differs from the show release')
            package = package_for_side(load_release(control, release), self.side)
            with panel_transaction(self.panel_path) as panel:
                return asdict(install_package(panel, session, self.side, package))

    def _rehearse(self, show, body):
        with control_transaction(self.control_path) as control:
            session = load_session(control, show)
            with panel_transaction(self.panel_path) as panel:
                return asdict(rehearse_frame(panel, session, self.side, **body))

    def _read(self, show, resource):
        with closing(open_panel_ledger(self.panel_path)) as db:
            match resource:
                case ['rehearsal']:
                    return asdict(read_rehearsal(db, show))
                case ['current']:
                    return read_current_display(db, show)
                case ['receipts', request]:
                    receipt = find_receipt(db, show, identifier(request))
                    if receipt is None:
                        raise KeyError('display request has no receipt')
                    return asdict(receipt)
                case ['log']:
                    package = load_installation(db, show)
                    return {'show': show, 'release': package.release, 'side': package.side,
                            'receipts': [asdict(receipt) for receipt in read_display_log(db, show)]}
                case _:
                    raise KeyError('unknown panel route')


def serve_panel(panel_path, control_path, side, port):
    side = Side(side)
    initialize_panel_ledger(panel_path, side)
    handler = partial(PanelRequestHandler, panel_path=panel_path, control_path=control_path, side=side)
    return ThreadingHTTPServer(('127.0.0.1', port), handler)


def main():
    parser = argparse.ArgumentParser(description='巡展投影端')
    parser.add_argument('--db', required=True)
    parser.add_argument('--control-db', required=True)
    parser.add_argument('--side', choices=list(Side), required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    with serve_panel(args.db, args.control_db, args.side, args.port) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return


if __name__ == '__main__':
    main()
