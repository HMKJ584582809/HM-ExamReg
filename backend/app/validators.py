# -*- coding: utf-8 -*-
"""通用合法性校验（供报名表与用户资料共用）。

原先只写在 applications.py 里，后来「用户资料也要登记证件号」时发现没法复用，
故抽成本模块：报名提交、管理员建号 / 编辑、批量照片按证件号匹配都走同一套规则，
避免「报名表校验了、用户资料没校验」的不一致。
"""
import re

from .deps import ApiError

# 证件类型：1 居民身份证 / 6 香港 / 7 澳门 / 8 台湾
ID_TYPES = ("1", "6", "7", "8")

# 居民身份证（GB 11643-1999）省份代码（前两位）
_ID_PROVINCES = {
    "11", "12", "13", "14", "15", "21", "22", "23", "31", "32", "33", "34", "35",
    "36", "37", "41", "42", "43", "44", "45", "46", "50", "51", "52", "53", "54",
    "61", "62", "63", "64", "65", "71", "81", "82", "91",
}
_ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_CHECK_CODES = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]


def check_id_type(v: str) -> str:
    v = (v or "1").strip()
    if v not in ID_TYPES:
        raise ApiError("证件类型不合法（1 居民身份证 / 6 香港 / 7 澳门 / 8 台湾）")
    return v


def _valid_id_date(s: str) -> bool:
    try:
        y, m, d = int(s[6:10]), int(s[10:12]), int(s[12:14])
    except ValueError:
        return False
    if y < 1900 or y > 2099 or m < 1 or m > 12 or d < 1 or d > 31:
        return False
    days = [0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return d <= days[m]


def _id_checksum_ok(s: str) -> bool:
    try:
        total = sum(int(s[i]) * _ID_WEIGHTS[i] for i in range(17))
    except ValueError:
        return False
    return _ID_CHECK_CODES[total % 11] == s[17].upper()


def validate_id_number(id_type: str, id_number, required: bool = True):
    """证件号码合法性校验。

    居民身份证：18 位 + 省份代码 + 出生日期 + GB 11643 校验位；
    港澳台证件：6-20 位字母数字。
    返回清洗后的号码（大写）；required=False 且传入空值时返回 ''。
    """
    it = check_id_type(id_type)
    s = (id_number or "").strip().upper()
    if not s:
        if required:
            raise ApiError("证件号码为必填项")
        return ""
    if it == "1":
        if not re.match(r"^\d{17}[\dX]$", s):
            raise ApiError("居民身份证号应为 18 位（末位可为数字或 X）")
        if s[:2] not in _ID_PROVINCES:
            raise ApiError("身份证号前两位省份代码无效，请核对")
        if not _valid_id_date(s):
            raise ApiError("身份证号中的出生日期无效，请核对")
        if not _id_checksum_ok(s):
            raise ApiError("身份证号校验位不正确，请核对后重新输入")
    else:
        if not re.match(r"^[A-Za-z0-9]{6,20}$", s):
            raise ApiError("证件号码格式不正确（应为 6-20 位字母或数字）")
    return s
