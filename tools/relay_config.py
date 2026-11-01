"""Keep a validated relay connection together between lookup processes."""

import json
import os
from pathlib import Path
import re
import stat
import urllib.parse

if __package__:
    from .common import JsonArgumentParser, ToolError, atomic_write_json, run_cli, state_lock
else:
    from common import JsonArgumentParser, ToolError, atomic_write_json, run_cli, state_lock


ROOT = Path(__file__).resolve().parents[1]
URL_VAR = "PUBLIC_DATA_RELAY_URL"
TOKEN_VAR = "PUBLIC_DATA_RELAY_TOKEN"
MAX_CONFIG = 8192


def config_path(root):
    return Path(root) / "state" / "runtime" / "rx-client" / "connection.json"


def validate_url(value):
    if (not isinstance(value, str) or len(value) > 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value):
        raise ToolError("INVALID_RELAY_URL", "중계 HTTPS 주소를 확인해 주세요.")
    value = value.strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        raise ToolError("INVALID_RELAY_URL", "중계 HTTPS 주소를 확인해 주세요.") from None
    loopback = hostname in ("127.0.0.1", "localhost", "::1")
    if (not hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path or port == 0
            or any(char.isspace() for char in hostname)
            or not (parsed.scheme == "https" or loopback and parsed.scheme == "http")):
        raise ToolError("INVALID_RELAY_URL", "중계 HTTPS 주소를 확인해 주세요.")
    return value


def validate_pair(settings):
    url = validate_url(settings.get(URL_VAR, ""))
    token = settings.get(TOKEN_VAR, "")
    if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{32,200}", token) is None:
        raise ToolError("NO_RELAY_TOKEN", "별도 중계 인증 토큰을 확인해 주세요.")
    return {URL_VAR: url, TOKEN_VAR: token}


def _check_private(info, directory=False):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise ToolError("INVALID_RELAY_CONFIG", "중계 설정 파일 형식을 확인해 주세요.")
    if os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise ToolError("RELAY_CONFIG_PERMISSIONS", "중계 설정의 소유자·접근 권한을 확인해 주세요.")


def _reject_json(_value):
    raise ValueError


def _unique_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError
        result[name] = value
    return result


def read_saved(root):
    path = config_path(root)
    try:
        directory = path.parent.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise ToolError("RELAY_CONFIG_READ_FAILED", "중계 설정을 읽을 수 없습니다.") from None
    _check_private(directory, directory=True)
    descriptor = None
    try:
        _check_private(path.lstat())
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        info = os.fstat(descriptor)
        _check_private(info)
        if info.st_size > MAX_CONFIG:
            raise ValueError
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(MAX_CONFIG + 1)
        if len(raw) > MAX_CONFIG:
            raise ValueError
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_json)
        if (not isinstance(value, dict) or set(value) != {"schemaVersion", "mode", "url", "token"}
                or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1 or value["mode"] != "relay"):
            raise ValueError
        try:
            return validate_pair({URL_VAR: value["url"], TOKEN_VAR: value["token"]})
        except ToolError:
            raise ValueError from None
    except FileNotFoundError:
        raise ToolError("RELAY_CONFIG_MISSING", "중계 설정 파일이 없습니다. 보안 입력으로 다시 설정해 주세요.") from None
    except (ValueError, UnicodeError):
        raise ToolError("INVALID_RELAY_CONFIG", "중계 설정이 손상됐습니다. 보안 입력으로 다시 설정해 주세요.") from None
    except OSError:
        raise ToolError("RELAY_CONFIG_READ_FAILED", "중계 설정을 읽을 수 없습니다.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def resolve_settings(root, environ, env_loader):
    settings = dict(environ)
    saved = read_saved(root)
    if saved is not None:
        # A saved connection is authoritative; never join half of two pairs.
        for name in (URL_VAR, TOKEN_VAR):
            value = settings.get(name)
            if value:
                normalized = validate_url(value) if name == URL_VAR else value
                if normalized != saved[name]:
                    raise ToolError("RELAY_CONFIG_CONFLICT", "실행 환경과 저장된 중계 설정이 다릅니다. 두 값을 확인해 함께 갱신해 주세요.")
        settings.update(saved)
        return settings, "saved"
    env_loader(root, settings)
    source = "environment" if settings.get(URL_VAR) or settings.get(TOKEN_VAR) else "none"
    return settings, source


def save_connection(root, environ, replace=False):
    pair = validate_pair(environ)
    path = config_path(root)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _check_private(path.parent.lstat(), directory=True)
        with state_lock(path):
            if path.exists():
                try:
                    previous = read_saved(root)
                except ToolError:
                    if not replace:
                        raise
                else:
                    if previous == pair:
                        return {"configured": True, "mode": "relay", "source": "saved", "changed": False}
                    if not replace:
                        raise ToolError("RELAY_CONFIG_EXISTS", "중계 설정이 이미 있습니다. 변경은 save --replace로 함께 적용해 주세요.")
            atomic_write_json(path, {"schemaVersion": 1, "mode": "relay", "url": pair[URL_VAR], "token": pair[TOKEN_VAR]})
            # NamedTemporaryFile creates the new POSIX file with mode 0600.
            _check_private(path.stat())
    except OSError:
        raise ToolError("RELAY_CONFIG_WRITE_FAILED", "중계 설정을 저장할 수 없습니다.") from None
    return {"configured": True, "mode": "relay", "source": "saved", "changed": True}


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("save")
    save.add_argument("--replace", action="store_true")
    commands.add_parser("status")
    args = parser.parse_args(argv)
    if args.command == "save":
        return save_connection(ROOT, os.environ, args.replace)
    if __package__:
        from .rx_lookup import load_env
    else:
        from rx_lookup import load_env
    settings, source = resolve_settings(ROOT, os.environ, load_env)
    relay = source != "none"
    if relay:
        validate_pair(settings)
    return {"configured": relay, "mode": "relay" if relay else "direct", "source": source}


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
