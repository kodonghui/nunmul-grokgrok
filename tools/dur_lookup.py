"""Official DUR lookup/evaluation, scoped to one family profile."""

from datetime import datetime
import html
import json
from pathlib import Path
import re

if __package__:
    from .common import JsonArgumentParser, SEOUL, ToolError, atomic_write_json, iso, parse_date, parse_instant, read_json, run_cli
    from .dur_rules import DUR_FIELDS, DUR_LABELS, DUR_PATHS, validate_page
    from .family_store import DEFAULT_STORE, resolve_profile, transact
    from .rx_lookup import create_client
else:
    from common import JsonArgumentParser, SEOUL, ToolError, atomic_write_json, iso, parse_date, parse_instant, read_json, run_cli
    from dur_rules import DUR_FIELDS, DUR_LABELS, DUR_PATHS, validate_page
    from family_store import DEFAULT_STORE, resolve_profile, transact
    from rx_lookup import create_client


SOURCE = "식품의약품안전처 의약품안전사용서비스(DUR) 품목정보"
NOTICE = "확인한 품목·유형·조건의 공식 조회 결과이며 모든 약의 안전성을 보장하지 않아요. 복용 여부는 의사·약사에게 확인해 주세요."
MAX_PAGES = 100


def _text(row, field):
    value = row.get(field)
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 8000:
        raise ToolError("INVALID_DUR_ROW", "DUR 공식 응답 필드를 확인할 수 없습니다.")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()


def _row(raw, code, kind):
    if not isinstance(raw, dict) or str(raw.get("ITEM_SEQ")) != code:
        raise ToolError("INVALID_DUR_ROW", "다른 품목의 DUR 행이 포함돼 있습니다.")
    label = _text(raw, "TYPE_NAME")
    aliases = {DUR_LABELS[kind], {"INTERACTION": "병용", "AGE": "연령", "ELDERLY": "노인", "PREGNANCY": "임부"}[kind]}
    if kind == "AGE":
        aliases.add("특정연령대금기")
    if label and label not in aliases:
        raise ToolError("INVALID_DUR_ROW", "DUR 유형이 요청과 다릅니다.")
    content, partner = _text(raw, "PROHBT_CONTENT"), _text(raw, "MIXTURE_ITEM_SEQ")
    ingredient = _text(raw, "INGR_KOR_NAME") or _text(raw, "INGR_NAME")
    type_only = not content and kind != "INTERACTION" and label in aliases and bool(ingredient)
    if not content and (kind != "INTERACTION" or partner) and not type_only:
        raise ToolError("INVALID_DUR_ROW", "DUR 금기·주의 내용이 없습니다.")
    if kind == "INTERACTION" and content and not re.fullmatch(r"[0-9]{1,20}", partner):
        raise ToolError("INVALID_DUR_ROW", "병용 상대 품목코드를 확인할 수 없습니다.")
    updated = _text(raw, "CHANGE_DATE") or _text(raw, "NOTIFICATION_DATE")
    if updated:
        if re.fullmatch(r"[0-9]{8}", updated):
            updated = updated[:4] + "-" + updated[4:6] + "-" + updated[6:]
        updated = parse_date(updated, "DUR.updateDate").isoformat()
    return {"partnerItemSeq": partner or None, "content": content, "updatedAt": updated or None,
            "ingredient": ingredient,
            "detailStatus": "TYPE_ONLY" if type_only else "PROVIDED" if content else "EMPTY_INTERACTION"}


def collect(code, kind, client, now):
    rows, total, seen = [], None, set()
    for page_no in range(1, MAX_PAGES + 1):
        page = validate_page(client.get_page(DUR_PATHS[kind], {"itemSeq": code, "pageNo": page_no, "numOfRows": 100}), page_no)
        if page["totalCount"] > MAX_PAGES * 100:
            raise ToolError("DUR_LIMIT", "DUR 전체 조회 상한을 초과해 확인하지 못했습니다.")
        if total is not None and page["totalCount"] != total:
            raise ToolError("INVALID_DUR_PAGE", "조회 중 DUR 전체 건수가 달라졌습니다.")
        total = page["totalCount"]
        for raw in page["items"]:
            row = _row(raw, code, kind)
            fingerprint = json.dumps(row, sort_keys=True, ensure_ascii=False)
            if fingerprint in seen:
                raise ToolError("INVALID_DUR_PAGE", "DUR 응답 행이 반복됐습니다.")
            seen.add(fingerprint)
            rows.append(row)
        if page_no * 100 >= total:
            break
    dates = [r["updatedAt"] for r in rows if r["updatedAt"] is not None]
    return {"itemSeq": code, "type": kind, "status": "OK", "totalCount": total, "rows": rows,
            "missingDetailCount": sum(row["detailStatus"] == "TYPE_ONLY" for row in rows),
            "source": SOURCE, "updatedAt": min(dates) if dates else None, "checkedAt": iso(now)}


def current_conditions(clinical, now):
    if not isinstance(clinical, dict) or clinical.get("confirmedOn") != now.date().isoformat():
        return {"ageYears": None, "ageMonths": None, "pregnant": None}
    result = {key: clinical.get(key) for key in ("ageYears", "ageMonths", "pregnant")}
    for key, maximum in (("ageYears", 130), ("ageMonths", 1560)):
        if result[key] is not None and (type(result[key]) is not int or not 0 <= result[key] <= maximum):
            raise ToolError("INVALID_INPUT", "DUR 나이 조건을 확인해 주세요.")
    if result["ageYears"] is not None and result["ageMonths"] is not None:
        raise ToolError("INVALID_INPUT", "나이는 세 또는 개월 중 하나입니다.")
    if result["pregnant"] is not None and type(result["pregnant"]) is not bool:
        raise ToolError("INVALID_INPUT", "DUR 임신 조건을 확인해 주세요.")
    return result


def _age_match(content, conditions):
    match = re.fullmatch(r"(?:만\s*)?([0-9]{1,3})\s*(세|개월)\s*(미만|이하|이상|초과)(?:\s*(?:의\s*)?(?:소아|영아|어린이|청소년|환자))?[.!]?", content)
    if not match:
        return "UNKNOWN"
    years, months = conditions["ageYears"], conditions["ageMonths"]
    if years is None and months is None:
        return "UNKNOWN"
    limit, unit, operator = int(match[1]), match[2], match[3]
    if unit == "세":
        values = [years if years is not None else months // 12]
    else:
        values = [months] if months is not None else [years * 12, years * 12 + 11]
    compare = {"미만": lambda n: n < limit, "이하": lambda n: n <= limit,
               "이상": lambda n: n >= limit, "초과": lambda n: n > limit}[operator]
    answers = {compare(value) for value in values}
    return "UNKNOWN" if len(answers) > 1 else "CONFIRMED" if answers.pop() else "NO"


def check(items, clinical, client, now, profile_id=None, profile_alias=None):
    if not isinstance(items, list):
        raise ToolError("INVALID_INPUT", "점검할 품목 목록이 필요합니다.")
    unique, unidentified = {}, []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ToolError("INVALID_INPUT", "확인한 제품명이 필요합니다.")
        code = item.get("itemSeq")
        if code is None:
            unidentified.append(item["name"])
        elif not isinstance(code, str) or not re.fullmatch(r"[0-9]{1,20}", code):
            raise ToolError("INVALID_INPUT", "확인한 숫자 품목코드 또는 null이 필요합니다.")
        else:
            unique.setdefault(code, item)
    conditions = current_conditions(clinical, now)
    years = conditions["ageYears"] if conditions["ageYears"] is not None else (
        conditions["ageMonths"] // 12 if conditions["ageMonths"] is not None else None)
    kinds = ["AGE"]
    if len(unique) > 1:
        kinds.append("INTERACTION")
    if years is None or years >= 65:
        kinds.append("ELDERLY")
    if conditions["pregnant"] is not False:
        kinds.append("PREGNANCY")
    lookups, warnings, dedupe = [], [], set()
    for code, item in unique.items():
        for kind in kinds:
            try:
                evidence = collect(code, kind, client, now)
            except ToolError as error:
                lookups.append({"itemSeq": code, "type": kind, "status": "FAILED", "error": {"code": error.code, "message": "공식 DUR의 해당 품목·유형을 확인하지 못했습니다."}, "source": SOURCE, "updatedAt": None, "checkedAt": iso(now)})
                continue
            lookups.append(evidence)
            for row in evidence["rows"]:
                if not row["content"] and row["detailStatus"] != "TYPE_ONLY":
                    continue
                codes, names, match = [code], [item["name"]], "CONFIRMED"
                if kind == "INTERACTION":
                    partner = row["partnerItemSeq"]
                    if partner == code or partner not in unique:
                        continue
                    codes = sorted([code, partner])
                    names = [unique[value]["name"] for value in codes]
                elif kind == "AGE":
                    match = _age_match(row["content"], conditions)
                elif kind == "ELDERLY":
                    match = "UNKNOWN" if years is None else "CONFIRMED" if years >= 65 else "NO"
                elif kind == "PREGNANCY":
                    match = "UNKNOWN" if conditions["pregnant"] is None else "CONFIRMED" if conditions["pregnant"] else "NO"
                if match == "NO":
                    continue
                if row["detailStatus"] == "TYPE_ONLY":
                    match = "UNKNOWN"
                fingerprint = (kind, tuple(codes), row["content"],
                               row["ingredient"] if row["detailStatus"] == "TYPE_ONLY" else None)
                if fingerprint in dedupe:
                    continue
                dedupe.add(fingerprint)
                warning = {"type": kind, "label": DUR_LABELS[kind], "itemSeqs": codes, "names": names,
                           "match": match, "content": row["content"] or None, "updatedAt": row["updatedAt"],
                           "ingredient": row["ingredient"], "detailStatus": row["detailStatus"]}
                if row["detailStatus"] == "TYPE_ONLY":
                    warning["note"] = f"공식 {DUR_LABELS[kind]} 분류가 있으나 상세 금기·주의 내용이 제공되지 않았어요."
                warnings.append(warning)
    success = sum(entry["status"] == "OK" for entry in lookups)
    expected = len(unique) * len(kinds)
    complete = expected > 0 and success == expected and not unidentified
    lookup = "NO_DRUG" if not items else "ALL" if complete else "PARTIAL" if success else "FAILED"
    result = "FOUND" if any(warning["match"] == "CONFIRMED" for warning in warnings) else (
        "UNKNOWN" if not complete or any(warning["match"] == "UNKNOWN" for warning in warnings) else "NONE")
    return {"profileId": profile_id, "profileAlias": profile_alias, "lookup": lookup, "result": result,
            "checkedTypes": kinds if unique else [], "checkedCount": success, "expectedCount": expected,
            "unidentifiedDrugNames": unidentified, "warnings": warnings,
            "sources": [{k: v for k, v in entry.items() if k != "rows"} for entry in lookups],
            "conditions": conditions, "checkedAt": iso(now), "notice": NOTICE}


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--profile")
    parser.add_argument("--in", dest="input", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--now")
    parser.add_argument("--saved-relay", action="store_true", help="저장한 중계 설정만 사용하고 없으면 중단")
    args = parser.parse_args(argv)
    if args.out and args.out.resolve() == args.store.resolve():
        raise ToolError("INVALID_ARGUMENT", "DUR 결과로 원장을 덮어쓸 수 없습니다.")
    client = create_client(saved_relay=args.saved_relay)
    now = parse_instant(args.now) if args.now else datetime.now(SEOUL)
    def snapshot(store):
        profile = resolve_profile(store, args.profile)
        return {"profileId": profile["id"], "profileAlias": profile["alias"], "clinical": profile["clinical"],
                "items": [{"itemSeq": med["itemSeq"], "name": med["name"]} for med in profile["store"]["medicines"].values() if med["deletedAt"] is None]}
    data = transact(args.store, snapshot, now, include_changes=False)
    if args.input:
        supplied = read_json(args.input)
        if not isinstance(supplied, dict) or set(supplied) != {"items"}:
            raise ToolError("INVALID_INPUT", "확인한 품목 items만 전달해 주세요.")
        data["items"] = supplied["items"]
    result = check(data["items"], data["clinical"], client, now, data["profileId"], data["profileAlias"])
    if args.out:
        atomic_write_json(args.out, result)
    return result


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
