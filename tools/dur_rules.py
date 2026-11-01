"""Official DUR paths and strict pagination shared by direct and relay calls."""

import re

if __package__:
    from .common import ToolError
else:
    from common import ToolError


DUR_PATHS = {
    "INTERACTION": "/1471000/DURPrdlstInfoService03/getUsjntTabooInfoList03",
    "AGE": "/1471000/DURPrdlstInfoService03/getSpcifyAgrdeTabooInfoList03",
    "ELDERLY": "/1471000/DURPrdlstInfoService03/getOdsnAtentInfoList03",
    "PREGNANCY": "/1471000/DURPrdlstInfoService03/getPwnmTabooInfoList03",
}
DUR_LABELS = {"INTERACTION": "병용금기", "AGE": "연령금기", "ELDERLY": "노인주의", "PREGNANCY": "임부금기"}
DUR_FIELDS = ("ITEM_SEQ", "TYPE_NAME", "PROHBT_CONTENT", "MIXTURE_ITEM_SEQ", "INGR_KOR_NAME", "INGR_NAME",
              "CLASS_NAME", "CHANGE_DATE", "NOTIFICATION_DATE")


def integer(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value):
        return int(value)
    raise ToolError("INVALID_DUR_PAGE", "DUR 페이지 메타데이터를 확인할 수 없습니다.")


def validate_page(value, page_no):
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ToolError("INVALID_DUR_PAGE", "DUR 페이지 형식을 확인할 수 없습니다.")
    total = integer(value.get("totalCount"))
    if value.get("pageNo") is not None and integer(value["pageNo"]) != page_no:
        raise ToolError("INVALID_DUR_PAGE", "DUR 페이지 번호가 다릅니다.")
    if value.get("numOfRows") is not None and integer(value["numOfRows"]) != 100:
        raise ToolError("INVALID_DUR_PAGE", "DUR 페이지 크기가 다릅니다.")
    expected = min(100, max(0, total - (page_no - 1) * 100))
    if len(value["items"]) != expected:
        raise ToolError("INVALID_DUR_PAGE", "DUR 페이지에 누락되거나 중복된 행이 있습니다.")
    return {"items": value["items"], "totalCount": total, "pageNo": page_no, "numOfRows": 100}
