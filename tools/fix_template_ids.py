# -*- coding: utf-8 -*-
"""把「导出示例-*.xlsx」模板里的示例身份证号修正为**校验位合法**的号码。

背景
----
示例数据最初是随机生成的，开启「身份证合法性校验」后这些号码不再合法，
用户照着示例填反而会被拦截。本脚本只重写身份证号一列（其余示例数据原样保留），
省份与出生日期合法时仅重算末位校验位，尽量贴近原数据。

用法：
  python tools/fix_template_ids.py <输出目录>
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from openpyxl import load_workbook                        # noqa: E402

from app.routers.applications import (                    # noqa: E402
    COMPUTER_FIELDS, MANDARIN_FIELDS)
from app.routers.export import build_workbook             # noqa: E402

PROV = {
    "11", "12", "13", "14", "15", "21", "22", "23", "31", "32", "33", "34", "35",
    "36", "37", "41", "42", "43", "44", "45", "46", "50", "51", "52", "53", "54",
    "61", "62", "63", "64", "65", "71", "81", "82", "91",
}
_W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_C = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]
_DAYS = [0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

SPECS = [
    ("computer", "导出示例-计算机类考试-报名导入模板.xlsx", COMPUTER_FIELDS),
    ("mandarin", "导出示例-普通话水平测试-考生报名模板.xlsx", MANDARIN_FIELDS),
]


def _check(pre17: str) -> str:
    return _C[sum(int(pre17[i]) * _W[i] for i in range(17)) % 11]


def _valid_date(s: str) -> bool:
    try:
        y, m, d = int(s[6:10]), int(s[10:12]), int(s[12:14])
    except ValueError:
        return False
    if y < 1900 or y > 2099 or m < 1 or m > 12 or d < 1 or d > 31:
        return False
    return d <= _DAYS[m]


def fix_id(raw, seq: int) -> str:
    """尽量保留原号码的省份与出生日期，只重算校验位。"""
    s = str(raw or "").strip().upper()
    if re.match(r"^\d{17}[\dX]$", s):
        if s[:2] in PROV and _valid_date(s):
            return s[:17] + _check(s[:17])
        # 省份或出生日期本身非法：换合法省份/日期，保留顺序号
        prov = s[:2] if s[:2] in PROV else "32"
        birth = s[6:14] if _valid_date(s) else "20030101"
        return prov + "0101" + birth + "%03d" % (seq % 1000) + _check(
            prov + "0101" + birth + "%03d" % (seq % 1000))
    # 位数不对：整体重生成
    pre = "32" + "0101" + "20030101" + "%03d" % (seq % 1000)
    return pre + _check(pre)


def read_rows(src: Path, fields: list):
    """读回 Sheet1 数据行（跳过表头），按字段顺序组装成 dict。"""
    if not src.exists():
        return []
    ws = load_workbook(src).worksheets[0]
    it = ws.iter_rows(values_only=True)
    next(it, None)
    rows = []
    for row in it:
        item = {}
        for i, f in enumerate(fields):
            v = row[i] if i < len(row) else None
            item[f] = "" if v is None else str(v)
        if any(item.values()):
            rows.append(item)
    return rows


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out.mkdir(parents=True, exist_ok=True)
    for et, fname, fields in SPECS:
        target = out / fname
        rows = read_rows(target, fields)
        fixed = 0
        for i, r in enumerate(rows):
            old = r.get("id_number", "")
            new = fix_id(old, i + 1)
            if new != str(old).strip().upper():
                fixed += 1
            r["id_number"] = new
        exam = {"id": 0, "exam_type": et, "name": target.stem}
        cnt = build_workbook(exam, rows, target, len(rows))
        print("已修正：%s（数据行 %d，重写身份证号 %d 个）" % (target.name, cnt, fixed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
