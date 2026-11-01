"""Look up official medicine candidates without guessing product identities."""

from decimal import Decimal
import http.client
import json
import os
from pathlib import Path
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

if __package__:
    from .common import JsonArgumentParser, ToolError, atomic_write_json, read_json, run_cli
else:
    from common import JsonArgumentParser, ToolError, atomic_write_json, read_json, run_cli


ROOT = Path(__file__).resolve().parents[1]
PERMIT_PATH = "/1471000/DrugPrdtPrmsnInfoService08/getDrugPrdtPrmsnDtlInq08"
EXPLAIN_PATH = "/1471000/DrbEasyDrugInfoService/getDrbEasyDrugList"
SOURCE = "식품의약품안전처 e약은요"
CONNECT_TIMEOUT = 2
READ_TIMEOUT = 10
NUMBER = r"[0-9]+(?:\.[0-9]+)?"
UNIT = r"마이크로그램|마이크로그람|밀리그램|밀리그람|그램|그람|mcg|μg|ug|mg|g"
MASS = re.compile(rf"({NUMBER}(?:[/+]{NUMBER})*)({UNIT})(?![a-z])", re.I)
FORM = re.compile(r"(캡슐|캅셀|정|시럽|과립|액|산)$")
ADMIN = re.compile(rf"/{NUMBER}(?:정|캡슐|캅셀|포|개)$")
ELLIPSIS = re.compile(r"(?:\.{2,}|[⋯…‥])+")
TAIL_ELLIPSIS = re.compile(r"(?:\.{2,}|[⋯…‥])+$")
PARTIAL = re.compile(rf"(.+(?:캡슐|캅셀|정|시럽|과립|액|산))({NUMBER}(?:[/+]{NUMBER})*)$")
COMPLEX = re.compile(rf"%|퍼센트|IU|국제단위|단위|/(?:{NUMBER})?(?:mL|L|그램|그람|g)(?![a-z])", re.I)


def normalize(value):
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))
    return ADMIN.sub("", value)


def without_description(value):
    value = re.sub(r"\([^)]*\)", "", value)
    return value.split("(", 1)[0]


def base_name(value):
    return MASS.sub("", without_description(value)).rstrip("/+").replace("캅셀", "캡슐")


def strengths(value):
    result = []
    for match in MASS.finditer(value):
        unit = match.group(2).casefold()
        factor = Decimal(1000000) if unit in ("g", "그램", "그람") else (
            Decimal(1000) if unit in ("mg", "밀리그램", "밀리그람") else Decimal(1))
        result.extend(Decimal(number) * factor for number in re.split(r"[/+]", match.group(1)))
    return tuple(result)


def is_complex(value):
    masses = list(MASS.finditer(value))
    return any(not any(m.start() <= found.start() < m.end() for m in masses)
               for found in COMPLEX.finditer(value))


def canonical(value):
    def replace(match):
        return "/".join(format(n.normalize(), "f") for n in strengths(match.group(0))) + "mcg"
    return MASS.sub(replace, value).replace("퍼센트", "%").replace("캅셀", "캡슐").casefold()


def swap_units(value):
    def replace(match):
        unit = match.group(2).casefold()
        alternate = {"mg": "밀리그램", "밀리그램": "mg", "밀리그람": "mg",
                     "g": "그램", "그램": "g", "그람": "g",
                     "마이크로그램": "mcg", "마이크로그람": "mcg"}.get(unit, "마이크로그램")
        return match.group(1) + alternate
    return MASS.sub(replace, value)


class NameClue:
    def __init__(self, original):
        original = normalize(original)
        self.truncated = bool(ELLIPSIS.search(original)) or "(" in re.sub(r"\([^)]*\)", "", original)
        self.clean = ADMIN.sub("", without_description(TAIL_ELLIPSIS.sub("", original)))
        self.base = base_name(self.clean)
        self.strengths = strengths(self.clean)
        self.complex = is_complex(self.clean)
        partial = PARTIAL.fullmatch(self.clean) if self.truncated and not self.strengths else None
        self.partial = bool(partial)
        if partial:
            self.base = partial.group(1).replace("캅셀", "캡슐")
        form = FORM.search(self.base)
        self.form = form.group(1).replace("캅셀", "캡슐") if form else None
        masses = list(MASS.finditer(self.clean))
        complete_mass = bool(masses and masses[-1].end() == len(self.clean))
        self.queryable = bool(self.clean) and (not self.truncated or complete_mass
                                               or FORM.search(self.clean) or self.partial)

    def queries(self):
        if not self.queryable:
            return []
        result = [self.clean, swap_units(self.clean)]
        if self.strengths and not self.complex:
            result.append(self.base)
        return list(dict.fromkeys(q for q in result if q))[:3]

    def matches(self, name):
        candidate = without_description(normalize(name))
        base = base_name(candidate)
        form = FORM.search(base)
        form = form.group(1).replace("캅셀", "캡슐") if form else None
        if base.casefold() != self.base.casefold() or form != self.form:
            return False
        if any((token in base) != (token in self.base) for token in ("서방", "장용")):
            return False
        if self.partial:
            return not is_complex(candidate) and candidate.casefold().startswith(self.clean.casefold())
        if self.complex:
            return canonical(candidate) == canonical(self.clean)
        if is_complex(candidate):
            return False
        return not self.strengths or strengths(candidate) == self.strengths


def load_env(root, environ):
    allowed = {"PUBLIC_DATA_SERVICE_KEY", "PUBLIC_DATA_BASE_URL", "PUBLIC_DATA_RELAY_URL", "PUBLIC_DATA_RELAY_TOKEN"}
    if allowed.issubset(environ):
        return
    path = Path(root) / ".env"
    if not path.exists():
        return
    try:
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                name, value = line.split("=", 1)
                name, value = name.strip(), value.strip()
                if name not in allowed or name in environ:
                    continue
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                environ[name] = value
    except (OSError, UnicodeError):
        raise ToolError("ENV_READ_FAILED", ".env를 읽을 수 없습니다.") from None


class TimedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        self.sock.settimeout(READ_TIMEOUT)


class TimedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        self.sock.settimeout(READ_TIMEOUT)


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return self.do_open(TimedHTTPConnection, request)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(TimedHTTPSConnection, request, context=self._context)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise urllib.error.HTTPError(request.full_url, code, "Redirect rejected", headers, fp)


def parse_response(raw, include_pagination=False):
    try:
        root = json.loads(raw)
    except (ValueError, UnicodeError, TypeError):
        raise ToolError("INVALID_RESPONSE", "키·파라미터 확인: JSON 응답이 아닙니다.") from None
    if not isinstance(root, dict):
        raise ToolError("INVALID_RESPONSE", "공공데이터 응답 형식을 확인할 수 없습니다.")
    root = root.get("response", root)
    if not isinstance(root, dict) or not isinstance(root.get("header"), dict):
        raise ToolError("INVALID_RESPONSE", "공공데이터 응답 헤더가 없습니다.")
    if root["header"].get("resultCode") != "00":
        raise ToolError("API_ERROR", "공공데이터 응답이 성공이 아닙니다. 키·파라미터를 확인해 주세요.")
    body = root.get("body")
    if not isinstance(body, dict):
        raise ToolError("INVALID_RESPONSE", "공공데이터 응답 본문이 없습니다.")
    items = body.get("items")
    if isinstance(items, dict) and "item" in items:
        items = items["item"]
    if items is None or items == "" or items == {}:
        items = []
    elif isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise ToolError("INVALID_RESPONSE", "공공데이터 items 형식을 확인할 수 없습니다.")
    if include_pagination:
        return {"items": items, "totalCount": body.get("totalCount"), "pageNo": body.get("pageNo"), "numOfRows": body.get("numOfRows")}
    return items


class PublicDataClient:
    def __init__(self, environ=None, opener=None, sleeper=None):
        environ = os.environ if environ is None else environ
        self.key = environ.get("PUBLIC_DATA_SERVICE_KEY", "").strip()
        self.base = (environ.get("PUBLIC_DATA_BASE_URL") or "https://apis.data.go.kr").rstrip("/")
        self.opener = opener if opener is not None else urllib.request.build_opener(
            _HTTPHandler(), _HTTPSHandler(), _NoRedirect())
        self.sleep = sleeper if sleeper is not None else time.sleep

    def require_credentials(self):
        if not self.key:
            raise ToolError("NO_KEY", "PUBLIC_DATA_SERVICE_KEY가 없습니다.")

    def get(self, path, parameters):
        return self._get(path, parameters, False)

    def get_page(self, path, parameters):
        return self._get(path, parameters, True)

    def _get(self, path, parameters, include_pagination):
        self.require_credentials()
        try:
            base = urllib.parse.urlsplit(self.base)
            port = base.port
        except ValueError:
            raise ToolError("INVALID_BASE_URL", "PUBLIC_DATA_BASE_URL을 확인해 주세요.") from None
        if (base.scheme not in ("http", "https") or not base.hostname or base.username or base.password
                or base.query or base.fragment or port == 0):
            raise ToolError("INVALID_BASE_URL", "PUBLIC_DATA_BASE_URL을 확인해 주세요.")
        query = urllib.parse.urlencode({"serviceKey": self.key, "type": "json", **parameters})
        request = urllib.request.Request(self.base + path + "?" + query, headers={"Accept": "application/json"})
        for attempt in range(2):
            try:
                with self.opener.open(request, timeout=CONNECT_TIMEOUT) as response:
                    raw = response.read()
            except urllib.error.HTTPError as error:
                retryable = error.code == 429 or 500 <= error.code <= 599
                code = f"HTTP_{error.code}" if isinstance(error.code, int) else "HTTP_ERROR"
                failure = ToolError(code, "공공데이터 HTTP 요청에 실패했습니다.")
                error.close()
            except (urllib.error.URLError, OSError, TimeoutError):
                retryable = True
                failure = ToolError("NETWORK_ERROR", "공공데이터에 연결하거나 응답을 읽을 수 없습니다.")
            else:
                return parse_response(raw, include_pagination)
            if attempt == 0 and retryable:
                self.sleep(0.5)
            else:
                raise failure


def candidate(row):
    if not isinstance(row, dict):
        return None
    code = str(row.get("ITEM_SEQ", "")).strip()
    name, maker = (row.get(field) for field in ("ITEM_NAME", "ENTP_NAME"))
    kinds = [row.get(field) for field in ("ETC_OTC_NAME", "ETC_OTC_CODE")
             if row.get(field) is not None and row.get(field) != ""]
    if not kinds or any(kind not in ("일반의약품", "전문의약품", "일반", "전문") for kind in kinds):
        return None
    normalized_kinds = {kind if kind.endswith("의약품") else kind + "의약품" for kind in kinds}
    if len(normalized_kinds) != 1:
        return None
    kind = normalized_kinds.pop()
    if (not re.fullmatch(r"[0-9]+", code) or not isinstance(name, str) or not name.strip()
            or not isinstance(maker, str) or not maker.strip()
            or kind not in ("일반의약품", "전문의약품", "일반", "전문")):
        return None
    return {"itemSeq": code, "itemName": name.strip(), "entpName": maker.strip(),
            "etcOtc": kind, "cancelled": row.get("CANCEL_NAME") != "정상"}


def _failed(result, error):
    result.update(status="FAILED", error={"code": error.code, "message": error.message})
    return result


def lookup(data, client):
    if (not isinstance(data, dict) or type(data.get("readable")) is not bool
            or not isinstance(data.get("medicines"), list)):
        raise ToolError("INVALID_INPUT", "readable과 medicines 목록이 필요합니다.")
    cards = []
    for medicine in data["medicines"]:
        if (not isinstance(medicine, dict) or type(medicine.get("readNo")) is not int
                or medicine["readNo"] < 1 or not isinstance(medicine.get("readName"), (str, type(None)))
                or not isinstance(medicine.get("readMaker"), (str, type(None)))):
            raise ToolError("INVALID_INPUT", "약의 readNo·readName·readMaker를 확인해 주세요.")
        card = {"readNo": medicine["readNo"], "readName": medicine.get("readName"),
                "status": "NOT_FOUND", "queries": [], "excludedCount": 0, "candidates": []}
        cards.append(card)
        try:
            client.require_credentials()
        except ToolError as error:
            _failed(card, error)
            continue
        clue = NameClue(medicine.get("readName"))
        if not data["readable"] or not clue.queryable:
            card["status"] = "NEEDS_NAME"
            continue
        maker = medicine.get("readMaker")
        for query in clue.queries():
            card["queries"].append(query)
            params = {"item_name": query, "pageNo": 1, "numOfRows": 100}
            if maker and maker.strip():
                params["entp_name"] = maker.strip()
            try:
                rows = client.get(PERMIT_PATH, params)
            except ToolError as error:
                _failed(card, error)
                break
            matches = []
            valid_rows = 0
            for row in rows:
                item = candidate(row)
                if item is None:
                    card["excludedCount"] += 1
                    continue
                valid_rows += 1
                if clue.matches(item["itemName"]):
                    matches.append(item)
            if rows and valid_rows == 0:
                _failed(card, ToolError("INVALID_ROWS", "공식 후보의 필수 정보를 확인할 수 없습니다."))
                break
            if matches:
                matches.sort(key=lambda c: (c["cancelled"], normalize(c["itemName"]) != clue.clean,
                                           not maker or normalize(c["entpName"]).casefold() != normalize(maker).casefold()))
                unique = {}
                for item in matches:
                    unique.setdefault(item["itemSeq"], item)
                card["candidates"] = list(unique.values())[:5]
                card["status"] = "PARTIAL" if card["excludedCount"] else "OK"
                break
        else:
            card["status"] = "PARTIAL" if card["excludedCount"] else "NOT_FOUND"
    return {"cards": cards}


def explain(item_seq, client):
    if not re.fullmatch(r"[0-9]+", item_seq):
        raise ToolError("INVALID_INPUT", "itemSeq: 숫자로 된 품목코드가 필요합니다.")
    result = {"itemSeq": item_seq, "status": "NOT_FOUND", "source": SOURCE, "updateDe": None}
    try:
        rows = client.get(EXPLAIN_PATH, {"itemSeq": item_seq, "pageNo": 1, "numOfRows": 1})
    except ToolError as error:
        return _failed(result, error)
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("itemName"), str) or not row["itemName"].strip():
            return _failed(result, ToolError("INVALID_RESPONSE", "공식 설명의 제품명을 확인할 수 없습니다."))
        if row.get("itemSeq") is not None and str(row["itemSeq"]) != item_seq:
            continue
        fields = ("itemName", "efcyQesitm", "useMethodQesitm", "atpnWarnQesitm", "atpnQesitm",
                  "intrcQesitm", "seQesitm", "depositMethodQesitm", "updateDe")
        result.update({field: row.get(field) for field in fields})
        result["status"] = "OK"
        break
    return result


def create_client(environ=None, *, root=None, saved_relay=False):
    settings = os.environ if environ is None else environ
    if environ is None or root is not None or saved_relay:
        if __package__:
            from .relay_config import resolve_settings
        else:
            from relay_config import resolve_settings
        settings, source = resolve_settings(ROOT if root is None else root, settings, load_env)
        if saved_relay and source != "saved":
            raise ToolError("RELAY_CONFIG_MISSING", "중계 설정을 먼저 보안 입력으로 저장해 주세요.")
    if settings.get("PUBLIC_DATA_RELAY_URL") or settings.get("PUBLIC_DATA_RELAY_TOKEN"):
        if __package__:
            from .rx_relay import RelayClient
        else:
            from rx_relay import RelayClient
        return RelayClient(settings)
    return PublicDataClient(settings)


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="input", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--explain")
    parser.add_argument("--saved-relay", action="store_true", help="저장한 중계 설정만 사용하고 없으면 중단")
    args = parser.parse_args(argv)
    if args.explain is None and (args.input is None or args.out is None):
        parser.error("조회에는 --in과 --out이 필요합니다.")
    if args.explain is not None and (args.input is not None or args.out is not None):
        parser.error("--explain과 파일 조회는 따로 실행해 주세요.")
    client = create_client(saved_relay=args.saved_relay)
    if args.explain is not None:
        return explain(args.explain, client)
    result = lookup(read_json(args.input), client)
    atomic_write_json(args.out, result)
    return result


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
