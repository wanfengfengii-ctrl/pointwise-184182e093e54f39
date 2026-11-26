import argparse
from contextlib import closing
from dataclasses import asdict
from functools import partial
from http.server import ThreadingHTTPServer

from art.catalog import initialize_catalog, load_frame, register_frame
from art.models import Side
from edition.drafts import initialize_drafts, load_draft, load_settings, save_draft, save_setting
from edition.packages import initialize_releases, load_release, package_for_side
from edition.publish import publish_edition
from review.history import initialize_reports
from review.report import finalize_report, get_report
from show.close import close_show
from show.cues import prepare_cue
from show.readiness import open_show
from show.session import create_session, initialize_sessions, load_session
from show.store import control_transaction, open_control_store
from show.transport import JsonHandler, PanelUnavailable, exchange, fields


def initialize_control(path):
    with control_transaction(path, create=True) as db:
        initialize_catalog(db)
        initialize_drafts(db)
        initialize_releases(db)
        initialize_sessions(db)
        initialize_reports(db)


class ControlRequestHandler(JsonHandler):
    def __init__(self, *args, db_path, panel_ports):
        self.db_path = db_path
        self.panel_ports = panel_ports
        super().__init__(*args)

    def _panel_read(self, side, show, resource):
        status, data = exchange(self.panel_ports[side], 'GET', f'/panels/{side}/shows/{show}/{resource}')
        if status != 200:
            raise PanelUnavailable(f'{side} {resource} returned HTTP {status}')
        return data

    def dispatch(self, method, path, body):
        match method, path:
            case 'POST', ['frames']:
                fields(body, 'frame', 'side', 'content', 'measurements')
                with control_transaction(self.db_path) as db:
                    return 200, register_frame(db, **body).payload()
            case 'PUT', ['drafts', draft]:
                fields(body, 'left', 'right', 'windows')
                with control_transaction(self.db_path) as db:
                    return 200, save_draft(db, draft, **body).payload()
            case 'PUT', ['settings', name]:
                fields(body, 'value')
                with control_transaction(self.db_path) as db:
                    return 200, save_setting(db, name, body['value'])
            case 'POST', ['releases']:
                fields(body, 'release', optional=('draft',))
                if 'draft' in body and not isinstance(body['draft'], str):
                    raise ValueError('draft must be a draft number')
                return 200, publish_edition(self.db_path, **body).payload()
            case 'POST', ['shows']:
                fields(body, 'show', 'release')
                with control_transaction(self.db_path) as db:
                    return 200, asdict(create_session(db, **body))
            case 'POST', ['shows', show, 'open']:
                fields(body, 'evidence')
                read = partial(self._panel_read, resource='rehearsal')
                return 200, asdict(open_show(self.db_path, show, body['evidence'], read))
            case 'POST', ['shows', show, 'cues', side, 'prepare']:
                fields(body, 'request', 'frame')
                return 200, asdict(prepare_cue(self.db_path, show, side, **body))
            case 'POST', ['shows', show, 'cues', side]:
                fields(body, 'request', 'frame')
                cue = prepare_cue(self.db_path, show, side, **body)
                return exchange(self.panel_ports[cue.side], 'POST', f'/panels/{cue.side}/display', asdict(cue))
            case 'POST', ['shows', show, 'close']:
                fields(body)
                return 200, asdict(close_show(self.db_path, show))
            case 'POST', ['shows', show, 'report']:
                fields(body)
                read = partial(self._panel_read, resource='log')
                return 200, finalize_report(self.db_path, show, read).payload()
            case 'GET', _:
                return 200, self._read(path)
            case _:
                raise KeyError('unknown control route')

    def _read(self, path):
        if len(path) == 2 and path[0] == 'reports':
            return get_report(self.db_path, path[1]).payload()
        with closing(open_control_store(self.db_path)) as db:
            match path:
                case ['frames', frame]:
                    return load_frame(db, frame).payload()
                case ['drafts', draft]:
                    return load_draft(db, draft).payload()
                case ['settings']:
                    return load_settings(db)
                case ['releases', release]:
                    return load_release(db, release).payload()
                case ['releases', release, 'panels', side]:
                    return asdict(package_for_side(load_release(db, release), Side(side)))
                case ['shows', show]:
                    return asdict(load_session(db, show))
                case _:
                    raise KeyError('unknown control route')


def serve_control(db_path, panel_ports, port):
    initialize_control(db_path)
    handler = partial(ControlRequestHandler, db_path=db_path, panel_ports=dict(panel_ports))
    return ThreadingHTTPServer(('127.0.0.1', port), handler)


def main():
    parser = argparse.ArgumentParser(description='巡展展务服务')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--db', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--db', required=True)
    serve.add_argument('--port', type=int, required=True)
    serve.add_argument('--left-port', type=int, required=True)
    serve.add_argument('--right-port', type=int, required=True)
    args = parser.parse_args()
    if args.command == 'init':
        initialize_control(args.db)
        return
    ports = {Side.LEFT: args.left_port, Side.RIGHT: args.right_port}
    with serve_control(args.db, ports, args.port) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return


if __name__ == '__main__':
    main()
