# -*- coding: utf-8 -*-
"""证件照一键导出专项验证。

覆盖：
  1. 目录结构：{考试批次}/{院系}/{班级}/{证件号码}.{jpg|png}
  2. 格式选择：JPG / PNG，且会做真实转换（PNG→JPG 铺白底、JPG→PNG）
  3. 缺照片的考生不进包，但会列在《_导出说明.txt》里
  4. 权限：无 export 权限者被拒
  5. 数据范围：二级学院审核只能导出本院系的照片
  6. 中文目录名不乱码（zip UTF-8 flag）

用法：
    python tools/verify_photo_export.py http://127.0.0.1:8917
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from urllib.parse import quote

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8917"
PASS, FAIL = [], []


def call(method, path, token=None, body=None, raw=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            rawb = r.read()
            if raw or (r.headers.get("Content-Type") or "").find("json") < 0:
                return r.status, rawb
            return r.status, json.loads(rawb.decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_b = e.read()
        try:
            return e.code, json.loads(body_b.decode("utf-8"))
        except Exception:
            return e.code, {"raw": body_b[:200].decode("utf-8", "ignore")}


def multipart(path, token, parts):
    """parts: [(field, filename, content, ctype)]；filename 为 None 时按普通字段。"""
    boundary = "----wb" + uuid.uuid4().hex
    buf = io.BytesIO()
    for field, filename, content, ctype in parts:
        if filename is None:
            buf.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n'
                      .encode("utf-8"))
            buf.write(str(content).encode("utf-8") + b"\r\n")
        else:
            buf.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}";'
                      f' filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode("utf-8"))
            buf.write(content + b"\r\n")
    buf.write(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(BASE + path, data=buf.getvalue(), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
            if (r.headers.get("Content-Type") or "").find("json") < 0:
                return r.status, {"_binary": True, "size": len(raw)}
            return r.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        rawb = e.read()
        try:
            return e.code, json.loads(rawb.decode("utf-8"))
        except Exception:
            return e.code, {"raw": rawb[:200].decode("utf-8", "ignore")}


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {extra}"))


def login(account, password):
    st, r = call("POST", "/api/auth/login", body={"account": account, "password": password})
    return (r["data"]["access_token"] if st == 200 and r.get("data") else None)


# ---------------------------------------------------------------- 身份证号
_W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_C = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]


def mk_id(seq):
    """生成合法身份证号：6 位地区 + 8 位生日 + 3 位顺序 + 1 位校验。"""
    base = "320102" + "20030101" + f"{seq:03d}"   # 共 17 位，末 3 位保证互不相同
    assert len(base) == 17, len(base)
    s = sum(int(base[i]) * _W[i] for i in range(17))
    return base + _C[s % 11]


def png_bytes(color=(220, 40, 40)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGBA", (120, 160), color + (255,)).save(b, "PNG")
    return b.getvalue()


def jpg_bytes(color=(40, 80, 220)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (120, 160), color).save(b, "JPEG", quality=90)
    return b.getvalue()


def main():
    import openpyxl
    print("=" * 70)
    print(f"证件照一键导出专项验证 · {BASE}\n")

    print("[0] 准备")
    admin = login("admin", "Admin@123")
    check("管理员登录成功", bool(admin))
    if not admin:
        return 1

    # 新建一个干净批次，避免与演示数据混在一起导致断言不稳定
    tag = time.strftime("%H%M%S")
    st, ex = call("POST", "/api/exams", token=admin, body={
        "exam_type": "computer", "exam_year": 2026, "exam_month": 9,
        "name": f"PHOTO-{tag}", "signup_start_at": "2026-09-01 00:00",
        "signup_end_at": "2026-12-31 23:59", "status": "draft"})
    check("创建临时考试批次", st == 200 and ex.get("code") == 0, ex.get("message"))
    exam_id = (ex.get("data") or {}).get("id") or 0
    exam_name = f"PHOTO-{tag}"
    if not exam_id:
        return 1

    # 4 名考生：2 个院系 × 2 个班级，其中 1 人不传照片
    people = [
        ("照片甲", mk_id(101), "计算机学院", "计算机2101"),
        ("照片乙", mk_id(102), "计算机学院", "软件工程2202"),
        ("照片丙", mk_id(103), "教育学院", "教育2301"),
        ("无照片丁", mk_id(104), "教育学院", "教育2301"),
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["姓名", "性别", "证件类型", "证件号码", "报考科目", "就读或毕业院校",
               "班级", "学历", "手机号码"])
    for nm, idno, college, klass in people:
        # 报考科目必须是字典里的真实值（导入会校验）
        ws.append([nm, "男", "1", idno, "信息处理工程师技术水平", college, klass, "本科",
                   "139" + idno[-8:]])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    st, r = multipart("/api/applications/import", admin, [
        ("exam_id", None, exam_id, None),
        ("audit_status", None, "pending", None),
        ("create_accounts", None, 1, None),
        ("defaults", None, json.dumps({"org_code": "1001", "exam_site_code": "100101"}), None),
        ("file", "photos.xlsx", buf.getvalue(),
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ])
    check("批量导入 4 条报名", st == 200 and r.get("data", {}).get("success") == 4,
          (r.get("data") or {}).get("errors") or r.get("message"))
    if (r.get("data") or {}).get("success") != 4:
        print("  导入失败明细：", (r.get("data") or {}).get("errors"))
        return 1

    # 给前 3 人批量导入证件照（按证件号匹配）：2 张 PNG + 1 张 JPG
    parts = [("files", people[0][1] + ".png", png_bytes(), "image/png"),
             ("files", people[1][1] + ".png", png_bytes((40, 160, 90)), "image/png"),
             ("files", people[2][1] + ".jpg", jpg_bytes(), "image/jpeg")]
    st, r = multipart("/api/photos/import/preview", admin, parts)
    check("照片预览按证件号匹配 3 人",
          st == 200 and (r.get("data") or {}).get("counts", {}).get("matched") == 3,
          (r.get("data") or {}).get("counts"))
    token = (r.get("data") or {}).get("token", "")
    st, r = call("POST", "/api/photos/import", token=admin, body={"token": token})
    check("照片覆盖导入 3 张", st == 200 and (r.get("data") or {}).get("updated") == 3, r)

    print("\n[1] 导出前统计")
    st, r = call("GET", f"/api/photos/export/stat?exam_id={exam_id}", token=admin)
    d = r.get("data") or {}
    check("统计接口可访问", st == 200, r.get("message"))
    check("共 4 条报名", d.get("total") == 4, d)
    check("3 人已上传证件照", d.get("with_photo") == 3, d)
    check("1 人缺照片", d.get("without_photo") == 1, d)

    print("\n[2] 导出 JPG")
    st, blob = call("POST", "/api/photos/export", token=admin,
                    body={"exam_id": exam_id, "fmt": "jpg"}, raw=True)
    check("导出接口返回 200", st == 200, st)
    check("返回内容是 zip", isinstance(blob, bytes) and blob[:2] == b"PK", type(blob))
    if not isinstance(blob, bytes) or blob[:2] != b"PK":
        return 1
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = zf.namelist()
    photos = [n for n in names if n.lower().endswith((".jpg", ".jpeg", ".png"))]
    check("包内 3 张照片", len(photos) == 3, photos)
    check("存在《_导出说明.txt》", any(n.endswith("_导出说明.txt") for n in names), names)

    want = [f"{exam_name}/计算机学院/计算机2101/{people[0][1]}.jpg",
            f"{exam_name}/计算机学院/软件工程2202/{people[1][1]}.jpg",
            f"{exam_name}/教育学院/教育2301/{people[2][1]}.jpg"]
    for w in want:
        check("目录结构：" + w.replace(exam_name, "批次"), w in names,
              [n for n in names if n.endswith(".jpg")])
    check("缺照片的考生不在包内",
          not any(people[3][1] in n for n in names), [n for n in names if people[3][1] in n])

    # 逐张校验真实格式（PNG 传上来也要变成 JPEG）
    ok_fmt = True
    for w in want:
        data = zf.read(w)
        ok_fmt = ok_fmt and data[:3] == b"\xff\xd8\xff"
    check("PNG 素材已转成真正的 JPEG（非改扩展名）", ok_fmt)

    note = zf.read([n for n in names if n.endswith("_导出说明.txt")][0]).decode("utf-8-sig")
    check("说明含成功数 3", "成功 3 张" in note, note[:200])
    check("说明列出缺照片考生姓名", people[3][0] in note, note[-200:])

    print("\n[3] 导出 PNG（格式切换）")
    st, blob2 = call("POST", "/api/photos/export", token=admin,
                     body={"exam_id": exam_id, "fmt": "png"}, raw=True)
    check("PNG 导出返回 200", st == 200, st)
    zf2 = zipfile.ZipFile(io.BytesIO(blob2))
    names2 = zf2.namelist()
    pngs = [n for n in names2 if n.lower().endswith(".png")]
    check("包内 3 张 PNG", len(pngs) == 3, pngs)
    first = zf2.read(pngs[0])
    check("JPG 素材已转成真正的 PNG", first[:8] == b"\x89PNG\r\n\x1a\n", first[:8])
    check("PNG 导出同样按三级目录",
          all(n.startswith(f"{exam_name}/") and n.count("/") == 3 for n in pngs), pngs)

    print("\n[4] 权限与数据范围")
    # 考生无 export 权限
    st, r = call("GET", "/api/users?page_size=200", token=admin)
    rows = (r.get("data") or {}).get("list") or []
    cand = next((x for x in rows if x["username"] == "candidate"), None)
    if cand:
        st, r = call("POST", f"/api/users/{cand['id']}/reset-password", token=admin,
                     body={"new_password": "Candidate@123"})
        ctok = login("candidate", "Candidate@123")
        st, r = call("POST", "/api/photos/export", token=ctok,
                     body={"exam_id": exam_id, "fmt": "jpg"})
        check("考生无导出权限（403）", st == 403, r.get("message"))
    else:
        check("考生无导出权限（403）", False, "找不到 candidate 账号")

    # 二级学院审核：只能导出本院系
    st, r, = call("POST", "/api/users", token=admin, body={
        "username": "ph" + tag, "real_name": "照片范围测试", "role": "college_reviewer",
        "password": "Photo@1234", "phone": "137" + (tag + "0000")[:8], "college": "计算机学院"})
    check("创建二级学院审核账号", st == 200 and r.get("code") == 0, r.get("message"))
    ctok = login("ph" + tag, "Photo@1234")
    st, blob3 = call("POST", "/api/photos/export", token=ctok,
                     body={"exam_id": exam_id, "fmt": "jpg"}, raw=True)
    check("二级学院审核可导出", st == 200, st)
    if isinstance(blob3, bytes) and blob3[:2] == b"PK":
        zf3 = zipfile.ZipFile(io.BytesIO(blob3))
        ps = [n for n in zf3.namelist() if n.lower().endswith(".jpg")]
        check("只有本院系（计算机学院）的 2 张",
              len(ps) == 2 and all("/计算机学院/" in n for n in ps), ps)
    else:
        check("只有本院系（计算机学院）的 2 张", False, blob3[:120] if isinstance(blob3, bytes) else blob3)

    print("\n[5] 入参校验")
    st, r = call("POST", "/api/photos/export", token=admin,
                 body={"exam_id": exam_id, "fmt": "gif"})
    check("非法格式被拒", st == 400, r.get("message"))
    st, r = call("POST", "/api/photos/export", token=admin, body={"exam_id": 0, "fmt": "jpg"})
    check("未选批次被拒", st == 400, r.get("message"))

    print("\n[5.5] 按班级分批导出")
    st, r = call("GET", f"/api/photos/export/stat?exam_id={exam_id}", token=admin)
    d = r.get("data") or {}
    classes = d.get("classes") or []
    check("统计返回可选班级列表", st == 200 and "软件工程2202" in classes, classes)
    st, r2 = call("GET", f"/api/photos/export/stat?exam_id={exam_id}"
                         f"&class_name={quote('软件工程2202')}", token=admin)
    d2 = r2.get("data") or {}
    check("按班级筛选后总数变少",
          st == 200 and 0 < (d2.get("total") or 0) < (d.get("total") or 0),
          f"{d.get('total')} -> {d2.get('total')}")
    st, blob4 = call("POST", "/api/photos/export", token=admin,
                     body={"exam_id": exam_id, "fmt": "jpg", "class_name": "软件工程2202"},
                     raw=True)
    if isinstance(blob4, bytes) and blob4[:2] == b"PK":
        zf4 = zipfile.ZipFile(io.BytesIO(blob4))
        pics = [n for n in zf4.namelist() if n.lower().endswith(".jpg")]
        check("按班级导出只含该班照片",
              len(pics) >= 1 and all("/软件工程2202/" in n for n in pics), pics)
    else:
        check("按班级导出只含该班照片", False,
              blob4[:120] if isinstance(blob4, bytes) else blob4)

    print("\n[6] 清理")
    st, r = call("POST", "/api/system/reset", token=admin,
                 body={"scopes": ["applications"], "confirm": "确认清空"})
    check("清理报名数据", st == 200, r.get("message"))
    st, r = call("DELETE", f"/api/exams/{exam_id}", token=admin)
    check("删除临时批次", st == 200 and r.get("code") == 0, r.get("message"))
    st, r = call("GET", "/api/users?keyword=ph" + tag, token=admin)
    for u in (r.get("data") or {}).get("list") or []:
        call("DELETE", f"/api/users/{u['id']}", token=admin)
    check("删除临时账号", True)

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
