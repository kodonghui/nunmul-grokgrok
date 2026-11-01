"""Authenticated, lookup-only notebook relay; no medication state access."""

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request

if __package__:
    from .common import JsonArgumentParser, ToolError, run_cli
    from .dur_rules import DUR_FIELDS, DUR_PATHS, validate_page
    from .relay_config import validate_url
    from .rx_lookup import (CONNECT_TIMEOUT, EXPLAIN_PATH, PERMIT_PATH, ROOT, PublicDataClient,
                            TimedHTTPConnection, TimedHTTPSConnection, _NoRedirect, load_env)
else:
    from common import JsonArgumentParser, ToolError, run_cli
    from dur_rules import DUR_FIELDS, DUR_PATHS, validate_page
    from relay_config import validate_url
    from rx_lookup import (CONNECT_TIMEOUT, EXPLAIN_PATH, PERMIT_PATH, ROOT, PublicDataClient,
                           TimedHTTPConnection, TimedHTTPSConnection, _NoRedirect, load_env)


MAX_BODY = 4096
PERMIT_FIELDS = ("ITEM_SEQ", "ITEM_NAME", "ENTP_NAME", "ETC_OTC_CODE", "ETC_OTC_NAME",
                 "CANCEL_NAME", "ITEM_INGR_NAME", "CHANGE_DATE")
EXPLAIN_FIELDS = ("itemSeq", "itemName", "efcyQesitm", "useMethodQesitm", "atpnWarnQesitm",
                  "atpnQesitm", "intrcQesitm", "seQesitm", "depositMethodQesitm", "updateDe")
ERRORS = {
    "RELAY_UNAUTHORIZED": "중계 인증을 확인해 주세요.",
    "RELAY_INVALID_INPUT": "허용된 약 검색·설명 입력만 사용할 수 있습니다.",
    "RELAY_NOT_FOUND": "지원하지 않는 중계 경로입니다.",
    "RELAY_METHOD_NOT_ALLOWED": "지원하지 않는 요청 방식입니다.",
    "RELAY_TOO_LARGE": "중계 요청 크기를 초과했습니다.",
    "RELAY_BUSY": "중계 서버가 다른 조회를 처리 중입니다.",
    "NO_KEY": "노트북에 공공데이터 키가 설정되지 않았습니다.",
    "RELAY_UPSTREAM_NETWORK_ERROR": "노트북에서 공공데이터에 연결하지 못했습니다.",
    "RELAY_UPSTREAM_API_ERROR": "공공데이터 인증·활용 승인·파라미터를 확인해 주세요.",
    "RELAY_UPSTREAM_INVALID_RESPONSE": "공공데이터 응답을 확인할 수 없습니다.",
    "RELAY_UPSTREAM_HTTP_ERROR": "공공데이터 HTTP 요청에 실패했습니다.",
    "RELAY_INTERNAL": "중계 서버가 요청을 완료하지 못했습니다.",
}


def _token_valid(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{32,200}", value) is not None


def _json(raw):
    def reject(_):
        raise ValueError
    return json.loads(raw.decode("utf-8"), parse_constant=reject)


def query_upstream(payload, client):
    if not isinstance(payload, dict):
        raise ToolError("RELAY_INVALID_INPUT", ERRORS["RELAY_INVALID_INPUT"])
    operation = payload.get("operation")
    if operation == "permit":
        if (set(payload) - {"operation", "itemName", "maker"}
                or not isinstance(payload.get("itemName"), str)
                or not 1 <= len(payload["itemName"].strip()) <= 200
                or payload.get("maker") is not None and
                (not isinstance(payload["maker"], str) or not 1 <= len(payload["maker"].strip()) <= 200)):
            raise ToolError("RELAY_INVALID_INPUT", ERRORS["RELAY_INVALID_INPUT"])
        params = {"item_name": payload["itemName"], "pageNo": 1, "numOfRows": 100}
        if payload.get("maker") is not None:
            params["entp_name"] = payload["maker"]
        rows, fields = client.get(PERMIT_PATH, params), PERMIT_FIELDS
    elif operation == "explain":
        if (set(payload) != {"operation", "itemSeq"} or not isinstance(payload.get("itemSeq"), str)
                or not re.fullmatch(r"[0-9]{1,20}", payload["itemSeq"])):
            raise ToolError("RELAY_INVALID_INPUT", ERRORS["RELAY_INVALID_INPUT"])
        rows, fields = client.get(EXPLAIN_PATH, {"itemSeq": payload["itemSeq"], "pageNo": 1, "numOfRows": 1}), EXPLAIN_FIELDS
    elif operation == "dur":
        if (set(payload) != {"operation", "type", "itemSeq", "pageNo"}
                or not isinstance(payload.get("type"), str) or payload["type"] not in DUR_PATHS
                or not isinstance(payload.get("itemSeq"), str) or not re.fullmatch(r"[0-9]{1,20}", payload["itemSeq"])
                or type(payload.get("pageNo")) is not int or not 1 <= payload["pageNo"] <= 100):
            raise ToolError("RELAY_INVALID_INPUT", ERRORS["RELAY_INVALID_INPUT"])
        number = payload["pageNo"]
        page = validate_page(client.get_page(DUR_PATHS[payload["type"]],
                            {"itemSeq": payload["itemSeq"], "pageNo": number, "numOfRows": 100}), number)
        return {**page, "items": [{field: row[field] for field in DUR_FIELDS if field in row}
                                  if isinstance(row, dict) else {} for row in page["items"]]}
    else:
        raise ToolError("RELAY_INVALID_INPUT", ERRORS["RELAY_INVALID_INPUT"])
    return {"items": [{field: row[field] for field in fields if field in row}
                      if isinstance(row, dict) else {} for row in rows[:100]]}


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, token, client):
        if address[0] != "127.0.0.1":
            raise ToolError("INVALID_INPUT", "중계 서버는 127.0.0.1에만 열 수 있습니다.")
        if not _token_valid(token):
            raise ToolError("NO_RELAY_TOKEN", "32~200자의 별도 중계 토큰이 필요합니다.")
        self.token, self.client = token, client
        self.capacity = threading.BoundedSemaphore(8)
        super().__init__(address, RelayHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address

    def process_request(self, request, address):
        if not self.capacity.acquire(blocking=False):
            raw = json.dumps({"error": {"code": "RELAY_BUSY", "message": ERRORS["RELAY_BUSY"]}}).encode()
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: "
                                + str(len(raw)).encode() + b"\r\nConnection: close\r\n\r\n" + raw)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.capacity.release()

    def handle_error(self, request, address):
        pass  # Never print request data, credentials or upstream exception text.


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "NunmulRelay"
    sys_version = ""

    def log_message(self, *args):
        pass

    def _send(self, status, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def _error(self, status, code):
        self._send(status, {"error": {"code": code, "message": ERRORS[code]}})

    def send_error(self, code, message=None, explain=None):
        if code == 413:
            self._error(413, "RELAY_TOO_LARGE")
        else:
            self._error(400, "RELAY_INVALID_INPUT")

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "nunmul-rx-relay"})
        elif self.path == "/v1/query":
            self._error(405, "RELAY_METHOD_NOT_ALLOWED")
        else:
            self._error(404, "RELAY_NOT_FOUND")

    def do_POST(self):
        authorizations = self.headers.get_all("Authorization", [])
        expected = "Bearer " + self.server.token
        if len(authorizations) != 1 or not hmac.compare_digest(authorizations[0].encode(), expected.encode()):
            self._error(401, "RELAY_UNAUTHORIZED")
            return
        if self.path != "/v1/query":
            self._error(404, "RELAY_NOT_FOUND")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0])
                or self.headers.get("Transfer-Encoding") is not None
                or self.headers.get_content_type() != "application/json"):
            self._error(400, "RELAY_INVALID_INPUT")
            return
        if len(lengths[0]) > 10:
            self._error(413, "RELAY_TOO_LARGE")
            return
        length = int(lengths[0])
        if length > MAX_BODY:
            self._error(413, "RELAY_TOO_LARGE")
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError
            payload = _json(raw)
            result = query_upstream(payload, self.server.client)
            self._send(200, result)
        except (ValueError, UnicodeError):
            self._error(400, "RELAY_INVALID_INPUT")
        except ToolError as error:
            if error.code == "RELAY_INVALID_INPUT":
                self._error(400, error.code)
            elif error.code == "NO_KEY":
                self._error(503, "NO_KEY")
            else:
                category = ("NETWORK_ERROR" if error.code == "NETWORK_ERROR" else
                            "API_ERROR" if error.code == "API_ERROR" else
                            "HTTP_ERROR" if error.code.startswith("HTTP_") else "INVALID_RESPONSE")
                self._error(502, "RELAY_UPSTREAM_" + category)
        except (OSError, TimeoutError):
            self.close_connection = True
        except Exception:
            self._error(500, "RELAY_INTERNAL")

    def _unsupported(self):
        self._error(405, "RELAY_METHOD_NOT_ALLOWED")

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _unsupported


class _RelayHTTPConnection(TimedHTTPConnection):
    def connect(self):
        super().connect()
        self.sock.settimeout(30)


class _RelayHTTPSConnection(TimedHTTPSConnection):
    def connect(self):
        super().connect()
        self.sock.settimeout(30)


class _RelayHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return self.do_open(_RelayHTTPConnection, request)


class _RelayHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_RelayHTTPSConnection, request, context=self._context)


class RelayClient:
    def __init__(self, environ=None, opener=None, sleeper=None):
        settings = os.environ if environ is None else environ
        self.token = (settings.get("PUBLIC_DATA_RELAY_TOKEN") or "").strip()
        self.url = settings.get("PUBLIC_DATA_RELAY_URL") or ""
        self.opener = opener if opener is not None else urllib.request.build_opener(
            _RelayHTTPHandler(), _RelayHTTPSHandler(), _NoRedirect())
        self.sleep = sleeper if sleeper is not None else time.sleep

    def require_credentials(self):
        if not _token_valid(self.token):
            raise ToolError("NO_RELAY_TOKEN", "별도 중계 인증 토큰을 확인해 주세요.")

    def get(self, path, parameters):
        return self._get(path, parameters, False)

    def get_page(self, path, parameters):
        return self._get(path, parameters, True)

    def _get(self, path, parameters, pagination):
        self.require_credentials()
        url = validate_url(self.url)
        if path == PERMIT_PATH:
            payload = {"operation": "permit", "itemName": parameters.get("item_name")}
            if parameters.get("entp_name") is not None:
                payload["maker"] = parameters["entp_name"]
        elif path == EXPLAIN_PATH:
            payload = {"operation": "explain", "itemSeq": parameters.get("itemSeq")}
        elif pagination and path in DUR_PATHS.values():
            kind = next(kind for kind, endpoint in DUR_PATHS.items() if endpoint == path)
            payload = {"operation": "dur", "type": kind, "itemSeq": parameters.get("itemSeq"), "pageNo": parameters.get("pageNo")}
        else:
            raise ToolError("RELAY_INVALID_INPUT", ERRORS["RELAY_INVALID_INPUT"])
        request = urllib.request.Request(url + "/v1/query", data=json.dumps(payload).encode(),
                                         headers={"Authorization": "Bearer " + self.token,
                                                  "Content-Type": "application/json", "Accept": "application/json"})
        for attempt in range(2):
            try:
                with self.opener.open(request, timeout=CONNECT_TIMEOUT) as response:
                    raw = response.read(2_000_001)
                    if len(raw) > 2_000_000:
                        raise ToolError("INVALID_RELAY_RESPONSE", "중계 응답 크기를 초과했습니다.")
            except urllib.error.HTTPError as error:
                retryable = error.code == 429
                try:
                    body = _json(error.read(4096))
                    code = body.get("error", {}).get("code") if isinstance(body, dict) else None
                except (ValueError, UnicodeError, OSError, AttributeError):
                    code = None
                error.close()
                failure = ToolError(code, ERRORS[code]) if isinstance(code, str) and code in ERRORS else ToolError(
                    "RELAY_HTTP_ERROR", "중계 HTTP 요청에 실패했습니다.")
            except (urllib.error.URLError, OSError, TimeoutError):
                retryable = True
                failure = ToolError("RELAY_NETWORK_ERROR", "노트북 중계에 연결하거나 응답을 읽을 수 없습니다.")
            else:
                try:
                    body = _json(raw)
                except (ValueError, UnicodeError):
                    raise ToolError("INVALID_RELAY_RESPONSE", "중계 JSON 응답을 확인해 주세요.") from None
                if pagination:
                    return validate_page(body, parameters.get("pageNo"))
                if not isinstance(body, dict) or set(body) != {"items"} or not isinstance(body["items"], list):
                    raise ToolError("INVALID_RELAY_RESPONSE", "중계 응답 형식을 확인해 주세요.")
                return body["items"]
            if attempt == 0 and retryable:
                self.sleep(0.5)
            else:
                raise failure


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token-file", type=Path)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("유효한 포트 번호가 필요합니다.")
    load_env(ROOT, os.environ)
    token = os.environ.get("PUBLIC_DATA_RELAY_TOKEN", "")
    if args.token_file is not None:
        try:
            token = args.token_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raise ToolError("NO_RELAY_TOKEN", "중계 토큰 파일을 읽을 수 없습니다.") from None
    settings = dict(os.environ)
    settings["PUBLIC_DATA_BASE_URL"] = "https://apis.data.go.kr"
    client = PublicDataClient(settings)
    client.require_credentials()
    with RelayServer(("127.0.0.1", args.port), token, client) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return {"stopped": True}


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
