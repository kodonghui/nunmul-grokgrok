"""JSON, validation and Korean time shared by the command-line tools."""

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


try:
    SEOUL = ZoneInfo("Asia/Seoul")
except ZoneInfoNotFoundError:
    # A clean Windows installation need not ship the IANA database.
    # The demo's documented timestamps use Korean Standard Time (+09:00).
    SEOUL = timezone(timedelta(hours=9), "Asia/Seoul")


class ToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError("INVALID_ARGUMENT", message)


def parse_date(value, field="date"):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ToolError("INVALID_INPUT", f"{field}: YYYY-MM-DD 날짜가 필요합니다.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ToolError("INVALID_INPUT", f"{field}: 유효한 날짜가 필요합니다.") from None


def parse_instant(value, field="time"):
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T", value):
        raise ToolError("INVALID_INPUT", f"{field}: 시간대가 있는 ISO 8601 시각이 필요합니다.")
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(SEOUL)
    except (ValueError, OverflowError):
        raise ToolError("INVALID_INPUT", f"{field}: 시간대가 있는 ISO 8601 시각이 필요합니다.") from None


def parse_hhmm(value, field="time"):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ToolError("INVALID_INPUT", f"{field}: HH:mm 형식의 시각이 필요합니다.")
    return value


def iso(value):
    return value.astimezone(SEOUL).isoformat(timespec="seconds")


def _reject_constant(_value):
    raise ValueError("Non-finite JSON number")


def read_json(path):
    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            return json.load(stream, parse_constant=_reject_constant)
    except (ValueError, UnicodeError):
        raise ToolError("INVALID_JSON", "UTF-8 JSON 파일을 확인해 주세요.") from None
    except OSError:
        raise ToolError("READ_FAILED", "입력 파일을 읽을 수 없습니다.") from None


def atomic_write_json(path, value):
    path = Path(path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError):
        raise ToolError("WRITE_FAILED", "결과 파일을 저장할 수 없습니다.") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


@contextmanager
def state_lock(path, timeout=2):
    """Lock a read/modify/replace transaction; OS locks are released on exit."""
    lock_path = Path(str(Path(path).resolve()) + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        stream = lock_path.open("a+b")
    except OSError:
        raise ToolError("WRITE_FAILED", "상태 파일 잠금을 준비할 수 없습니다.") from None
    acquired = False
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while not acquired:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise ToolError("STATE_BUSY", "다른 명령이 복용 기록을 처리 중입니다.") from None
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def run_cli(main):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = main()
        code = 0
    except ToolError as error:
        result = {"error": {"code": error.code, "message": error.message}}
        code = 1
    except OSError:
        result = {"error": {"code": "IO_ERROR", "message": "파일 작업을 완료할 수 없습니다."}}
        code = 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code
