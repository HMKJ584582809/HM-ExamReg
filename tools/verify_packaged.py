# -*- coding: utf-8 -*-
"""
打包内容新鲜度校验 —— 61 项
------------------------------------------------------------------
源码测试全绿 ≠ 交付的 exe 里是新代码。本脚本连**运行中的 exe**（或源码服务），
直接抓它对外提供的静态资源与接口，验证本轮改动确实被打进去了。

用法：
    python tools/verify_packaged.py --base http://127.0.0.1:8803

覆盖：
  [1] 静态资源新鲜度：逐文件取回文本，断言必含 / 必不含标记       (19 项)
  [2] 字典新增两类：只读接口 + 维护 + 删除保护                    (5 项)
  [3] 用户管理 = 管理员专属：非管理员访问被 403                   (3 项)
  [4] AI 网络代理：写入回显 + socks5 被拒                         (4 项)
  [5] 报名默认值：系统维护可改 + 随批次详情下发                    (4 项)
  [6] 数据看板深度层：10 个新增指标 + KPI 过程指标                (11 项)
  [7] 实名认证：配置 / 本人状态 / 审核列表 / 用户列表带状态        (4 项)
  [8] 汇总导出官方模板：两套内置模板确实打进 exe                   (2 项)
  [9] 证件照制作：本地 ONNX 引擎在 exe 内真能推理                 (9 项)

注意：会建临时账号与批次，因此**必须用一次性全新库**，不要与 smoke_test 连跑。
"""
import argparse
import io
import json
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PIL import Image, ImageDraw

PASS = 0
FAIL = 0
BASE = "http://127.0.0.1:8803"
TOKEN = ""

# 每次运行换一套名字，保证脚本可重复跑（撞唯一约束会误报失败）
SUFFIX = uuid.uuid4().hex[:6]
DEP_NAME = "打包校验处" + SUFFIX[:2]
DEP_USER = "pkgd" + SUFFIX
RV_USER = "pkgr" + SUFFIX
PHONE1 = "139" + "".join(random.choice("0123456789") for _ in range(8))
PHONE2 = "138" + "".join(random.choice("0123456789") for _ in range(8))


def call(method, path, body=None, token=None, raw=False):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            blob = resp.read()
            return resp.status, (blob.decode("utf-8") if raw else json.loads(blob.decode("utf-8")))
    except urllib.error.HTTPError as e:
        blob = e.read()
        if raw:
            return e.code, blob.decode("utf-8", "ignore")
        try:
            return e.code, json.loads(blob.decode("utf-8"))
        except Exception:
            return e.code, {"message": blob.decode("utf-8", "ignore")[:200]}


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + ("  → %s" % (extra,) if extra else ""))


def get_text(path):
    st, txt = call("GET", path, raw=True)
    return st, txt


def make_photo(w=600, h=800):
    """合成一张「人像」测试图（背景 + 躯干 + 头部）。

    不追求 MODNet 抠得多准，只要求是可解码的正常 JPG、四周是背景色。
    """
    img = Image.new("RGB", (w, h), (205, 214, 224))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([int(w * 0.24), int(h * 0.48), int(w * 0.76), int(h * 0.99)],
                        radius=70, fill=(72, 92, 132))
    d.ellipse([int(w * 0.32), int(h * 0.13), int(w * 0.68), int(h * 0.52)],
              fill=(240, 203, 172))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def post_file(path, content, filename="p.jpg", ctype="image/jpeg", fields=()):
    """multipart 上传，返回 (status, headers, raw) —— 保留响应头以校验 X-IdPhoto-*。"""
    b = "----wb" + uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in fields:
        buf.write(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n' % (b, k))
                  .encode("utf-8") + str(v).encode("utf-8") + b"\r\n")
    buf.write(('--%s\r\nContent-Disposition: form-data; name="file";'
               ' filename="%s"\r\nContent-Type: %s\r\n\r\n' % (b, filename, ctype))
              .encode("utf-8"))
    buf.write(content + b"\r\n")
    buf.write(("--%s--\r\n" % b).encode("utf-8"))
    req = urllib.request.Request(BASE + path, data=buf.getvalue(), method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=%s" % b)
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def main():
    global TOKEN, BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="Admin@123")
    args = ap.parse_args()
    BASE = args.base

    print("=" * 66)
    print("打包内容新鲜度校验 · " + BASE)
    print("=" * 66)

    # ---------------- [1] 静态资源新鲜度 ----------------
    print("\n[1] 静态资源新鲜度（exe 实际吐出的文件）")
    # (路径, 描述, 必须包含, 必须不包含)
    CASES = [
        ("/static/js/app.js", "路由守卫支持 adminOnly", ["adminOnly"], []),
        ("/static/js/pages/users.js", "用户管理：角色页签 + 内滚动",
         ["role-tabs", "tableMax"], ["kpi-card"]),
        ("/static/js/pages/dashboard.js", "看板全屏", ["requestFullscreen"], []),
        ("/static/js/pages/ai.js", "AI 网络代理表单项", ["proxy"], []),
        ("/static/js/pages/dicts.js", "字典两类（学院/部门）",
         ["college", "department"], []),
        ("/static/js/pages/apply.js", "报名：所在单位预填默认值",
         ["defaults", "employer"], []),
        ("/static/js/pages/profile.js", "手机号正则为单反斜杠",
         ["1[3-9]\\d{9}"], ["1[3-9]\\\\d{9}"]),
        ("/static/js/store.js", "字典下拉数据源", ["departments"], []),
        ("/static/css/app.css", "角色页签样式", [".role-tabs"], []),
        # ---- 本轮（大屏深度层 / 实名 / 模板导出）----
        ("/static/js/pages/dashboard.js", "大屏深度层面板",
         ["运行质量", "审核时效分布", "各批次审核进度", "审核员工作量"], []),
        ("/static/js/pages/realname.js", "实名认证页", ["实名"], []),
        ("/static/js/pages/users.js", "用户列表实名列与审核入口",
         ["实名审核", "rnTagType"], []),
        ("/static/js/pages/export.js", "导出模板来源与一键打包",
         ["官方模板管理", "一键导出", "汇总表"], []),
        ("/static/js/pages/analysis.js", "分析页深度分析", ["深度分析"], []),
        ("/static/css/app.css", "大屏深度层样式", [".screen-kpis2", ".pg-line"], []),
        # must_not：真实缺陷 —— 曾对 api.get 已解包的结果再取一层 .data，
        #   导致浏览器里尺寸/底色选项一个都不显示、还误报「本地抠图模型不可用」。
        #   后端一直是好的，只有断言 exe 实际吐出的 JS 才能守住这个回归。
        ("/static/js/pages/idphoto.js", "证件照制作页（一寸/二寸 + 换底色）",
         ["one_inch", "换底色", "本地抠图"], ["r.data"]),
    ]
    for path, desc, must, must_not in CASES:
        st, txt = get_text(path)
        ok = st == 200 and all(m in txt for m in must) and not any(m in txt for m in must_not)
        check("%s（%s）" % (desc, path.rsplit("/", 1)[-1]), ok, "HTTP %d，%d 字节" % (st, len(txt)))

    # 登录后才能验证的：接口侧
    st, lg = call("POST", "/api/auth/login",
                  {"account": args.user, "password": args.password})
    check("管理员可登录", st == 200 and lg.get("code") == 0, lg.get("message"))
    TOKEN = (lg.get("data") or {}).get("access_token") or ""
    check("取得令牌", bool(TOKEN))
    check("管理员角色为 admin",
          ((lg.get("data") or {}).get("user") or {}).get("role") == "admin")

    # ---------------- [2] 字典新增两类 ----------------
    print("\n[2] 字典新增两类（二级学院 / 部门）")
    st, r = call("GET", "/api/dicts/college", token=TOKEN)
    check("只读接口 /api/dicts/college 可用", st == 200 and r.get("code") == 0, r.get("message"))
    st, r = call("GET", "/api/dicts/department", token=TOKEN)
    check("只读接口 /api/dicts/department 可用", st == 200 and r.get("code") == 0, r.get("message"))

    st, r = call("POST", "/api/dicts/manage/department",
                 {"name": DEP_NAME}, token=TOKEN)
    check("新增部门字典项", st == 200 and r.get("code") == 0, r.get("message"))
    # 注意：非 text_pk 的字典（int 主键）新增只回 {name}，id 要从列表里取
    st, r = call("GET", "/api/dicts/manage/department?keyword=" +
                 urllib.parse.quote(DEP_NAME), token=TOKEN)
    dep_id = next((i.get("id") for i in ((r.get("data") or {}).get("list") or [])
                   if i.get("name") == DEP_NAME), None)
    check("字典项可从列表取到 id", dep_id is not None, str(r.get("data"))[:120])

    # 引用后禁止删除（user_ref 生效）
    st, r = call("POST", "/api/users", {
        "username": DEP_USER, "real_name": "部门引用", "phone": PHONE1,
        "role": "candidate", "password": "Pkg@12345", "department": DEP_NAME,
    }, token=TOKEN)
    check("建号时可写 department", st == 200 and r.get("code") == 0, r.get("message"))
    if dep_id:
        st, r = call("DELETE", "/api/dicts/manage/department/%s" % dep_id, token=TOKEN)
        check("被引用的部门禁止删除", st != 200 or r.get("code") != 0, r.get("message"))

    # ---------------- [3] 用户管理 = 管理员专属 ----------------
    print("\n[3] 用户管理仅管理员可用")
    st, r = call("POST", "/api/users", {
        "username": RV_USER, "real_name": "审核员", "phone": PHONE2,
        "role": "reviewer", "password": "Pkg@12345",
    }, token=TOKEN)
    check("创建审核员成功", st == 200 and r.get("code") == 0, r.get("message"))
    st, rl = call("POST", "/api/auth/login",
                  {"account": RV_USER, "password": "Pkg@12345"})
    rv_token = (rl.get("data") or {}).get("access_token") or ""
    check("审核员可登录", bool(rv_token), rl.get("message"))
    if rv_token:
        st, r = call("GET", "/api/users?page=1&page_size=10", token=rv_token)
        check("非管理员访问用户列表被 403", st == 403, "HTTP %d" % st)

    # ---------------- [4] AI 网络代理 ----------------
    print("\n[4] AI 网络代理配置")
    st, r = call("PUT", "/api/ai/config", {"proxy": "http://127.0.0.1:7890"}, token=TOKEN)
    check("写入 http 代理成功", st == 200 and r.get("code") == 0, r.get("message"))
    st, r = call("GET", "/api/ai/config", token=TOKEN)
    got = ((r.get("data") or {}).get("config") or {}).get("proxy")
    check("代理可回显", got == "http://127.0.0.1:7890", str(got))
    st, r = call("PUT", "/api/ai/config", {"proxy": "socks5://127.0.0.1:1080"}, token=TOKEN)
    check("socks5 代理被拒绝", st != 200 or r.get("code") != 0, r.get("message"))
    st, r = call("PUT", "/api/ai/config", {"proxy": ""}, token=TOKEN)
    check("清空代理成功", st == 200 and r.get("code") == 0, r.get("message"))

    # ---------------- [5] 报名默认值下发 ----------------
    print("\n[5] 报名表单默认值")
    st, r = call("GET", "/api/system/settings", token=TOKEN)
    emp = ((r.get("data") or {}).get("apply") or {}).get("employer_default")
    check("系统设置含 apply.employer_default（出厂为空，不预填单位名）", emp == "", str(emp))

    yr = random.choice([2031, 2032, 2033, 2034, 2035])
    st, r = call("POST", "/api/exams", {
        "exam_type": "mandarin", "exam_year": yr, "exam_month": 5,
        "signup_start_at": "%d-05-01 00:00:00" % yr,
        "signup_end_at": "%d-05-20 23:59:59" % yr,
        "description": "打包校验批次", "status": "draft"}, token=TOKEN)
    check("创建普通话批次", st == 200 and r.get("code") == 0, r.get("message"))
    exam_id = (r.get("data") or {}).get("id")
    if exam_id:
        st, r = call("GET", "/api/exams/%s" % exam_id, token=TOKEN)
        d = ((r.get("data") or {}).get("defaults") or {}).get("employer")
        check("批次详情下发 defaults.employer（出厂为空）", d == "", str(d))
        call("DELETE", "/api/exams/%s" % exam_id, token=TOKEN)

    # ---------------- [6] 数据看板深度层 ----------------
    print("\n[6] 数据看板深度指标")
    st, r = call("GET", "/api/dashboard", token=TOKEN)
    dd = r.get("data") or {}
    for k in ("efficiency", "photo", "profile", "reject_reasons", "batch_progress",
              "org_class", "reviewer_rank", "hourly", "completeness", "trend30"):
        check("大屏返回 %s" % k, k in dd, "缺失，实有 %s" % str(sorted(dd.keys()))[:160])
    kpi = dd.get("kpi") or {}
    check("KPI 含平均审核耗时与证件照完整率",
          "avg_hours" in kpi and "photo_rate" in kpi, str(kpi)[:160])

    # ---------------- [7] 实名认证 ----------------
    print("\n[7] 实名认证")
    st, r = call("GET", "/api/realname/config", token=TOKEN)
    check("实名配置接口可用", st == 200 and r.get("code") == 0, r.get("message"))
    st, r = call("GET", "/api/realname/me", token=TOKEN)
    check("本人实名状态可查", st == 200 and r.get("code") == 0, r.get("message"))
    st, r = call("GET", "/api/realname/list?page=1&page_size=5", token=TOKEN)
    check("实名审核列表可用", st == 200 and r.get("code") == 0, r.get("message"))
    st, r = call("GET", "/api/users?page=1&page_size=3", token=TOKEN)
    ulist = (r.get("data") or {}).get("list") or []
    check("用户列表每行都带 realname_status",
          bool(ulist) and all("realname_status" in u for u in ulist),
          str([u.get("username") for u in ulist])[:160])

    # ---------------- [8] 官方模板确实打进 exe ----------------
    print("\n[8] 汇总导出官方模板")
    st, r = call("GET", "/api/export/templates", token=TOKEN)
    items = ((r.get("data") or {}).get("items") or [])
    check("模板接口返回两套模板", len(items) >= 2,
          str([(i.get("base"), i.get("source")) for i in items]))
    # 只验「内置模板存在」不能证明打包带了资源：source=builtin 时路径指向 _MEIPASS，
    # 必须同时断言 exists=True（文件真的被解出来了）
    check("内置模板在 exe 内可用（已随打包带入）",
          any(i.get("exists") and i.get("source") == "builtin" for i in items),
          json.dumps(items, ensure_ascii=False)[:220])

    # ---------------- [9] 证件照本地引擎（打包最容易坏的一环）----------------
    print("\n[9] 证件照制作（本地抠图引擎随 exe 打包）")
    photo = make_photo()
    st, r = call("GET", "/api/idphoto/specs", token=TOKEN)
    sp = r.get("data") or {}
    check("规格接口可用", st == 200 and r.get("code") == 0, r.get("message"))
    sizes = {s["key"] for s in sp.get("sizes", [])}
    colors = {c["key"] for c in sp.get("colors", [])}
    check("尺寸含一寸 / 二寸 / 原尺寸",
          {"one_inch", "two_inch", "original"} <= sizes, str(sizes))
    check("底色含白 / 蓝 / 红 / 透明",
          {"white", "blue", "red", "transparent"} <= colors, str(colors))
    eng = (sp.get("engines") or {}).get("local") or {}
    # ⚠ 只断言接口 200 证明不了打包成功：模型没被 --add-data 带进去时，
    #   available() 会返回 False，前端悄悄退回远程引擎，页面上完全看不出来
    check("本地引擎可用（hivision_modnet.onnx 已随包带入 + onnxruntime 可导入）",
          eng.get("enabled") is True, eng)
    check("本地引擎无不可用原因", not (eng.get("reason") or ""), str(eng.get("reason")))
    st, _hd, raw = post_file("/api/idphoto/preview", photo)
    check("exe 内本地 ONNX 推理成功（真的跑出了抠图 PNG）",
          st == 200 and raw[:4] == b"\x89PNG", "HTTP %d，%d 字节" % (st, len(raw)))
    st, hd, raw = post_file("/api/idphoto/make", photo,
                            fields=(("size", "two_inch"), ("color", "blue"),
                                    ("engine", "local")))
    check("可制作二寸蓝底证件照", st == 200 and len(raw) > 1000,
          "HTTP %d，%d 字节" % (st, len(raw)))
    check("实际尺寸为 413×579", hd.get("X-IdPhoto-Size") == "413x579",
          str(hd.get("X-IdPhoto-Size")))
    check("走本地引擎（照片不出本机）", hd.get("X-IdPhoto-Engine") == "local",
          str(hd.get("X-IdPhoto-Engine")))

    call("PUT", "/api/ai/config", {"proxy": "none"}, token=TOKEN)
    print("\n" + "=" * 66)
    print("结果：%d 项通过，%d 项失败" % (PASS, FAIL))
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
