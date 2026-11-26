import json
import sqlite3
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class PanelUnavailable(OSError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _decode(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)


def fields(payload, *required, optional=()):
    if not isinstance(payload, dict) or not set(required) <= payload.keys():
        raise ValueError('missing request fields: ' + ', '.join(required))
    if payload.keys() - set(required) - set(optional):
        raise ValueError('unexpected request fields')
    return payload


def exchange(port, method, path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = Request(f'http://127.0.0.1:{port}{path}', data=data, method=method,
                      headers={'Content-Type': 'application/json'})
    try:
        try:
            response = build_opener(ProxyHandler({})).open(request, timeout=4)
        except HTTPError as error:
            response = error
        with response:
            payload = _decode(response.read())
            if not isinstance(payload, dict):
                raise ValueError('response must be an object')
            return response.getcode(), payload
    except (OSError, ValueError, HTTPException) as exc:
        raise PanelUnavailable('loopback service is unavailable') from exc


class JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle('GET')

    def do_POST(self):
        self._handle('POST')

    def do_PUT(self):
        self._handle('PUT')

    def log_message(self, format, *args):
        return

    def _handle(self, method):
        try:
            path = [unquote(part) for part in urlsplit(self.path).path.split('/')[1:]]
            body = self._read_body() if method != 'GET' else {}
            status, payload = self.dispatch(method, path, body)
        except KeyError as exc:
            status, payload = 404, {'error': 'not_found', 'message': str(exc.args[0])}
        except ValueError as exc:
            status = getattr(exc, 'status', 400)
            payload = {'error': getattr(exc, 'code', 'invalid_input'), 'message': str(exc)}
            payload.update(getattr(exc, 'details', {}))
        except sqlite3.Error:
            status, payload = 503, {'error': 'storage_error', 'message': 'database operation failed'}
        except PanelUnavailable as exc:
            status, payload = 503, {'error': 'panel_unavailable', 'message': str(exc)}
        self._reply(status, payload)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length < 0 or length > 1_048_576:
            raise ValueError('invalid request size')
        payload = _decode(self.rfile.read(length)) if length else {}
        if not isinstance(payload, dict):
            raise ValueError('request body must be an object')
        return payload

    def _reply(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except ConnectionError:
            # The operation has already produced its durable result.
            pass
