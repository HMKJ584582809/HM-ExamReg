# -*- coding: utf-8 -*-
"""大数据量导出性能验证（开发提示词模块5要求：>1 万行不卡死、分批生成）。

用法
----
    # 仅做内存/耗时基准（不依赖服务端）
    python tools/perf_export.py

    # 端到端：先启动服务，再指定地址与行数
    python tools/perf_export.py http://127.0.0.1:8791 12000

验证内容
--------
1. 直接构造 12000 行数据，走 build_workbook 流式分支，用 tracemalloc 测峰值内存；
2. 校验输出文件的 Sheet1 行数 = 数据行数 + 1（表头）；
3. 校验辅助 Sheet（填表说明 / sheet3 / Sheet2 / Sheet3 / Sheet4）结构完整；
4. 端到端：调用导出接口计时，并校验响应体大小与行数。
"""
import io
import json
import os
import sqlite3
import sys
import time
import tracemalloc
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
DB_PATH = ROOT / "data" / "app.db"

os.environ.setdefault("EXAM_SEED_DEMO", "1")
os.environ.setdefault("EXAM_DEV", "1")
sys.path.insert(0, str(BACKEND))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + ("" if cond else f"  -> {detail}"))


# ------------------------------------------------------------------ 基准测试

def bench_inprocess(n: int):
    """在进程内构造 n 行数据并导出，测峰值内存与耗时。"""
    from openpyxl import load_workbook
    from app import db
    from app.config import EXPORT_DIR, ensure_dirs
    from app.routers import export as exp

    ensure_dirs()
    exam = {"id": 999999, "name": "性能基准-计算机类考试", "exam_type": "computer"}

    def row_gen():
        for i in range(n):
            yield {
                "org_code": "1001", "exam_site_code": "100101", "name": f"压力测试{i:06d}",
                "gender": "男" if i % 2 else "女", "id_type": "1",
                "id_number": f"32010220{i:010d}", "subject": "计算机一级",
                "school": "计算机学院", "class_name": f"计算机21{i % 40:02d}",
                "education": "本科", "phone": f"139{i:08d}",
                "email": f"perf{i}@exam.local", "address": f"江苏省南京市鼓楼区{i}号",
            }

    out = EXPORT_DIR / "_perf_computer.xlsx"
    tracemalloc.start()
    t0 = time.time()
    written = exp.build_workbook(exam, row_gen(), out, total=n)
    elapsed = time.time() - t0
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    size = out.stat().st_size
    print(f"    流式导出 {n} 行：耗时 {elapsed:.2f}s，峰值内存 {peak / 1024 / 1024:.1f} MB，"
          f"文件 {size / 1024 / 1024:.1f} MB")
    check(f"流式导出写入 {n} 行", written == n, written)
    check("流式导出峰值内存 < 120MB", peak / 1024 / 1024 < 120, f"{peak / 1024 / 1024:.1f} MB")
    check("流式导出耗时 < 60s", elapsed < 60, f"{elapsed:.2f}s")
    check("触发了流式分支（>3000 行）", n > exp.STREAM_THRESHOLD, n)

    wb = load_workbook(str(out), read_only=True)
    ws = wb["Sheet1"]
    rows = sum(1 for _ in ws.iter_rows(values_only=True))
    sheets = wb.sheetnames
    wb.close()
    check("输出 Sheet1 行数 = 数据行数 + 表头", rows == n + 1, rows)
    check("计算机模板辅助 Sheet 完整", sheets == ["Sheet1", "填表说明", "sheet3"], sheets)
    try:
        out.unlink()
    except OSError:
        pass
    return elapsed


def bench_inprocess_mandarin(n: int):
    """普通话模板（20 列 + 复杂 Sheet3）流式导出。"""
    from openpyxl import load_workbook
    from app.config import EXPORT_DIR, ensure_dirs
    from app.routers import export as exp

    ensure_dirs()
    exam = {"id": 999998, "name": "性能基准-普通话水平测试", "exam_type": "mandarin"}

    def row_gen():
        for i in range(n):
            yield {
                "name": f"普通话{i:06d}", "gender": "女", "ethnicity": "汉族", "id_type": "1",
                "id_number": f"32010220{i:010d}", "occupation": "学生", "employer": "计算机学院",
                "phone": f"138{i:08d}", "student_no": f"2022{i:05d}",
                "class_name": f"计算机21{i % 40:02d}", "department": "计算机学院",
                "contact_address": "江苏省南京市鼓楼区1号", "mail_address": "江苏省南京市鼓楼区1号",
                "postcode": "210000", "birth_province": "江苏省", "birth_city": "南京市",
                "birth_county": "鼓楼区", "live_province": "江苏省", "live_city": "南京市",
                "live_county": "鼓楼区",
            }

    out = EXPORT_DIR / "_perf_mandarin.xlsx"
    tracemalloc.start()
    t0 = time.time()
    written = exp.build_workbook(exam, row_gen(), out, total=n)
    elapsed = time.time() - t0
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"    普通话流式导出 {n} 行：耗时 {elapsed:.2f}s，峰值内存 {peak / 1024 / 1024:.1f} MB")
    check(f"普通话流式导出写入 {n} 行", written == n, written)
    check("普通话流式导出峰值内存 < 140MB", peak / 1024 / 1024 < 140, f"{peak / 1024 / 1024:.1f} MB")

    wb = load_workbook(str(out), read_only=True)
    sheets = wb.sheetnames
    ws = wb["Sheet1"]
    rows = sum(1 for _ in wb["Sheet1"].iter_rows(values_only=True))
    wb.close()
    check("普通话输出行数正确", rows == n + 1, rows)
    check("普通话模板 Sheet 结构完整",
          sheets == ["Sheet1", "Sheet2", "Sheet3", "Sheet4"], sheets)
    try:
        out.unlink()
    except OSError:
        pass


# ------------------------------------------------------------------ 端到端

def call(method, path, token=None, body=None, raw=False, timeout=300):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = r.read()
            if raw:
                return r.status, content, dict(r.headers)
            return r.status, json.loads(content.decode("utf-8")), dict(r.headers)
    except urllib.error.HTTPError as e:
        content = e.read()
        try:
            return e.code, json.loads(content.decode("utf-8")), dict(e.headers)
        except Exception:
            return e.code, {"raw": content[:200].decode("utf-8", "ignore")}, dict(e.headers)


def bench_e2e(n: int):
    """端到端：直连 SQLite 灌入 n 行，再调用导出接口计时。"""
    from openpyxl import load_workbook

    st, r, _ = call("POST", "/api/auth/login",
                    body={"account": "admin", "password": "Admin@123", "remember": True})
    if st != 200:
        check("端到端：管理员登录", False, r)
        return
    admin = r["data"]["access_token"]
    check("端到端：管理员登录", True)

    st, r, _ = call("GET", "/api/exams?exam_type=computer&page_size=50", token=admin)
    comp = [e for e in r["data"]["list"] if e["exam_type"] == "computer"]
    if not comp:
        check("端到端：存在计算机类批次", False, "无批次，请先启动服务并确保有计算机类考试")
        return
    exam_id = comp[0]["id"]
    check("端到端：存在计算机类批次", True)

    # 直连 SQLite 批量灌数据（WAL 模式支持并发写）
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("DELETE FROM applications_computer WHERE exam_id=? AND id_number LIKE '9999%'",
                 (exam_id,))
    conn.commit()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    cols = ("exam_id,user_id,org_code,org_name,exam_site_code,name,gender,id_type,id_number,subject,"
            "school,class_name,education,phone,email,address,audit_status,created_at,updated_at")
    rows = [(exam_id, -(i + 1), "1001", "计算机考试中心", "100101", f"压力测试{i:06d}", "男", "1",
             f"9999{i:012d}", "计算机一级", "计算机学院", f"计算机21{i % 40:02d}", "本科",
             f"137{i:08d}", f"perf{i}@exam.local", f"江苏省南京市鼓楼区{i}号",
             "approved", ts, ts) for i in range(n)]
    t0 = time.time()
    conn.executemany(f"INSERT INTO applications_computer({cols}) VALUES({','.join(['?'] * 19)})", rows)
    conn.commit()
    insert_elapsed = time.time() - t0
    conn.close()
    print(f"    灌入 {n} 行耗时 {insert_elapsed:.2f}s")

    st, r, _ = call("GET", f"/api/export/preview?exam_id={exam_id}&audit_status=approved",
                    token=admin)
    total = r["data"]["total"]
    check("端到端：预览返回全量条数", total >= n, total)
    check("端到端：自动切换流式模式", r["data"].get("stream_mode") is True, r["data"].get("stream_mode"))

    t0 = time.time()
    st, content, hdrs = call("GET", f"/api/export/download?exam_id={exam_id}&audit_status=approved",
                             token=admin, raw=True, timeout=600)
    elapsed = time.time() - t0
    check("端到端：导出接口返回 200", st == 200, st)
    check("端到端：返回 xlsx 内容", content[:2] == b"PK", content[:20])
    print(f"    导出 {total} 行：耗时 {elapsed:.2f}s，响应 {len(content) / 1024 / 1024:.1f} MB")
    check("端到端：导出耗时 < 90s", elapsed < 90, f"{elapsed:.2f}s")

    wb = load_workbook(io.BytesIO(content), read_only=True)
    cnt = sum(1 for _ in wb["Sheet1"].iter_rows(values_only=True))
    sheets = wb.sheetnames
    wb.close()
    check("端到端：文件行数 = 全量 + 表头", cnt == total + 1, f"{cnt} vs {total + 1}")
    check("端到端：Sheet 结构完整", sheets == ["Sheet1", "填表说明", "sheet3"], sheets)

    # 清理灌入的压力数据
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("DELETE FROM applications_computer WHERE exam_id=? AND id_number LIKE '9999%'",
                 (exam_id,))
    conn.commit()
    conn.close()
    print("    已清理压力测试数据")


BASE = ""
if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    BASE = args[0] if args else ""
    n = int(args[1]) if len(args) > 1 else 12000

    print("=" * 70)
    print(f"大数据量导出性能验证 · {n} 行")
    print("\n[1] 进程内流式导出基准（计算机模板）")
    bench_inprocess(n)
    print("\n[2] 进程内流式导出基准（普通话模板）")
    bench_inprocess_mandarin(n)

    if BASE:
        print(f"\n[3] 端到端导出（{BASE}）")
        try:
            bench_e2e(n)
        except Exception as e:  # noqa: BLE001
            check("端到端导出", False, repr(e))
    else:
        print("\n[3] 端到端导出 已跳过（未提供服务地址）")

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print("  -", f)
    sys.exit(1 if FAIL else 0)
