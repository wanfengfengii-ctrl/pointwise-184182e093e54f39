import json
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep

from panel.http import serve_panel
from show.http import serve_control
from show.transport import exchange


SAMPLE_DIR = Path(__file__).resolve().parents[1] / 'samples'


def sample(name):
    return json.loads((SAMPLE_DIR / f'{name}.json').read_text(encoding='utf-8'))


def scenario(case_id, inputs, expected, actual, downstream_expected, downstream_actual, code_paths, oracle):
    evidence = {'case_id': case_id, 'input': inputs, 'expected': expected, 'actual': actual,
                'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
                'code_paths': code_paths, 'oracle': oracle}
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True), flush=True)


class Listener:
    def __init__(self, server):
        self.server = server
        self.port = server.server_port
        self.thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class Cluster:
    def __enter__(self):
        self.temp = TemporaryDirectory(prefix='projection-tests-')
        root = Path(self.temp.name)
        self.control_db = root / 'control.sqlite3'
        self.panel_dbs = {side: root / f'{side}.sqlite3' for side in ('left', 'right')}
        self.ports = {}
        self.listeners = {}
        try:
            for role in ('left', 'right', 'control'):
                self.start(role)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        try:
            for role in list(reversed(self.listeners)):
                self.stop(role)
        finally:
            self.temp.cleanup()

    def start(self, role):
        if role in self.listeners:
            raise AssertionError(f'{role} is already listening')
        port = self.ports.get(role, 0)
        if role == 'control':
            server = serve_control(self.control_db, {side: self.ports[side] for side in self.panel_dbs}, port)
        else:
            server = serve_panel(self.panel_dbs[role], self.control_db, role, port)
        listener = Listener(server)
        self.listeners[role] = listener
        self.ports[role] = listener.port

    def stop(self, role):
        self.listeners.pop(role).close()

    def restart(self, role):
        self.stop(role)
        self.start(role)

    def call(self, role, method, path, body=None, *, expect=200):
        status, received = exchange(self.ports[role], method, path, body)
        if status != expect:
            raise AssertionError(f'{role} {method} {path}: wanted {expect}, got {status} {received}')
        return received

    def control(self, method, path, body=None, *, expect=200):
        return self.call('control', method, path, body, expect=expect)

    def panel(self, side, method, path, body=None, *, expect=200):
        return self.call(side, method, f'/panels/{side}{path}', body, expect=expect)

    def seed(self, release='edition-1', draft='default'):
        for frame in sample('frames'):
            self.control('POST', '/frames', frame)
        self.control('PUT', f'/drafts/{draft}', sample('draft'))
        return self.control('POST', '/releases', {'release': release, 'draft': draft})

    def prepare(self, show='matinee', release='edition-1', rehearsals=None):
        if rehearsals is None:
            rehearsals = sample('operations')['rehearsals']
        self.control('POST', '/shows', {'show': show, 'release': release})
        evidence = []
        for side, frame in rehearsals.items():
            self.panel(side, 'POST', f'/shows/{show}/install', {'release': release})
            evidence.append(self.panel(side, 'POST', f'/shows/{show}/rehearse', {'release': release, 'frame': frame}))
        return evidence

    def open(self, show='matinee', release='edition-1'):
        evidence = self.prepare(show, release)
        return self.control('POST', f'/shows/{show}/open', {'evidence': evidence})

    def cue(self, side, request, frame, show='matinee', *, hold=False, expect=200):
        suffix = '/prepare' if hold else ''
        return self.control('POST', f'/shows/{show}/cues/{side}{suffix}',
                            {'request': request, 'frame': frame}, expect=expect)

    def current(self, side, show='matinee'):
        return self.panel(side, 'GET', f'/shows/{show}/current')

    def log(self, side, show='matinee'):
        return self.panel(side, 'GET', f'/shows/{show}/log')['receipts']

    def close_show(self, show='matinee'):
        return self.control('POST', f'/shows/{show}/close', {})

    def report(self, show='matinee', *, expect=200):
        return self.control('POST', f'/shows/{show}/report', {}, expect=expect)

    def drop_display_reply(self, cue):
        side = cue['side']
        receipt_path = f"/panels/{side}/shows/{cue['show']}/receipts/{cue['request']}"
        self.call(side, 'GET', receipt_path, expect=404)
        connection = HTTPConnection('127.0.0.1', self.ports[side], timeout=4)
        try:
            connection.request('POST', f'/panels/{side}/display', json.dumps(cue).encode('utf-8'),
                               {'Content-Type': 'application/json'})
            # Wait through a separate read so that closing this socket loses an already committed reply.
            # The display connection's response is never read.
            deadline = monotonic() + 4
            while monotonic() < deadline:
                status, result = exchange(self.ports[side], 'GET', receipt_path)
                if status == 200:
                    return
                if status != 404:
                    raise AssertionError(f'receipt probe returned {status}: {result}')
                sleep(0.01)
            raise AssertionError('display did not commit before the drop-reply deadline')
        finally:
            connection.close()


def demo():
    operations = sample('operations')
    show = operations['show']
    release = operations['release']
    with Cluster() as cluster:
        fixed = cluster.seed(release, operations['draft'])
        evidence = cluster.prepare(show, release, operations['rehearsals'])
        opened = cluster.control('POST', f'/shows/{show}/open', {'evidence': evidence})
        if opened['phase'] != 'open':
            raise AssertionError(opened)
        for cue in operations['cues']:
            cluster.cue(**cue, show=show)
        closed = cluster.close_show(show)
        actual = cluster.report(show)
        expected = sample('expected-report')
        if actual != expected:
            raise AssertionError(f'expected {expected}, received {actual}')
        original = cluster.control('GET', f"/reports/{actual['report']}")
        if original != expected or closed['phase'] != 'closed':
            raise AssertionError('closed show did not return its fixed report')
        totals = {point: reading['total'] for point, reading in fixed['meters'].items()}
        if totals != {'中缝': 10, '上沿': 2}:
            raise AssertionError(totals)
        scenario('v7', {'draft': sample('draft'), 'operations': operations}, expected, actual,
                 expected, original, ['panel/display.py', 'review/intake.py', 'review/coverage.py', 'review/history.py'],
                 '按四次正式送达逐侧手排端内序次，L3 仅试映，取原片单中从未正式播过的画面')
        print(json.dumps({'seam': fixed['meters'], 'report': actual}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    demo()
