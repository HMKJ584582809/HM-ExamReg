# -*- coding: utf-8 -*-
"""证件照功能专项验证。

覆盖：个人上传 / 读取 / 删除、类型与大小校验、越权访问、
批量导入的四种匹配结果（精确 / 学号 / 重名拒绝 / 未匹配）与覆盖生效。

用法：
    python tools/verify_photos.py http://127.0.0.1:8910
"""
import base64
import io
import json
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8910"
PASS, FAIL = [], []

# 1x1 最小合法 PNG（仅用于校验服务端接受，不追求可视）
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
JPEG_MIN = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\xff\xd9"


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            if (r.headers.get("Content-Type") or "").find("json") < 0:
                return r.status, {"_binary": True, "size": len(raw),
                                  "ctype": r.headers.get("Content-Type", "")}
            return r.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return e.code, {"raw": raw[:200].decode("utf-8", "ignore")}


def multipart(path, token, parts):
    """parts: [(field, filename, content, ctype)]，filename 为空则按普通字段处理。"""
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
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            if (r.headers.get("Content-Type") or "").find("json") < 0:
                return r.status, {"_binary": True, "size": len(raw)}
            return r.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return e.code, {"raw": raw[:200].decode("utf-8", "ignore")}


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {extra}"))


def login(account, password):
    st, r = call("POST", "/api/auth/login", body={"account": account, "password": password})
    return (r["data"]["access_token"] if st == 200 and r.get("data") else None), r


def main():
    print("=" * 70)
    print(f"证件照功能专项验证 · {BASE}\n")

    print("[0] 准备")
    admin, r = login("admin", "Admin@123")
    check("管理员登录成功", bool(admin), r)
    if not admin:
        return 1
    st, r = call("GET", "/api/auth/me", token=admin)
    admin_id = r["data"]["id"] if st == 200 else 0
    check("获取管理员 ID", admin_id > 0, r)

    print("\n[1] 个人证件照上传 / 读取 / 删除")
    st, r = multipart("/api/photos/me", admin,
                      [("file", "me.png", PNG_1PX, "image/png")])
    check("上传 PNG 证件照成功", st == 200 and r.get("code") == 0, r)
    check("返回了存储文件名（服务端生成，不含敏感信息）",
          st == 200 and r["data"]["photo"] == f"u{admin_id}.png", r.get("data") if st == 200 else r)

    st, r = call("GET", f"/api/photos/{admin_id}", token=admin)
    check("可读取自己的证件照", st == 200 and r.get("_binary") and r["size"] > 0, r)
    check("照片响应类型为 image/png",
          r.get("ctype", "").startswith("image/png"), r.get("ctype"))

    # 换成 JPEG：应覆盖且清掉旧的 png
    st, r = multipart("/api/photos/me", admin,
                      [("file", "me.jpg", JPEG_MIN, "image/jpeg")])
    check("更换为 JPEG 成功", st == 200 and r["data"]["photo"] == f"u{admin_id}.jpg", r)
    st, r = call("GET", f"/api/photos/{admin_id}", token=admin)
    check("更换后读取到 JPEG", r.get("ctype", "").startswith("image/jpeg"), r.get("ctype"))

    print("\n[2] 安全校验")
    st, r = multipart("/api/photos/me", admin,
                      [("file", "evil.png", b"MZ\x90\x00 not an image at all", "image/png")])
    check("伪造扩展名的非图片被拒（按 magic bytes 判定）", st == 400, r)

    big = PNG_1PX + b"\x00" * (5 * 1024 * 1024)
    st, r = multipart("/api/photos/me", admin,
                      [("file", "big.png", big, "image/png")])
    check("超过 5MB 的图片被拒", st == 400, r)

    # 路径穿越：库里若被写入非法文件名，读取应被拦（用合法流程无法构造，直接校验读取端）
    st, r = call("GET", "/api/photos/../app.db", token=admin)
    check("非法 user_id 不会返回文件", st in (403, 404, 422), r)

    print("\n[3] 越权访问控制")
    # 建一个考生账号
    uname = f"ph{random.randint(10000, 99999)}"
    st, r = call("POST", "/api/users", token=admin, body={
        "username": uname, "real_name": "照片考生", "role": "candidate",
        "password": "Photo@1234", "phone": f"137{random.randint(10000000, 99999999)}",
    })
    check("创建考生账号", st == 200 and r.get("code") == 0, r)
    cid = r["data"].get("id") if st == 200 else 0
    if not cid:
        st, r = call("GET", "/api/users?keyword=" + uname, token=admin)
        cid = r["data"]["list"][0]["id"]
    cand, r = login(uname, "Photo@1234")
    check("考生登录成功", bool(cand), r)

    st, r = call("GET", f"/api/photos/{admin_id}", token=cand)
    check("考生读取他人证件照被拒（403）", st == 403, r)

    st, r = multipart("/api/photos/import/preview", cand,
                      [("files", "x.png", PNG_1PX, "image/png")])
    check("考生无权批量导入证件照（403）", st == 403, r)

    print("\n[4] 批量导入：匹配规则")
    # 造两个同名考生验证重名不误配（姓名带随机数，避免与历史测试数据叠加）
    dup_name = f"重名{random.randint(10000, 99999)}"
    for i in range(2):
        st, r = call("POST", "/api/users", token=admin, body={
            "username": f"dup{random.randint(10000,99999)}", "real_name": dup_name,
            "role": "candidate", "password": "Photo@1234",
            "phone": f"139{random.randint(10000000, 99999999)}",
        })
    st, r = call("GET", "/api/users?keyword=" + urllib.parse.quote(dup_name), token=admin)
    dup_count = r["data"]["total"] if st == 200 else 0
    check("已构造 2 个重名考生", dup_count == 2, dup_count)

    # 造一个带证件号的账号，验证「证件号」这一最高优先级匹配
    idn, id_user = "320102199501011239", f"idn{random.randint(10000, 99999)}"
    st, r = call("POST", "/api/users", token=admin, body={
        "username": id_user, "real_name": "证件号考生", "role": "candidate",
        "password": "Photo@1234", "phone": f"136{random.randint(10000000, 99999999)}",
        "id_number": idn,
    })
    target = {"id_number": idn, "username": id_user} if st == 200 else None
    check("建号可登记证件号（并通过合法性校验）", bool(target), r)

    st, r = call("POST", "/api/users", token=admin, body={
        "username": f"badid{random.randint(10000,99999)}", "real_name": "非法证件号",
        "role": "candidate", "password": "Photo@1234",
        "phone": f"135{random.randint(10000000, 99999999)}",
        "id_number": "320102199501011234",          # 校验位错误
    })
    check("建号时非法身份证号被拒", st == 400, r)

    parts = []
    if target:
        parts.append(("files", f"{target['id_number']}.jpg", JPEG_MIN, "image/jpeg"))
    parts.append(("files", f"{uname}.jpg", JPEG_MIN, "image/jpeg"))       # 按用户名匹配
    parts.append(("files", f"{dup_name}.jpg", JPEG_MIN, "image/jpeg"))    # 重名 → 拒绝
    parts.append(("files", "查无此人.jpg", JPEG_MIN, "image/jpeg"))        # 未匹配
    parts.append(("files", "bad.txt", b"not image", "text/plain"))        # 非图片

    st, r = multipart("/api/photos/import/preview", admin, parts)
    check("批量预览成功", st == 200 and r.get("code") == 0, r)
    d = r.get("data", {}) if st == 200 else {}
    counts = d.get("counts", {})
    check("精确（证件号）匹配成功", counts.get("matched", 0) >= 1, counts)
    check("重名判为多人匹配、不自动覆盖",
          counts.get("ambiguous", 0) >= 1, counts)
    check("查无此人判为未匹配", counts.get("unmatched", 0) >= 1, counts)
    check("非图片文件被标记无效", counts.get("invalid", 0) >= 1, counts)
    items = d.get("items", [])
    amb = [i for i in items if i["status"] == "ambiguous"]
    check("重名项说明了原因", amb and "多个账号" in amb[0].get("note", ""), amb[:1])
    m = [i for i in items if i["status"] == "matched"]
    check("匹配项带出了匹配方式与用户名",
          m and m[0].get("by_label") and m[0].get("username"), m[:1])
    check("按证件号精确匹配生效（最高优先级）",
          any(i.get("by") == "id_number" for i in m), m)
    check("按用户名/学号匹配生效",
          any(i.get("by") == "username" for i in m), m)

    print("\n[5] 批量导入：确认覆盖")
    token_pv = d.get("token", "")
    st, r = call("POST", "/api/photos/import", token=admin, body={"token": token_pv})
    check("确认导入成功", st == 200 and r.get("code") == 0, r)
    check("覆盖数量与预览一致",
          st == 200 and r["data"]["updated"] == counts.get("matched", 0),
          r.get("data") if st == 200 else r)

    if m:
        uid = m[0]["user_id"]
        st, r = call("GET", f"/api/photos/{uid}", token=admin)
        check("被覆盖的账号可读取到新证件照", st == 200 and r.get("_binary"), r)
        st, r = call("GET", "/api/users?keyword=" + (m[0].get("username") or ""), token=admin)
        row = (r["data"]["list"] or [{}])[0] if st == 200 else {}
        check("users.photo 字段已更新",
              row.get("photo") == f"u{uid}.jpg", row.get("photo"))

    st, r = call("POST", "/api/photos/import", token=admin, body={"token": token_pv})
    check("重复使用同一凭证被拒（防重复导入）", st == 400, r)

    print("\n[6] 删除")
    st, r = call("DELETE", "/api/photos/me", token=admin)
    check("删除本人证件照成功", st == 200 and r.get("code") == 0, r)
    st, r = call("GET", f"/api/photos/{admin_id}", token=admin)
    check("删除后读取返回 404", st == 404, r)

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
