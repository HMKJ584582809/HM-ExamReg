# -*- coding: utf-8 -*-
"""证件照制作（一寸 / 二寸 + 自动换底色）专项验证（模块 11）。

覆盖：
  * 规格接口：尺寸表、底色表、默认值、双引擎状态
  * **本地引擎真能推理**：这是打包最容易坏的环节 —— 24.7MB 的
    hivision_modnet.onnx 必须被解到 _MEIPASS，onnxruntime 必须能加载并跑出结果。
    只断言「接口 200」不够，必须真的拿到带 alpha 的抠图 PNG。
  * 尺寸 × 底色的全组合：响应头、真实像素、角落底色
  * 不换底（keep）走免抠图快路径
  * 引擎降级与错误处理

用法：
    python tools/verify_idphoto.py http://127.0.0.1:8917
"""
import io
import json
import sys
import urllib.error
import urllib.request
import uuid

from PIL import Image, ImageDraw

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8917"
PASS, FAIL = [], []

# 与 backend/app/idphoto/specs.py 保持一致（改规格时这里要跟着改，否则会误报）
EXPECT_SIZE = {"one_inch": (295, 413), "two_inch": (413, 579), "original": (600, 800)}
EXPECT_RGB = {"white": (255, 255, 255), "blue": (67, 142, 219), "red": (241, 78, 85)}


# ------------------------------------------------------------------ 测试素材

def make_photo(w=600, h=800):
    """合成一张「人像」测试图：浅灰蓝渐变背景 + 头部椭圆 + 躯干。

    不追求 MODNet 抠得多准（合成图本来就不是真实人像），
    只要求：是一张能被 Pillow 解码的正常 JPG，且四周一定是背景色
    —— 这样角落像素就能用来断言「底色换对了」。
    """
    img = Image.new("RGB", (w, h), (205, 214, 224))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)],
               fill=(205 - y * 35 // h, 214 - y * 25 // h, 224 - y * 10 // h))
    d.rounded_rectangle([int(w * 0.24), int(h * 0.48), int(w * 0.76), int(h * 0.99)],
                        radius=70, fill=(72, 92, 132))          # 躯干
    d.ellipse([int(w * 0.42), int(h * 0.44), int(w * 0.58), int(h * 0.60)],
              fill=(226, 186, 158))                              # 脖子
    d.ellipse([int(w * 0.32), int(h * 0.13), int(w * 0.68), int(h * 0.52)],
              fill=(240, 203, 172))                              # 头
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


PHOTO = make_photo()


# ------------------------------------------------------------------ 请求封装

def call(method, path, token=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def jcall(method, path, token=None, body=None):
    st, hd, raw = call(method, path, token, body)
    try:
        return st, hd, json.loads(raw.decode("utf-8"))
    except Exception:
        return st, hd, {"raw": raw[:200].decode("utf-8", "ignore")}


def post_file(path, token, content, filename="p.jpg", ctype="image/jpeg", fields=()):
    """multipart 上传，返回 (status, headers, raw)，保留响应头以便校验 X-IdPhoto-*。"""
    b = "----wb" + uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in fields:
        buf.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
                  .encode("utf-8") + str(v).encode("utf-8") + b"\r\n")
    buf.write(f'--{b}\r\nContent-Disposition: form-data; name="file";'
              f' filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode("utf-8"))
    buf.write(content + b"\r\n")
    buf.write(f"--{b}--\r\n".encode("utf-8"))
    req = urllib.request.Request(BASE + path, data=buf.getvalue(), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={b}")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {extra}"))


def corner_rgb(raw):
    """角落 5×5 的平均色：换底色后这一块必定是纯底色。"""
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    px = [img.getpixel((x, y)) for x in range(5) for y in range(5)]
    n = len(px)
    return tuple(sum(p[i] for p in px) // n for i in range(3))


def img_size(raw):
    return Image.open(io.BytesIO(raw)).size


# ------------------------------------------------------------------ 用例

def main():
    print("=" * 70)
    print(f"证件照制作（一寸/二寸 + 自动换底色）专项验证 · {BASE}\n")

    print("[0] 准备")
    st, hd, r = jcall("POST", "/api/auth/login",
                      body={"account": "admin", "password": "Admin@123"})
    admin = r["data"]["access_token"] if st == 200 and r.get("data") else None
    check("管理员登录成功", bool(admin), r)
    if not admin:
        return 1

    print("\n[1] 规格接口 /api/idphoto/specs")
    st, hd, r = jcall("GET", "/api/idphoto/specs", admin)
    d = r.get("data", {}) if st == 200 else {}
    check("规格接口返回成功", st == 200 and r.get("code") == 0, r)

    sizes = {s["key"]: s for s in d.get("sizes", [])}
    check("尺寸含一寸 / 二寸 / 原尺寸",
          {"one_inch", "two_inch", "original"} <= set(sizes), list(sizes))
    check("一寸为 295×413（25×35mm @300dpi）",
          (sizes.get("one_inch", {}).get("width"), sizes.get("one_inch", {}).get("height"))
          == (295, 413), sizes.get("one_inch"))
    check("二寸为 413×579（35×49mm @300dpi）",
          (sizes.get("two_inch", {}).get("width"), sizes.get("two_inch", {}).get("height"))
          == (413, 579), sizes.get("two_inch"))

    colors = {c["key"]: c for c in d.get("colors", [])}
    check("底色含白 / 蓝 / 红 / 透明",
          {"white", "blue", "red", "transparent"} <= set(colors), list(colors))
    check("透明底不带 hex（保留 alpha 通道）",
          colors.get("transparent", {}).get("hex", "x") is None, colors.get("transparent"))
    check("默认尺寸为一寸、默认底色为白底",
          d.get("default_size") == "one_inch" and d.get("default_color") == "white",
          (d.get("default_size"), d.get("default_color")))

    print("\n[2] 本地抠图引擎（打包关键：模型 + onnxruntime）")
    eng = d.get("engines", {})
    local = eng.get("local", {})
    check("engines 结构完整（local / remote / default）",
          {"local", "remote", "default"} <= set(eng), list(eng))
    check("本地引擎可用（模型已内置 + onnxruntime 可导入）",
          local.get("enabled") is True, local)
    check("本地引擎无不可用原因", not (local.get("reason") or ""), local.get("reason"))
    check("默认走本地引擎（照片不出本机）",
          eng.get("default") == "local", eng.get("default"))

    # 真正的推理验证：preview 走 matting_only，成功即证明 ONNX 模型跑通了
    st, hd, raw = post_file("/api/idphoto/preview", admin, PHOTO)
    check("抠图预览返回图片（本地 ONNX 推理成功）",
          st == 200 and raw[:4] == b"\x89PNG", (st, raw[:40]))
    if st == 200 and raw[:4] == b"\x89PNG":
        im = Image.open(io.BytesIO(raw))
        check("抠图输出尺寸与输入一致（600×800）", im.size == (600, 800), im.size)
        check("抠图输出为 RGBA（带 alpha 通道）", im.mode == "RGBA", im.mode)
        alphas = im.split()[3].tobytes()
        check("alpha 通道有变化（真抠出了前景，不是全 0/全 255）",
              len(set(alphas)) > 1 and min(alphas) < 250,
              f"min={min(alphas)} max={max(alphas)} 种类={len(set(alphas))}")
    st, hd, r = post_file("/api/idphoto/preview", admin, b"not an image at all")
    check("预览传入非图片被拒（400）", st == 400, st)

    print("\n[3] 制作：尺寸")
    made = {}
    for key in ("one_inch", "two_inch", "original"):
        st, hd, raw = post_file("/api/idphoto/make", admin, PHOTO,
                                fields=(("size", key), ("color", "white"),
                                        ("engine", "local")))
        made[key] = (st, hd, raw)
        check(f"{key} 制作成功", st == 200 and len(raw) > 1000, (st, len(raw)))
        if st == 200:
            check(f"{key} 实际像素为 {EXPECT_SIZE[key][0]}×{EXPECT_SIZE[key][1]}",
                  img_size(raw) == EXPECT_SIZE[key], img_size(raw))
            check(f"{key} 响应头 X-IdPhoto-Size 与实际一致",
                  hd.get("X-IdPhoto-Size") == "x".join(str(v) for v in EXPECT_SIZE[key]),
                  hd.get("X-IdPhoto-Size"))
            check(f"{key} 走本地引擎（X-IdPhoto-Engine=local）",
                  hd.get("X-IdPhoto-Engine") == "local", hd.get("X-IdPhoto-Engine"))
            check(f"{key} 响应类型为 image/jpeg",
                  (hd.get("Content-Type") or "").startswith("image/jpeg"),
                  hd.get("Content-Type"))
            check(f"{key} 文件名按 RFC 5987 回传中文名",
                  "filename*=UTF-8''" in (hd.get("Content-Disposition") or ""),
                  hd.get("Content-Disposition"))

    print("\n[4] 制作：换底色")
    for key, rgb in EXPECT_RGB.items():
        st, hd, raw = post_file("/api/idphoto/make", admin, PHOTO,
                                fields=(("size", "one_inch"), ("color", key),
                                        ("engine", "local")))
        check(f"{key} 底制作成功", st == 200 and len(raw) > 1000, (st, len(raw)))
        if st == 200:
            got = corner_rgb(raw)
            check(f"{key} 底角落像素为 {rgb}",
                  all(abs(a - b) <= 2 for a, b in zip(got, rgb)), got)

    st, hd, raw_t = post_file("/api/idphoto/make", admin, PHOTO,
                              fields=(("size", "one_inch"), ("color", "transparent"),
                                      ("engine", "local")))
    check("透明底制作成功", st == 200 and raw_t[:4] == b"\x89PNG", (st, raw_t[:20]))
    if st == 200 and raw_t[:4] == b"\x89PNG":
        check("透明底响应类型为 image/png",
              (hd.get("Content-Type") or "").startswith("image/png"),
              hd.get("Content-Type"))
        tim = Image.open(io.BytesIO(raw_t))
        check("透明底输出为 RGBA", tim.mode == "RGBA", tim.mode)
        check("透明底角落 alpha 为 0（真的是透明底，不是白底）",
              tim.convert("RGBA").getpixel((0, 0))[3] == 0,
              tim.convert("RGBA").getpixel((0, 0)))

    st, hd, raw_w = post_file("/api/idphoto/make", admin, PHOTO,
                              fields=(("size", "one_inch"), ("color", "white"),
                                      ("engine", "local")))
    st2, hd2, raw_b = post_file("/api/idphoto/make", admin, PHOTO,
                                fields=(("size", "one_inch"), ("color", "blue"),
                                        ("engine", "local")))
    check("不同底色产出内容不同（底色真的生效了）", raw_w != raw_b, "白底与蓝底字节相同")

    print("\n[5] 不换底（keep）快路径")
    st, hd, raw_k = post_file("/api/idphoto/make", admin, PHOTO,
                              fields=(("size", "one_inch"), ("color", "keep")))
    check("不换底 + 一寸制作成功", st == 200 and len(raw_k) > 1000, (st, len(raw_k)))
    check("不换底不触发抠图（X-IdPhoto-Engine=none）",
          hd.get("X-IdPhoto-Engine") == "none", hd.get("X-IdPhoto-Engine"))
    if st == 200:
        check("不换底仍按一寸排版（295×413）", img_size(raw_k) == (295, 413), img_size(raw_k))
        # contain 排版必然留边（一寸 0.714 的比例对不上手机直出的 0.75），
        # 留边必须沿用照片自身的边缘色，不能硬套一圈白框
        src_top = Image.open(io.BytesIO(PHOTO)).convert("RGB").getpixel((0, 0))
        got = corner_rgb(raw_k)
        check("留白边沿用照片边缘色，不是硬套白框", got != (255, 255, 255), got)
        check("留白边与原背景自然衔接（色差 ≤ 40）",
              all(abs(a - b) <= 40 for a, b in zip(got, src_top)), (got, src_top))
        check("不换底与白底抠图结果不同（走的是免抠图路径）", raw_k != raw_w, "两者字节相同")
        mid = Image.open(io.BytesIO(raw_k)).convert("RGB").crop((120, 180, 200, 260))
        # getcolors 返回 None 表示颜色数超过 maxcolors → 同样是「非纯色」
        cols = mid.getcolors(maxcolors=200000)
        check("不换底保留了原图内容（中间区域非纯色）", cols is None or len(cols) > 1,
              "颜色种类 %s" % (len(cols) if cols else ">200000"))

    st, hd, raw_ok = post_file("/api/idphoto/make", admin, PHOTO,
                               fields=(("size", "original"), ("color", "keep")))
    check("不换底 + 原尺寸制作成功", st == 200, st)
    check("原尺寸 + 不换底原样返回（不做任何重编码）",
          st == 200 and raw_ok == PHOTO, len(raw_ok))

    print("\n[6] 引擎选择与降级")
    st, hd, raw = post_file("/api/idphoto/make", admin, PHOTO,
                            fields=(("size", "one_inch"), ("color", "white"),
                                    ("engine", "auto")))
    check("engine=auto 走本地引擎", st == 200 and hd.get("X-IdPhoto-Engine") == "local",
          hd.get("X-IdPhoto-Engine"))

    # 远程引擎：先指向一个必然连不上的地址，验证失败会给出中文原因而不是 500
    st, hd, r = jcall("PUT", "/api/system/settings", admin,
                      body={"idphoto": {"remote_url": "http://127.0.0.1:9/nope"}})
    check("可配置远程引擎地址", st == 200 and r.get("code") == 0, r)
    st, hd, r = post_file("/api/idphoto/make", admin, PHOTO,
                          fields=(("size", "one_inch"), ("color", "white"),
                                  ("engine", "remote")))
    check("远程引擎不可达时明确报错（400，非 500）", st == 400, st)
    try:
        msg = json.loads(r.decode("utf-8")).get("message", "")
    except Exception:
        msg = r[:120].decode("utf-8", "ignore")
    check("远程失败原因可读（含「远程」字样）", "远程" in msg, msg)
    st, hd, r = jcall("PUT", "/api/system/settings", admin,
                      body={"idphoto": {"remote_url": ""}})
    check("恢复远程引擎配置", st == 200 and r.get("code") == 0, r)

    print("\n[7] 异常与权限")
    st, hd, r = post_file("/api/idphoto/make", admin, b"")
    check("空文件被拒（400）", st == 400, st)
    st, hd, r = post_file("/api/idphoto/make", admin, b"MZ\x90\x00 definitely not a photo")
    check("非图片内容被拒（400）", st == 400, st)

    huge = Image.new("RGB", (6200, 200), (180, 180, 180))
    buf = io.BytesIO()
    huge.save(buf, "JPEG", quality=60)
    st, hd, r = post_file("/api/idphoto/make", admin, buf.getvalue())
    check("边长超过 6000 的照片被拒（400）", st == 400, st)

    st, hd, r = post_file("/api/idphoto/make", None, PHOTO)
    check("未登录调用制作接口被拒（401）", st == 401, st)
    st, hd, r = jcall("GET", "/api/idphoto/specs")
    check("未登录读取规格被拒（401）", st == 401, st)

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
