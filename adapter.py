"""Buyer-credential stdio MCP prototype. No private validator implementation.

Prepared source only: qualify mocked protocol/transport in isolation before use.
State belongs to ONE buyer/account; never delete it to retry an uncertain start.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ACTOR = 'kLghTtrolYoiD2Vnz'
BUILD = '8hcfHTb1H0ZdSzdTU'
BUILD_NUMBER = '0.1.6'
PROTOCOL = '2025-11-25'
MAX_MESSAGE = 262144
MAX_RESPONSE = 8 * 1024 * 1024
OPTIONS = {'build': BUILD_NUMBER, 'memory': '256', 'timeout': '60',
           'maxTotalChargeUsd': '0.01', 'restartOnError': 'false', 'waitForFinish': '0'}
TERMINAL_FAILURES = {'FAILED', 'ABORTED', 'TIMED-OUT'}


class Refused(Exception):
    """Only a fixed code is safe to disclose, never an upstream exception body."""


def require(condition, code):
    if not condition:
        raise Refused(code)


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def unique_pairs(items):
    out = {}
    for k, v in items:
        require(k not in out, 'duplicate_json_key')
        out[k] = v
    return out


def loads(raw):
    return json.loads(raw, object_pairs_hook=unique_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Refused('nonfinite_json')))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identifier(value):
    require(isinstance(value, str) and re.fullmatch('[A-Za-z0-9]{1,64}', value), 'invalid_provider_id')
    return value


def operation_id(value):
    require(isinstance(value, str) and re.fullmatch('[A-Za-z0-9_-]{8,100}', value), 'invalid_operation_id')
    return value


def token():
    value = os.environ.get('APIFY_TOKEN', '')
    require(bool(value) and not any(c.isspace() for c in value), 'buyer_APIFY_TOKEN_required')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Transport:
    """Fixed origin/route API transport; no redirects, proxy or automatic retry."""
    def request(self, method, path, query=None, body=None):
        allowed = (method == 'POST' and path == '/v2/actors/' + ACTOR + '/runs') or (
            method == 'GET' and re.fullmatch(
                r'/v2/(?:actor-builds/[A-Za-z0-9]+|actor-runs/[A-Za-z0-9]+|datasets/[A-Za-z0-9]+/items|key-value-stores/[A-Za-z0-9]+/records/OUTPUT)', path))
        require(allowed, 'route_not_allowed')
        secret = token()
        url = 'https://api.apify.com' + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, method=method,
            data=None if body is None else dumps(body).encode('utf-8'),
            headers={'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json',
                     'Accept': 'application/json', 'User-Agent': 'csv-contract-preflight-mcp/0.1.0'})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=10) as response:
                raw = response.read(MAX_RESPONSE + 1)
            require(len(raw) <= MAX_RESPONSE, 'response_size_bound')
            require(secret.encode() not in raw, 'credential_in_upstream_response')
            return loads(raw.decode('utf-8'))
        except Refused:
            raise
        except Exception:
            raise Refused('upstream_request_failed_or_uncertain') from None


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = path.parent.lstat()
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid()
                and not parent.st_mode & 0o077, 'buyer_state_parent_must_be_private')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            meta = os.fstat(fd)
            require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.getuid()
                    and meta.st_nlink == 1 and not meta.st_mode & 0o077, 'buyer_state_must_be_private')
        finally:
            os.close(fd)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS operations(
            operation_id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL,
            schema_sha TEXT NOT NULL, csv_sha TEXT NOT NULL, phase TEXT NOT NULL,
            run_id TEXT, result TEXT, intended_at INTEGER NOT NULL)''')
        self.db.commit()

    def get(self, op):
        row = self.db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id(op),)).fetchone()
        require(row is not None, 'unknown_operation')
        return dict(row)

    def admit(self, op, payload):
        digest = sha(dumps({'actor': ACTOR, 'build': BUILD, 'input': payload}).encode())
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.db.execute('SELECT * FROM operations WHERE operation_id=?', (op,)).fetchone()
            if old:
                require(old['digest'] == digest, 'operation_payload_conflict')
                self.db.commit(); return dict(old), False
            duplicate = self.db.execute('SELECT * FROM operations WHERE digest=?', (digest,)).fetchone()
            if duplicate:
                self.db.commit(); return dict(duplicate), False
            self.db.execute('INSERT INTO operations VALUES(?,?,?,?,?,NULL,NULL,?)',
                (op, digest, sha(payload['contractJson'].encode()), sha(payload['csvText'].encode()),
                 'START_INTENT', int(time.time())))
            self.db.commit()
            return self.get(op), True
        except BaseException:
            self.db.rollback(); raise

    def update(self, op, phase, run_id=None, result=None):
        raw = None if result is None else dumps(result)
        secret = os.environ.get('APIFY_TOKEN', '')
        require(not secret or raw is None or secret not in raw, 'credential_in_result')
        with self.db:
            self.db.execute('UPDATE operations SET phase=?,run_id=COALESCE(?,run_id),result=COALESCE(?,result) WHERE operation_id=?',
                            (phase, run_id, raw, op))
        return self.get(op)


def validate_report(report, row):
    """Public report envelope verification only; never validate/repair CSV here."""
    require(isinstance(report, dict) and set(report) == {'profile', 'verdict', 'schema_sha256', 'csv_sha256', 'records', 'findings'}, 'report_shape')
    require(report['profile'] == 'csv-contract-preflight-0.1' and report['verdict'] in ('pass', 'findings'), 'report_profile')
    require(report['schema_sha256'] == row['schema_sha'] and report['csv_sha256'] == row['csv_sha'], 'report_input_hash_mismatch')
    n = report['records']
    require(type(n) is int and 0 <= n <= 500 and isinstance(report['findings'], list), 'report_record_bound')
    require((report['verdict'] == 'pass') == (not report['findings']), 'report_verdict_mismatch')
    for f in report['findings']:
        require(isinstance(f, dict) and type(f.get('record')) is int and 1 <= f['record'] <= n, 'finding_record')
        code = f.get('code')
        if code == 'wrong_column_count':
            require(set(f) == {'record', 'code'}, 'finding_shape')
        elif code in ('missing_value', 'invalid_integer'):
            require(set(f) == {'record', 'field', 'code'} and isinstance(f['field'], str) and bool(f['field']), 'finding_shape')
        elif code == 'duplicate_primary_key':
            require(set(f) == {'record', 'first_record', 'code'} and type(f['first_record']) is int
                    and 1 <= f['first_record'] < f['record'], 'finding_shape')
        else:
            raise Refused('unknown_finding_code')
    return report


class Adapter:
    def __init__(self, store_path, transport=None):
        self.store_path = store_path
        self.transport = transport or Transport()

    def snapshot(self, row):
        return {'operationId': row['operation_id'], 'state': row['phase'], 'runId': row['run_id'],
                'report': loads(row['result']) if row['result'] else None,
                'billing': 'Buyer Apify native billing; at most one start per durable local intent. Actual charges/revenue not verified.',
                'limits': 'A profile pass is not source truth or import authorization. Unknown starts HOLD: no retry under any new ID or fresh state.'}

    def start(self, args):
        require(isinstance(args, dict) and set(args) == {'operationId', 'contractJson', 'csvText', 'confirmPurchase'}, 'input_keys')
        op = operation_id(args['operationId'])
        require(args['confirmPurchase'] is True, 'explicit_purchase_confirmation_required')
        payload = {k: args[k] for k in ('contractJson', 'csvText')}
        for key, limit in (('contractJson', 8000), ('csvText', 20000)):
            require(isinstance(payload[key], str), 'input_must_be_two_strings')
            require(len(payload[key].encode('utf-8')) <= limit, 'input_utf8_byte_bound')
        secret = token()
        require(not any(secret in value for value in payload.values()) and secret not in op, 'credential_in_input')
        store = Store(self.store_path)
        try:
            row, send = store.admit(op, payload)
            if not send:
                return self.snapshot(row)
            try:
                build = self.transport.request('GET', '/v2/actor-builds/' + BUILD)['data']
                require(build.get('id') == BUILD and build.get('actId') == ACTOR
                        and build.get('buildNumber') == BUILD_NUMBER and build.get('status') == 'SUCCEEDED', 'approved_build_not_verified')
                run = self.transport.request('POST', '/v2/actors/' + ACTOR + '/runs', OPTIONS, payload)['data']
                # Retain returned ID before any further interpretation or result GET.
                run_id = identifier(run.get('id'))
                require(secret not in run_id, 'credential_in_provider_id')
                row = store.update(op, 'RUN_KNOWN', run_id=run_id)
                require(run.get('actId') == ACTOR and run.get('buildId') == BUILD, 'run_binding_mismatch')
                return self.snapshot(row)
            except Exception:
                row = store.get(op)
                row = store.update(op, 'HOLD_KNOWN_RUN' if row['run_id'] else 'START_UNCERTAIN')
                return self.snapshot(row)
        finally:
            store.db.close()

    def get(self, args):
        require(isinstance(args, dict) and set(args) == {'operationId'}, 'input_keys')
        store = Store(self.store_path)
        try:
            row = store.get(args['operationId'])
            if row['phase'] in ('COMPLETE', 'TERMINAL_FAILURE') or not row['run_id']:
                return self.snapshot(row)
            try:
                run = self.transport.request('GET', '/v2/actor-runs/' + identifier(row['run_id']))['data']
                require(run.get('id') == row['run_id'] and run.get('actId') == ACTOR and run.get('buildId') == BUILD, 'run_binding_mismatch')
                if run.get('status') in TERMINAL_FAILURES:
                    return self.snapshot(store.update(row['operation_id'], 'TERMINAL_FAILURE'))
                if run.get('status') != 'SUCCEEDED':
                    return self.snapshot(row)
                ds = identifier(run.get('defaultDatasetId')); kv = identifier(run.get('defaultKeyValueStoreId'))
                items = self.transport.request('GET', '/v2/datasets/' + ds + '/items', {'format': 'json', 'limit': '2', 'clean': 'true'})
                output = self.transport.request('GET', '/v2/key-value-stores/' + kv + '/records/OUTPUT')
                require(isinstance(items, list) and len(items) == 1 and items[0] == output, 'dataset_OUTPUT_mismatch')
                report = validate_report(output, row)
                return self.snapshot(store.update(row['operation_id'], 'COMPLETE', result=report))
            except Exception:
                return self.snapshot(store.update(row['operation_id'], 'HOLD_KNOWN_RUN'))
        finally:
            store.db.close()


CANONICAL_REPORT_SCHEMA = {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'CSV Contract Preflight report', 'type': 'object', 'additionalProperties': False, 'required': ['profile', 'verdict', 'schema_sha256', 'csv_sha256', 'records', 'findings'], 'properties': {'profile': {'const': 'csv-contract-preflight-0.1'}, 'verdict': {'enum': ['pass', 'findings']}, 'schema_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}, 'csv_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}, 'records': {'type': 'integer', 'minimum': 0, 'maximum': 500}, 'findings': {'type': 'array', 'items': {'oneOf': [{'type': 'object', 'additionalProperties': False, 'required': ['record', 'code'], 'properties': {'record': {'type': 'integer', 'minimum': 1, 'maximum': 500}, 'code': {'const': 'wrong_column_count'}}}, {'type': 'object', 'additionalProperties': False, 'required': ['record', 'field', 'code'], 'properties': {'record': {'type': 'integer', 'minimum': 1, 'maximum': 500}, 'field': {'type': 'string', 'minLength': 1}, 'code': {'enum': ['missing_value', 'invalid_integer']}}}, {'type': 'object', 'additionalProperties': False, 'required': ['record', 'first_record', 'code'], 'properties': {'record': {'type': 'integer', 'minimum': 1, 'maximum': 500}, 'first_record': {'type': 'integer', 'minimum': 1, 'maximum': 500}, 'code': {'const': 'duplicate_primary_key'}}}]}}}, 'allOf': [{'if': {'properties': {'verdict': {'const': 'pass'}}}, 'then': {'properties': {'findings': {'maxItems': 0}}}, 'else': {'properties': {'findings': {'minItems': 1}}}}]}

OUTPUT_SCHEMA = {'type': 'object', 'required': ['operationId', 'state', 'runId', 'report', 'billing', 'limits'],
                 'properties': {'operationId': {'type': 'string'}, 'state': {'type': 'string'},
                                'runId': {'type': ['string', 'null']}, 'report': {'anyOf': [CANONICAL_REPORT_SCHEMA, {'type': 'null'}]},
                                'billing': {'type': 'string'}, 'limits': {'type': 'string'}}, 'additionalProperties': False}
TOOLS = [
    {'name': 'start_validation', 'description': 'PURCHASE: explicitly start one bounded CSV report using YOUR Apify account. Completed pass/findings reports costUSD0.01. Never retry an uncertain start with another ID. Inputs stored by Apify. No repair/import permission.',
     'inputSchema': {'type': 'object', 'properties': {'operationId': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{8,100}$'},
        'contractJson': {'type': 'string', 'description': 'At most8000 UTF8 bytes'}, 'csvText': {'type': 'string', 'description': 'At most20000 UTF8 bytes; public profile500records/64columns'},
        'confirmPurchase': {'type': 'boolean', 'const': True}}, 'required': ['operationId', 'contractJson', 'csvText', 'confirmPurchase'], 'additionalProperties': False},
     'outputSchema': OUTPUT_SCHEMA, 'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': True}},
    {'name': 'get_validation', 'description': 'Read only the registered original run and bounded matching dataset/OUTPUT; persist local result. Unknown start stays HOLD. No run creation, retry, input repair, creator-revenue or import claim.',
     'inputSchema': {'type': 'object', 'properties': {'operationId': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{8,100}$'}}, 'required': ['operationId'], 'additionalProperties': False},
     'outputSchema': OUTPUT_SCHEMA, 'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': True}}
]


class Server:
    def __init__(self, adapter):
        self.adapter = adapter
        self.initialized = False
        self.ready = False

    def handle(self, msg):
        require(isinstance(msg, dict) and msg.get('jsonrpc') == '2.0' and isinstance(msg.get('method'), str), 'invalid_request')
        method = msg['method']; params = msg.get('params', {})
        if 'id' not in msg:
            if method == 'notifications/initialized' and self.initialized:
                self.ready = True
            return None
        ident = msg['id']
        require(type(ident) in (str, int), 'invalid_request_id')
        if method == 'initialize':
            require(not self.initialized and isinstance(params, dict) and isinstance(params.get('protocolVersion'), str)
                    and isinstance(params.get('capabilities'), dict) and isinstance(params.get('clientInfo'), dict), 'invalid_initialize')
            self.initialized = True
            result = {'protocolVersion': PROTOCOL, 'capabilities': {'tools': {'listChanged': False}},
                      'serverInfo': {'name': 'csv-contract-preflight-mcp', 'version': '0.1.0'},
                      'instructions': 'Metadata is free. start_validation explicitly spends buyer native Apify allowance. Preserve durable state and never retry an unknown start.'}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            require(self.ready, 'not_initialized')
            result = {'tools': TOOLS}
        elif method == 'tools/call':
            require(self.ready and isinstance(params, dict) and 'task' not in params, 'not_initialized_or_unsupported_tasks')
            name = params.get('name'); args = params.get('arguments', {})
            if name not in ('start_validation', 'get_validation'):
                return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': 'unknown_tool'}}
            try:
                value = self.adapter.start(args) if name == 'start_validation' else self.adapter.get(args)
                result = {'content': [{'type': 'text', 'text': dumps(value)}], 'structuredContent': value,
                          'isError': value['state'] in ('START_INTENT', 'START_UNCERTAIN', 'HOLD_KNOWN_RUN', 'TERMINAL_FAILURE')}
            except Exception:
                result = {'content': [{'type': 'text', 'text': 'Request refused; no automatic retry. Inspect local operation status.'}], 'isError': True}
        else:
            return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': 'method_not_found'}}
        return {'jsonrpc': '2.0', 'id': ident, 'result': result}


def main():
    os.umask(0o077)
    base = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))
    state = os.environ.get('CSV_CONTRACT_MCP_STATE', str(base / 'csv-contract-preflight-mcp/operations.sqlite3'))
    server = Server(Adapter(state))
    while True:
        raw = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
        if not raw:
            return
        if len(raw) > MAX_MESSAGE or not raw.endswith(b'\n'):
            # Terminate on overlong/unframed input; don't interpret remainder as another call.
            return
        msg = None
        try:
            msg = loads(raw.decode('utf-8')); response = server.handle(msg)
        except Exception:
            ident = msg.get('id') if isinstance(msg, dict) and type(msg.get('id')) in (str, int) else None
            response = {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32600, 'message': 'invalid_or_refused_request'}}
        if response is not None:
            serialized = dumps(response)
            secret = os.environ.get('APIFY_TOKEN', '')
            if secret and secret in serialized:
                serialized = dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32603, 'message': 'redacted_response_refused'}})
            sys.stdout.write(serialized + '\n'); sys.stdout.flush()


if __name__ == '__main__':
    main()
