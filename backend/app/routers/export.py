# -*- coding: utf-8 -*-
"""模块5：汇总导出（严格对齐官方模板表头与 Sheet 结构）。

大数据量策略
------------
- 行数 ≤ STREAM_THRESHOLD：常规 Workbook，保留完整样式（表头填充、边框、冻结窗格）
- 行数 >  STREAM_THRESHOLD：openpyxl write_only 流式写入 + 游标分批取数，
  内存占用与行数基本无关，1 万行以上也不会卡死。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from copy import copy as _style_copy
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from pydantic import BaseModel

from .. import db
from .. import exam_types as ET
from . import photos as PX
from .photos import (PHOTO_DIR, _SAFE_FILE, _convert_image, _export_rows, _photo_map,
                     _safe_name)
from ..config import EXPORT_DIR, PHOTO_TMP_DIR, TEMPLATE_DIR, load_dicts
from ..deps import ApiError, ok
from ..permissions import require_perms, scope_filter, scope_label
from .applications import AUDIT_LABEL, COMPUTER_FIELDS, MANDARIN_FIELDS, table_of
from .exams import TYPE_LABEL

router = APIRouter(prefix="/api/export", tags=["export"])

# 内置官方模板（打包时 --add-data 到 app/assets/templates，与 idphoto 同法）
if getattr(sys, "frozen", False):
    BUILTIN_TEMPLATE_DIR = Path(sys._MEIPASS) / "app" / "assets" / "templates"
else:
    BUILTIN_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates"

TEMPLATE_LABEL = {"computer": "报名导入模板-计算机", "mandarin": "考生报名模板-普通话"}
MAX_TEMPLATE_BYTES = 20 * 1024 * 1024
# 模板模式的行数上限：openpyxl 必须整本读入内存，超过这个量级改走流式自建模式
TEMPLATE_MAX_ROWS = 20000

HEAD_FILL = PatternFill("solid", fgColor="D9E7F5")
HEAD_FONT = Font(bold=True, size=11)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

STREAM_THRESHOLD = 3000     # 超过该行数走流式写入
BATCH_SIZE = 1000           # 流式取数批次

COMPUTER_WIDTHS = [12, 12, 10, 6, 10, 24, 30, 26, 16, 10, 14, 24, 32]
MANDARIN_WIDTHS = [10, 10, 10, 10, 24, 22, 24, 14, 14, 14, 18, 30, 30, 12, 16, 16, 16, 16, 16, 16]


class ExportIn(BaseModel):
    exam_id: int
    app_type: str = ""
    audit_status: str = "approved"   # approved / all / pending / rejected / returned
    keyword: str = ""
    # 表格来源：template=复制官方模板再填充（默认），build=系统自建表头
    table_mode: str = "template"


class BundleIn(BaseModel):
    """一键导出：表格 + 证件照打包成一个 zip。"""
    exam_id: int
    audit_status: str = "approved"
    keyword: str = ""
    table_mode: str = "template"
    with_table: int = 1
    with_photos: int = 1
    photo_fmt: str = "jpg"


# ------------------------------------------------------------ 官方模板

def _builtin_template(base: str) -> Path:
    return BUILTIN_TEMPLATE_DIR / f"{base}.xlsx"


def _custom_template(base: str) -> Path:
    return TEMPLATE_DIR / f"{base}.xlsx"


def template_path(base: str) -> Path:
    """管理员上传的覆盖版优先，其次内置模板。"""
    p = _custom_template(base)
    if p.exists():
        return p
    return _builtin_template(base)


def _norm(s: str) -> str:
    """表头归一化：去空白、全角空格与常见标点差异，便于按名称匹配。"""
    return (str(s or "").replace("\u3000", "").replace(" ", "")
            .replace("（", "(").replace("）", ")").strip())


def _locate_header(ws, names: list, max_scan: int = 8):
    """在模板里定位表头行，并按**名称**建立「表头 → 列号」映射。

    绝不能按列顺序对号入座：同一模板内数据列顺序可能和标题列不一致，
    省直辖单位还可能排到末列，按顺序写必然错位。
    """
    want = {_norm(n): n for n in names}
    best_row, best_map, best_hit = 0, {}, 0
    for row in range(1, min(max_scan, ws.max_row or 1) + 1):
        m, hit = {}, 0
        for c in range(1, (ws.max_column or 1) + 1):
            key = _norm(ws.cell(row=row, column=c).value)
            if key in want and want[key] not in m:
                m[want[key]] = c
                hit += 1
        if hit > best_hit:
            best_row, best_map, best_hit = row, m, hit
    return best_row, best_map


def build_from_template(exam: dict, rows, path: Path) -> tuple:
    """复制官方模板 → 保留表头与样式 → 从表头下一行开始填充数据。

    返回 (写入行数, 未匹配的字段名列表)。
    """
    base = ET.base_type_of(exam["exam_type"])
    src = template_path(base)
    if not src.exists():
        raise ApiError(f"未找到「{TEMPLATE_LABEL.get(base, base)}」的官方模板文件")
    fields, headers = _export_columns(exam)
    wb = load_workbook(str(src))
    ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb[wb.sheetnames[0]]
    hrow, cmap = _locate_header(ws, headers)
    if not hrow:
        raise ApiError("模板里没有找到可识别的表头行，请检查上传的文件是否正确")
    missing = [h for h in headers if h not in cmap]

    start = hrow + 1
    orig_max = ws.max_row or 0
    # 数据行样式源：优先模板自带的第一条数据行，没有就退回表头样式
    style_src = {}
    for h, col in cmap.items():
        src_cell = ws.cell(row=start, column=col) if orig_max >= start \
            else ws.cell(row=hrow, column=col)
        style_src[h] = _style_copy(src_cell._style)

    n = 0
    for r in rows:
        vals = _row_values(fields, r)
        row_idx = start + n
        for h, v in zip(headers, vals):
            col = cmap.get(h)
            if not col:
                continue
            cell = ws.cell(row=row_idx, column=col)
            cell.value = v
            if row_idx > orig_max:                     # 超出模板自带行数时补样式
                cell._style = _style_copy(style_src[h])
            cell.alignment = Alignment(horizontal="left", vertical="center")
        n += 1

    # 模板自带的空行/示例行比数据多时，把多余部分删掉，别留一堆空行给人填错
    last = start + n - 1
    if orig_max > last:
        ws.delete_rows(last + 1, orig_max - last)
    wb.save(str(path))
    return n, missing


def _style_header(ws, ncol: int, row: int = 1):
    for c in range(1, ncol + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER


def _autofit(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _wo_header(ws, headers):
    """write_only 模式下的表头行（需用 WriteOnlyCell 才能带样式）。"""
    cells = []
    for h in headers:
        c = WriteOnlyCell(ws, value=h)
        c.fill = HEAD_FILL
        c.font = HEAD_FONT
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = BORDER
        cells.append(c)
    return cells


def _row_values(fields: list, r: dict) -> list:
    """按给定字段顺序取值（列集合随考试类型的字段配置变化）。

    通用模板的自定义字段值存在 extra(JSON) 里，先展开成普通键再取值。
    """
    out = []
    raw_extra = r.get("extra")
    if isinstance(raw_extra, str) and raw_extra:
        try:
            ex = json.loads(raw_extra)
        except (ValueError, TypeError):
            ex = {}
        if isinstance(ex, dict) and ex:
            merged = dict(r)
            for k, v in ex.items():
                merged[k] = "、".join(str(x) for x in v) if isinstance(v, list) else v
            r = merged
    for f in fields:
        v = r.get(f, "")
        if f == "id_type" and not v:
            v = "1"
        out.append(v)
    return out


def _export_columns(exam: dict):
    """导出的字段列表与表头。

    - 内置模板（无字段配置）：表头与顺序**完全沿用官方模板**，保证与官方文件逐格一致
    - 自定义类型：去掉被关闭采集的列，并应用管理员改的显示名
    """
    code = exam["exam_type"]
    et = ET.base_type_of(code)
    cfg = ET.config_of(code)
    fields = list(ET.fields_of(code))
    headers = [(cfg.get(f) or {}).get("label") or ET.default_label(et, f) for f in fields]
    # 通用模板没有官方字典：表头就是字段显示名；自定义字段追加在末尾
    if et == "generic":
        for cf in ET.custom_fields_of(code):
            fields.append(cf["key"])
            headers.append(cf["label"])
        return fields, headers
    official = load_dicts()[et]["headers"]
    allf = ET.BASE_FIELDS[et]
    if len(official) != len(allf):        # 字典异常时不冒险改动，原样输出
        return list(allf), list(official)
    official_map = dict(zip(allf, official))
    headers = []
    for f in fields:
        lb = (cfg.get(f) or {}).get("label")
        headers.append(lb or official_map.get(f) or ET.default_label(et, f))
    return fields, headers


def _widths_of(et: str, fields: list) -> list:
    """列宽跟随实际导出的列（关闭采集的字段不再占位）。"""
    if et == "generic":
        return [18] * len(fields)
    allw = COMPUTER_WIDTHS if et == "computer" else MANDARIN_WIDTHS
    allf = ET.BASE_FIELDS[et]
    if len(allw) != len(allf):
        return allw
    m = dict(zip(allf, allw))
    return [m.get(f, 14) for f in fields]


def _grid_append(ws, grid: list):
    """按二维网格逐行 append（write_only 模式仅支持 append）。"""
    for row in grid:
        ws.append(row)


def _aux_computer(wb, write_only: bool = False):
    d = load_dicts()["computer"]
    ws2 = wb.create_sheet("填表说明")
    for line in d["instructions"]:
        ws2.append([line])
    ws2.column_dimensions["A"].width = 120
    if not write_only:
        for row in ws2.iter_rows():
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")

    ws3 = wb.create_sheet("sheet3")
    n = max(len(d["orgs"]), len(d["subjects"]))
    # 官方模板第 1 行留空，表头在第 2 行，此处保持一致
    grid = [[None] * 5, [None, "机构编码", "考试机构名称", None, "报考科目"]]
    for i in range(n):
        o = d["orgs"][i] if i < len(d["orgs"]) else None
        s = d["subjects"][i] if i < len(d["subjects"]) else None
        grid.append([None, o["code"] if o else None, o["name"] if o else None, None, s])
    _grid_append(ws3, grid)
    ws3.column_dimensions["B"].width = 12
    ws3.column_dimensions["C"].width = 40
    ws3.column_dimensions["E"].width = 46


def _aux_mandarin(wb, write_only: bool = False):
    d = load_dicts()["mandarin"]
    ws2 = wb.create_sheet("Sheet2")
    for o in d["occupations"]:
        ws2.append([o])
    ws2.column_dimensions["A"].width = 30

    # Sheet3：复刻官方「省级 / 市级 / 县区级」错列排布
    ws3 = wb.create_sheet("Sheet3")
    layout = []          # [(start_col, region, 常规城市)]
    standalone = []      # 官方模板单独排在末列的省直辖单位（如陕西「杨凌示范区」）
    col = 2
    for region in d["regions"]:
        normal = [c for c in region["cities"] if not c.get("standalone")]
        standalone += [c for c in region["cities"] if c.get("standalone")]
        layout.append((col, region, normal))
        col = (col + 1 + len(normal)) if normal else (col + 2)
    total_cols = col - 1
    standalone_col = 0
    if standalone:
        standalone_col = col
        total_cols = col

    max_rows = 3
    for _c, region, normal in layout:
        # 市级列纵向罗列该省全部城市（含单独排布的省直辖单位）
        max_rows = max(max_rows, 2 + len(region["cities"]))
        for city in normal:
            max_rows = max(max_rows, 3 + len(city["counties"]))
    for city in standalone:
        max_rows = max(max_rows, 3 + len(city["counties"]))
    # 第 1 列还需要纵向罗列全部省级名称
    max_rows = max(max_rows, 2 + len(d["provinces"]))

    grid = [[None] * total_cols for _ in range(max_rows)]
    grid[0][0] = "省级"
    grid[1][0] = ("注:此表是考生报名参考模板系统表,如需修改请慎重操作,"
                  "如有疑问,请联系管理员,或者重新下载此模板！")
    for start, region, normal in layout:
        prov = region["province"]
        grid[0][start - 1] = f"市级（{prov}）"
        if start < total_cols:
            grid[0][start] = "县区级" if start == 2 else f"县区级（{prov}）"
        # 市级列：纵向罗列该省城市（按模板市级列原始顺序，含省直辖单位）
        for i, c in enumerate(region["cities"]):
            if 2 + i < max_rows:
                grid[2 + i][start - 1] = c["name"]
        # 县区级列：每个城市一列，第 3 行为城市名，其下为该市所辖县区。
        # 按模板原始列序（src_col）排列——个别省份数据列顺序与市级列并不一致
        # （如辽宁省「铁岭市 / 朝阳市」顺序颠倒），按市级列顺序写会与原表不符。
        ordered = sorted(normal, key=lambda c: c.get("src_col") or 0)
        county_col = start + 1
        for city in ordered:
            grid[2][county_col - 1] = city["name"]
            for i, county in enumerate(city["counties"]):
                if 3 + i < max_rows:
                    grid[3 + i][county_col - 1] = county
            county_col += 1
    # 独立块（官方模板排在末列，无第 1 行标题）
    if standalone_col:
        for city in standalone:
            grid[2][standalone_col - 1] = city["name"]
            for i, county in enumerate(city["counties"]):
                if 3 + i < max_rows:
                    grid[3 + i][standalone_col - 1] = county
    for i, p in enumerate(d["provinces"]):
        if 2 + i < max_rows:
            grid[2 + i][0] = p
    _grid_append(ws3, grid)
    ws3.column_dimensions["A"].width = 20

    ws4 = wb.create_sheet("Sheet4")
    ws4.append(["模板版本"])
    ws4.append([d["version"]])


def build_workbook(exam: dict, rows, path: Path, total: int = 0):
    """生成导出文件。rows 为可迭代对象；total 仅用于选择写入策略。"""
    et = ET.base_type_of(exam["exam_type"])
    fields, headers = _export_columns(exam)
    widths = _widths_of(et, fields)
    stream = total > STREAM_THRESHOLD

    if not stream:
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(headers)
        n = 0
        for r in rows:
            ws.append(_row_values(fields, r))
            n += 1
        _style_header(ws, len(headers))
        _autofit(ws, widths)
        ws.freeze_panes = "A2"
        if et == "computer":
            _aux_computer(wb, False)
        elif et == "mandarin":
            _aux_mandarin(wb, False)
        wb.save(str(path))
        return n

    # 流式：先固定列宽/冻结窗格，再逐行 append，内存恒定
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Sheet1")
    _autofit(ws, widths)
    ws.freeze_panes = "A2"
    ws.append(_wo_header(ws, headers))
    n = 0
    for r in rows:
        ws.append(_row_values(fields, r))
        n += 1
    if et == "computer":
        _aux_computer(wb, True)
    elif et == "mandarin":
        _aux_mandarin(wb, True)
    wb.save(str(path))
    return n


def _count(exam_id: int, exam_type: str, audit_status: str, keyword: str, user) -> int:
    tbl = table_of(exam_type)
    where, params = ["exam_id=?"], [exam_id]
    if audit_status and audit_status != "all":
        where.append("audit_status=?")
        params.append(audit_status)
    if keyword:
        where.append("(name LIKE ? OR id_number LIKE ?)")
        params += [f"%{keyword}%"] * 2
    frag, sparams = scope_filter(user, tbl)
    return db.query_one(f"SELECT COUNT(*) c FROM {tbl} WHERE {' AND '.join(where)}{frag}",
                        tuple(params + sparams))["c"]


def _collect(exam_id: int, exam_type: str, audit_status: str, keyword: str, user,
             limit: int = 0):
    tbl = table_of(exam_type)
    where, params = ["exam_id=?"], [exam_id]
    if audit_status and audit_status != "all":
        where.append("audit_status=?")
        params.append(audit_status)
    if keyword:
        where.append("(name LIKE ? OR id_number LIKE ?)")
        params += [f"%{keyword}%"] * 2
    # user 必传：不给默认值，漏传会直接 TypeError，而不是静默导出全量数据
    frag, sparams = scope_filter(user, tbl)
    sql = f"SELECT * FROM {tbl} WHERE {' AND '.join(where)}{frag} ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return db.query(sql, tuple(params + sparams))


def _collect_iter(exam_id: int, exam_type: str, audit_status: str, keyword: str,
                  user, batch: int = BATCH_SIZE):
    """游标分批取数，避免一次性把十万行读进内存。"""
    tbl = table_of(exam_type)
    where, params = ["exam_id=?"], [exam_id]
    if audit_status and audit_status != "all":
        where.append("audit_status=?")
        params.append(audit_status)
    if keyword:
        where.append("(name LIKE ? OR id_number LIKE ?)")
        params += [f"%{keyword}%"] * 2
    # 同上：user 必传
    frag, sparams = scope_filter(user, tbl)
    sql = f"SELECT * FROM {tbl} WHERE {' AND '.join(where)}{frag} ORDER BY id"
    cur = db.get_conn().execute(sql, tuple(params + sparams))
    try:
        while True:
            chunk = cur.fetchmany(batch)
            if not chunk:
                break
            for r in chunk:
                yield dict(r)
    finally:
        cur.close()


def _make_file(exam_id: int, app_type: str, audit_status: str, keyword: str, user,
               table_mode: str = "template"):
    """生成汇总表。

    table_mode=``template``：复制官方模板文件后填充（表头、列顺序、下拉、说明页
    全部沿用官方文件，拿到就能上报）；``build``：系统自建表头（旧方式，保留为备选）。
    模板模式要一次性读全量数据（openpyxl 不支持流式写已有文件），
    因此超过 BATCH 上限时给出明确提示，而不是硬撑到内存爆掉。
    """
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)
    if app_type and app_type != ET.base_type_of(exam["exam_type"]):
        raise ApiError("导出类型与考试批次类型不一致")
    base = ET.base_type_of(exam["exam_type"])
    total = _count(exam_id, base, audit_status, keyword, user)
    if not total:
        raise ApiError("当前筛选条件下没有可导出的报名数据")

    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    label = {"approved": "已通过", "all": "全部", "pending": "待审核",
             "rejected": "已驳回", "returned": "已退回"}.get(audit_status, audit_status)
    use_tpl = table_mode == "template" and base in TEMPLATE_LABEL
    if table_mode == "template" and not use_tpl:
        table_mode = "build"        # 通用类型没有官方模板，自动退回自建
    # 文件名直接说明「用哪种方式生成的」，避免同名文件分不清来源
    suffix = ({"computer": "报名导入模板-计算机", "mandarin": "考生报名模板-普通话",
               "generic": "报名汇总表-通用"}.get(base, "报名汇总表") if use_tpl
              else {"computer": "汇总表-计算机", "mandarin": "汇总表-普通话",
                    "generic": "汇总表-通用"}.get(base, "汇总表"))
    filename = f"{exam['name']}-{label}-{suffix}-{ts}.xlsx"
    filename = filename.replace("/", "_").replace("\\", "_").replace(":", "_")
    path = EXPORT_DIR / filename

    if use_tpl:
        if total > TEMPLATE_MAX_ROWS:
            raise ApiError(
                f"当前要导出 {total} 行，超过模板模式上限 {TEMPLATE_MAX_ROWS} 行。"
                "模板模式需要把官方文件整体读入内存再写盘，行数过多会卡死；"
                "请改用「自建表格」模式，或按班级 / 审核状态分批导出。")
        rows = _collect(exam_id, base, audit_status, keyword, user)
        written, missing = build_from_template(exam, rows, path)
        return path, filename, written, missing

    rows = _collect_iter(exam_id, base, audit_status, keyword, user)
    written = build_workbook(exam, rows, path, total)
    return path, filename, written, []


def _zf_write(zf, rel: str, data: bytes):
    """写一条 zip 记录，中文名必须打 UTF-8 标记，否则 Windows 解压乱码。"""
    zi = zipfile.ZipInfo(rel, date_time=time.localtime()[:6])
    zi.flag_bits |= 0x800
    zi.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(zi, data)


def _xlsx_response(tup):
    path, filename, count, missing = tup
    headers = {"X-Exported-Count": str(count)}
    if missing:
        # 模板里找不到的列：明确告诉用户，避免「导出成功却少了几列」没人发现
        headers["X-Template-Missing"] = quote("、".join(missing))
    return FileResponse(
        str(path), filename=filename, headers=headers,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.post("")
def export(body: ExportIn, user=Depends(require_perms("export"))):
    tup = _make_file(body.exam_id, body.app_type, body.audit_status,
                     body.keyword, user, (body.table_mode or "template"))
    return _xlsx_response(tup)


@router.get("/preview")
def preview(exam_id: int = Query(...), audit_status: str = "approved",
            keyword: str = "", user=Depends(require_perms("export"))):
    """导出预览：keyword 必须与导出接口一致，否则预览不过滤、导出却过滤。"""
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)
    base = ET.base_type_of(exam["exam_type"])
    total = _count(exam_id, base, audit_status, keyword, user)
    rows = _collect(exam_id, base, audit_status, keyword, user, limit=20)
    fields, headers = _export_columns(exam)
    preview_rows = []
    for r in rows:
        item = []
        for h, f in zip(headers, fields):
            if f == "org_code":
                item.append(f"{r.get('org_code','')} / {r.get('org_name','')}")
            else:
                item.append(r.get(f, ""))
        preview_rows.append(item)
    return ok({
        "exam_name": exam["name"],
        "exam_type": exam["exam_type"],
        "exam_type_label": TYPE_LABEL[exam["exam_type"]],
        "headers": headers,
        "rows": preview_rows,
        "total": total,
        "keyword": keyword,
        "audit_status": audit_status,
        "audit_status_label": "全部" if audit_status == "all" else AUDIT_LABEL.get(audit_status, ""),
        "scope_label": scope_label(user),
        "stream_mode": total > STREAM_THRESHOLD,
    })


@router.get("/download")
def download(exam_id: int = Query(...), audit_status: str = "approved", keyword: str = "",
             table_mode: str = "template", user=Depends(require_perms("export"))):
    tup = _make_file(exam_id, "", audit_status, keyword, user, table_mode or "template")
    return _xlsx_response(tup)


# ------------------------------------------------------------ 模板管理

def _probe_template(base: str) -> dict:
    """检查某类模板是否存在、来源、以及表头能匹配上多少列。"""
    p = template_path(base)
    info = {"base": base, "label": TEMPLATE_LABEL.get(base, base), "exists": p.exists(),
            "source": "custom" if _custom_template(base).exists() else "builtin",
            "path": str(p), "updated_at": "", "columns": 0, "header_row": 0,
            "matched": [], "missing": []}
    if not p.exists():
        return info
    st = p.stat()
    info["updated_at"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    try:
        wb = load_workbook(str(p), read_only=True)
        ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb[wb.sheetnames[0]]
        fields, headers = _export_columns({"exam_type": base})
        hrow, cmap = _locate_header(ws, headers)
        info["header_row"] = hrow
        info["columns"] = ws.max_column or 0
        info["matched"] = [h for h in headers if h in cmap]
        info["missing"] = [h for h in headers if h not in cmap]
        wb.close()
    except Exception as e:
        info["error"] = f"模板读取失败：{type(e).__name__}: {e}"
    return info


@router.get("/templates")
def templates(user=Depends(require_perms("export"))):
    """当前生效的官方模板：来源（内置 / 管理员上传）、表头匹配情况。"""
    return ok({"items": [_probe_template(b) for b in ("computer", "mandarin")],
               "dir": str(TEMPLATE_DIR), "max_rows": TEMPLATE_MAX_ROWS})


@router.post("/template/upload")
async def upload_template(base: str = Query(...), file: UploadFile = File(...),
                          user=Depends(require_perms("system_manage"))):
    """上传覆盖版官方模板（按类型上传；之后导出一律以它为准）。

    改的是全系统导出基准，属于系统维护类操作，不能只凭「能导出」就放行。
    """
    if base not in TEMPLATE_LABEL:
        raise ApiError("模板类型不合法（computer / mandarin）")
    name = (file.filename or "").lower()
    if not name.endswith(".xlsx"):
        raise ApiError("请上传 .xlsx 格式的模板文件")
    data = await file.read()
    if not data:
        raise ApiError("文件内容为空")
    if len(data) > MAX_TEMPLATE_BYTES:
        raise ApiError(f"模板文件不能超过 {MAX_TEMPLATE_BYTES // 1024 // 1024}MB")
    tmp = TEMPLATE_DIR / f".upload_{base}.tmp"
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        wb = load_workbook(str(tmp), read_only=True)
        if "Sheet1" not in wb.sheetnames:
            raise ApiError("模板里必须包含名为 Sheet1 的工作表")
        ws = wb["Sheet1"]
        fields, headers = _export_columns({"exam_type": base})
        hrow, cmap = _locate_header(ws, headers)
        wb.close()
        if not hrow:
            raise ApiError("没找到表头行：请确认 Sheet1 第一行是该类型的官方表头")
        if len(cmap) < len(headers) // 2:
            raise ApiError(f"只识别到 {len(cmap)}/{len(headers)} 个字段，"
                           "请确认上传的是本类型的官方模板")
    except ApiError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise ApiError(f"模板文件无法解析：{type(e).__name__}")
    shutil.move(str(tmp), str(_custom_template(base)))
    return ok(_probe_template(base), "模板已更新，后续导出以新模板为准")


@router.post("/template/reset")
def reset_template(base: str = Query(...), user=Depends(require_perms("system_manage"))):
    """恢复为内置官方模板（删除管理员上传的覆盖版）。"""
    if base not in TEMPLATE_LABEL:
        raise ApiError("模板类型不合法（computer / mandarin）")
    _custom_template(base).unlink(missing_ok=True)
    return ok(_probe_template(base), "已恢复为内置官方模板")


# ------------------------------------------------------------ 一键导出（表格 + 照片）

def _bundle_rows(exam: dict, exam_id: int, audit_status: str, user) -> list:
    return _export_rows(exam_id, ET.base_type_of(exam["exam_type"]), audit_status, user)


@router.post("/bundle")
def bundle(body: BundleIn, user=Depends(require_perms("export"))):
    """一键导出：把「汇总表 + 证件照」打成一个 zip，一次下载齐。"""
    exam_id = int(body.exam_id or 0)
    if not exam_id:
        raise ApiError("请选择考试批次")
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)
    base = ET.base_type_of(exam["exam_type"])
    audit = body.audit_status or "approved"
    with_table = int(body.with_table or 0) == 1
    with_photos = int(body.with_photos or 0) == 1
    if not (with_table or with_photos):
        raise ApiError("请至少选择一项导出内容（汇总表 / 证件照）")

    fmt = (body.photo_fmt or "jpg").strip().lower()
    fmt = "jpg" if fmt == "jpeg" else fmt
    if fmt not in ("jpg", "png"):
        raise ApiError("照片格式只能是 JPG 或 PNG")

    total = _count(exam_id, base, audit, body.keyword, user)
    if not total:
        raise ApiError("当前筛选条件下没有可导出的报名数据")

    root = _safe_name(exam["name"], f"考试{exam_id}")
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    PHOTO_TMP_DIR.mkdir(parents=True, exist_ok=True)
    table_path = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="bundle", suffix=".zip", dir=str(PHOTO_TMP_DIR))
        os.close(fd)
        photo_done, missing_photo, used = 0, [], set()
        table_name, table_count, table_missing = "", 0, []

        if with_photos and total > PX.MAX_EXPORT:
            raise ApiError(f"单次最多打包 {PX.MAX_EXPORT} 条，请按班级或审核状态分批导出")

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if with_table:
                table_path, table_name, table_count, table_missing = _make_file(
                    exam_id, "", audit, body.keyword, user, body.table_mode or "template")
                _zf_write(zf, f"{root}/汇总表/{table_name}", table_path.read_bytes())

            if with_photos:
                rows = _bundle_rows(exam, exam_id, audit, user)
                umap = _photo_map([r["user_id"] for r in rows])
                for r in rows:
                    uid = r["user_id"] or 0
                    u = umap.get(uid)
                    fname = (u or {}).get("photo") or ""
                    idno = (r["id_number"] or "").strip()
                    college = _safe_name(r["college"])
                    klass = _safe_name(r["class_name"])
                    if not uid:
                        missing_photo.append([r["name"], idno, college, klass,
                                              "未关联账号（导入时未建号）"])
                        continue
                    if not fname or not _SAFE_FILE.match(fname):
                        missing_photo.append([r["name"], idno, college, klass, "未上传证件照"])
                        continue
                    src = PHOTO_DIR / fname
                    if not src.exists():
                        missing_photo.append([r["name"], idno, college, klass, "证件照文件已丢失"])
                        continue
                    bid = _safe_name(idno, default="") or f"u{uid}"
                    rel = f"{root}/证件照/{college}/{klass}/{bid}.{fmt}"
                    n = 2
                    while rel.lower() in used:
                        rel = f"{root}/证件照/{college}/{klass}/{bid}_{n}.{fmt}"
                        n += 1
                    used.add(rel.lower())
                    try:
                        _zf_write(zf, rel, _convert_image(src.read_bytes(), fmt))
                    except Exception as e:
                        missing_photo.append([r["name"], idno, college, klass,
                                              f"格式转换失败：{e}"])
                        continue
                    photo_done += 1

            lines = [
                "一键导出说明",
                f"考试批次：{exam['name']}",
                f"导出时间：{db.now_str()}",
                f"筛选状态：{AUDIT_LABEL.get(audit, audit)}",
                f"数据范围：{scope_label(user)}",
                "",
            ]
            if with_table:
                lines.append(f"汇总表：{table_name}（{table_count} 行，"
                             + ("复制官方模板填充" if body.table_mode != "build" else "系统自建表格")
                             + "）")
                if table_missing:
                    lines.append("  注意：模板里没有找到这些列，导出表中不含它们："
                                 + "、".join(table_missing))
            if with_photos:
                lines.append(f"证件照：{photo_done} 张，格式 {fmt.upper()}，"
                             f"目录 {root}/证件照/院系/班级/证件号码.{fmt}")
                if missing_photo:
                    lines.append("")
                    lines.append(f"未导出照片 {len(missing_photo)} 人"
                                 "（姓名 / 证件号码 / 院系 / 班级 / 原因）：")
                    for m in missing_photo:
                        lines.append("  " + " / ".join(str(x) for x in m))
            _zf_write(zf, f"{root}/_导出说明.txt", "\n".join(lines).encode("utf-8-sig"))

        name = f"{root}-一键导出-{ts}.zip"

        def _iter():
            try:
                with open(tmp_path, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        yield chunk
            finally:
                for p in (tmp_path, table_path):
                    if p:
                        try:
                            os.unlink(str(p))
                        except OSError:
                            pass

        return StreamingResponse(
            _iter(), media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=utf-8''{quote(name)}",
                     "X-Table-Count": str(table_count),
                     "X-Photo-Count": str(photo_done),
                     "X-Missing-Photo": str(len(missing_photo))})
    except Exception:
        for p in (tmp_path, table_path):
            if p:
                try:
                    os.unlink(str(p))
                except OSError:
                    pass
        raise


@router.get("/files")
def files(user=Depends(require_perms("export"))):
    items = []
    for p in sorted(EXPORT_DIR.glob("*.xlsx"), key=lambda x: x.stat().st_mtime, reverse=True)[:30]:
        items.append({"name": p.name, "size": p.stat().st_size,
                      "created_at": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
    return ok(items)


@router.get("/exam-options")
def exam_options(user=Depends(require_perms("export"))):
    """可导出批次下拉（含当前账号范围内的可导出条数）。"""
    rows = db.query("SELECT id,name,exam_type,exam_year,exam_month,status FROM exams"
                    " ORDER BY exam_year DESC, exam_month DESC, id DESC")
    out = []
    for r in rows:
        tbl = table_of(ET.base_type_of(r["exam_type"]))
        frag, sparams = scope_filter(user, tbl)
        c = db.query_one(f"SELECT COUNT(*) c FROM {tbl} WHERE exam_id=? AND audit_status='approved'"
                         f"{frag}", tuple([r["id"]] + sparams))["c"]
        r["label"] = f"{r['name']}（{TYPE_LABEL[r['exam_type']]}）"
        r["exportable"] = c
        out.append(r)
    return ok(out)
