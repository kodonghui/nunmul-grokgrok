"""Validate public mascot assets and decorate opt-in chat notifications."""

import hashlib
from pathlib import Path
import re

if __package__:
    from .common import JsonArgumentParser, ToolError, read_json, run_cli
else:
    from common import JsonArgumentParser, ToolError, read_json, run_cli


ROOT = Path(__file__).resolve().parents[1]
FILES = {"step1": "step1-teary-v1.png", "step2": "step2-one-tear-v1.png",
         "step3": "step3-two-tears-v1.png", "taken": "taken-smile-v1.png"}
DOSE_KEY = re.compile(r"(?:[^|]+\|)?[0-9]{4}-[0-9]{2}-[0-9]{2}\|(?:[01][0-9]|2[0-3]):[0-5][0-9]")


def _invalid():
    return ToolError("INVALID_IMAGE_ASSET", "알림 이미지와 공개 설정 파일을 확인해 주세요.")


def load_catalog(root=None):
    root = Path(ROOT if root is None else root).resolve()
    try:
        manifest = read_json(root / "config" / "notification_images.json")
        if (not isinstance(manifest, dict) or set(manifest) != {"schemaVersion", "images"}
                or type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1
                or not isinstance(manifest["images"], dict) or set(manifest["images"]) != set(FILES)):
            raise _invalid()
        base = (root / "assets" / "notifications").resolve()
        if not base.is_relative_to(root):
            raise _invalid()
        catalog = {}
        for name, filename in FILES.items():
            item = manifest["images"][name]
            if (not isinstance(item, dict) or set(item) != {"file", "alt", "sha256"}
                    or item["file"] != filename or not isinstance(item["alt"], str)
                    or not 1 <= len(item["alt"]) <= 200 or not isinstance(item["sha256"], str)
                    or re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) is None):
                raise _invalid()
            path = (base / filename).resolve()
            if path.parent != base or not path.is_file() or not 24 <= path.stat().st_size <= 2_000_000:
                raise _invalid()
            raw = path.read_bytes()
            if (raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[8:16] != b"\0\0\0\rIHDR"
                    or not 128 <= int.from_bytes(raw[16:20], "big") <= 4096
                    or not 128 <= int.from_bytes(raw[20:24], "big") <= 4096
                    or hashlib.sha256(raw).hexdigest() != item["sha256"]):
                raise _invalid()
            catalog[name] = {"id": name, "path": str(path), "mimeType": "image/png",
                             "alt": item["alt"], "sha256": item["sha256"]}
        return catalog
    except (OSError, ValueError, ToolError):
        raise _invalid() from None


def attach_due(result, catalog):
    messages = []
    for message in result["send"]:
        message = dict(message)
        if (type(message.get("step")) is int and message["step"] in (1, 2, 3)
                and isinstance(message.get("key"), str) and DOSE_KEY.fullmatch(message["key"])
                and message.get("kind") in (None, "dose")):
            message["image"] = dict(catalog[f"step{message['step']}"])
        messages.append(message)
    return {**result, "send": messages}


def taken_ids(doses):
    return {key for key, record in doses.items() if record.get("state") == "TAKEN"}


def attach_mark(result, previous_taken, rows, catalog, source="chat"):
    result = dict(result)
    result.pop("image", None)
    if (source == "chat" and result.get("marked") is not None and result.get("state") == "TAKEN"
            and not result.get("replayed") and rows
            and all(record["state"] == "TAKEN" for _, record in rows)
            and any(key not in previous_taken for key, _ in rows)):
        result["image"] = dict(catalog["taken"])
    return result


def family_mark_rows(family, result):
    if result.get("marked") is None:
        return []
    if __package__:
        from .med_store import _groups
    else:
        from med_store import _groups
    profile = family["profiles"][result["profileId"]]
    key = result["marked"].removeprefix(profile["id"] + "|")
    return _groups(profile["store"]).get(key, [])


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.parse_args(argv)
    catalog = load_catalog()
    return {"count": len(catalog), "images": list(catalog.values())}


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
