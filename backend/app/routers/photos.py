# -*- coding: utf-8 -*-
"""证件照：个人上传 / 按用户读取 / 批量导入匹配覆盖。

设计要点
--------
1. **文件存磁盘、库里只存文件名** `u{user_id}.{ext}`：
   - 几千张照片塞进 SQLite 会让库文件迅速膨胀、备份变慢；
   - 文件名不含身份证号等敏感信息，也不会出现在日志里；
   - 规避中文文件名在 Windows 上的编码问题。
2. **类型以 magic bytes 判定**，不信扩展名、不信 Content-Type，
   防止把其它内容改名成 .jpg 传上来。
3. **读取走 `/api/photos/{user_id}`**，由服务端查库映射真实文件，
   前端全程拿不到存储路径，天然杜绝路径穿越。
4. **批量导入两步走**（预览 → 确认），与报名数据导入体验一致，
   且预览结果落盘为 meta.json，确认时以预览为准，避免"预览 A、覆盖 B"。
"""
import io
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import db
from ..config import PHOTO_DIR, PHOTO_EXTS, PHOTO_MAX_BYTES, PHOTO_TMP_DIR
from ..deps import ApiError, get_current_user, ok
from .. import exam_types as ET
from ..permissions import (_college_column, can_access_user, effective_perms,
                            require_perms, scope_filter)

router = APIRouter(prefix="/api/photos", tags=["photos"])

# 服务端自己生成的文件名，读取时严格校验，杜绝 ../ 之类穿越
_SAFE_FILE = re.compile(r"^u\d+\.(jpg|jpeg|png)$")

# 匹配优先级：证件号 > 用户名/学号 > 手机号 > 姓名（越靠前越唯一）
_MATCH_FIELDS = ("id_number", "username", "phone", "real_name")
_FIELD_LABEL = {"id_number": "证件号码", "username": "用户名/学号",
                "phone": "手机号", "real_name": "姓名"}

MAX_FILES = 1000
TMP_TTL_SECONDS = 24 * 3600
MAX_EXPORT = 5000          # 单次导出上限，防止一次打几 GB 的包
EXPORT_TMP_TTL = 3600      # 导出临时 zip 保留 1 小时后清理


# ------------------------------------------------------------------ 基础工具

def _sniff(data: bytes) -> str:
    """按文件头判定真实图片类型；返回空串表示不是允许的图片。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    return ""


def _clear_old(user_id: int, keep_name: str = ""):
    """删除该用户其它扩展名的旧照片（换格式时避免残留双份）。"""
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    for e in PHOTO_EXTS:
        p = PHOTO_DIR / f"u{user_id}.{e}"
        if p.exists() and p.name != keep_name:
            try:
                p.unlink()
            except OSError:
                pass


def _can_view(viewer, target_id) -> bool:
    """本人可看；用户管理 / 审核 / 导入 权限者可看他人证件照（审核需核对身份）。"""
    if viewer and viewer.get("id") == target_id:
        return True
    owned = effective_perms(viewer) if viewer else []
    return any(p in owned for p in ("user_manage", "audit", "import"))


def _cleanup_tmp():
    """清理超过 TTL 的临时预览目录（用户预览后没点确认会残留）。"""
    if not PHOTO_TMP_DIR.exists():
        return
    now = time.time()
    for d in PHOTO_TMP_DIR.iterdir():
        if not d.is_dir():
            continue
        try:
            if now - d.stat().st_mtime > TMP_TTL_SECONDS:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _idphoto_make(data: bytes, size: str, color: str):
    """批量导入时按需排版/换底（延迟导入，未启用该功能时不拖慢启动）。"""
    from ..idphoto.maker import make
    return make(data, size_key=size or "", color_key=color or "")


def _match_user(key: str):
    """按优先级把文件名匹配到用户。

    返回 (status, user_row, field)：
      matched   唯一命中
      ambiguous 命中多人（多为重名）→ 拒绝覆盖，交人工
      unmatched 无命中
    """
    if not key:
        return "unmatched", None, ""
    for field in _MATCH_FIELDS:
        rows = db.query(f"SELECT id, username, real_name, id_number FROM users WHERE {field}=?",
                        (key,))
        if len(rows) == 1:
            return "matched", rows[0], field
        if len(rows) > 1:
            return "ambiguous", None, field
    return "unmatched", None, ""


# ------------------------------------------------------------------ 个人证件照

@router.post("/me")
async def upload_mine(file: UploadFile = File(...), user=Depends(get_current_user)):
    """上传 / 更换本人证件照（覆盖式，一个账号一张）。"""
    data = await file.read()
    if not data:
        raise ApiError("文件内容为空")
    if len(data) > PHOTO_MAX_BYTES:
        raise ApiError(f"图片不能超过 {PHOTO_MAX_BYTES // 1024 // 1024} MB")
    ext = _sniff(data)
    if not ext:
        raise ApiError("仅支持 JPG / PNG 格式的图片")

    fname = f"u{user['id']}.{ext}"
    _clear_old(user["id"], fname)
    (PHOTO_DIR / fname).write_bytes(data)
    db.execute("UPDATE users SET photo=?, updated_at=? WHERE id=?",
               (fname, db.now_str(), user["id"]))
    return ok({"photo": fname, "url": f"/api/photos/{user['id']}", "size": len(data)},
              "证件照已上传")


@router.delete("/me")
def delete_mine(user=Depends(get_current_user)):
    """删除本人证件照。"""
    _clear_old(user["id"])
    db.execute("UPDATE users SET photo='', updated_at=? WHERE id=?",
               (db.now_str(), user["id"]))
    return ok({"photo": ""}, "证件照已删除")


@router.get("/{user_id}")
def get_photo(user_id: int, user=Depends(get_current_user)):
    """读取指定用户的证件照。本人或有管理/审核/导入权限者可访问。"""
    if not _can_view(user, user_id):
        raise ApiError("无权查看该用户的证件照", code=403, status=403)
    row = db.query_one("SELECT photo FROM users WHERE id=?", (user_id,))
    if not row or not row.get("photo"):
        raise ApiError("该用户暂无证件照", code=404, status=404)

    fname = row["photo"]
    if not _SAFE_FILE.match(fname):
        raise ApiError("证件照文件名不合法", code=400)
    path = PHOTO_DIR / fname
    if not path.exists():
        raise ApiError("证件照文件已丢失，请重新上传", code=404, status=404)

    media = "image/png" if fname.endswith(".png") else "image/jpeg"
    return FileResponse(str(path), media_type=media)


# ------------------------------------------------------------------ 批量导入

@router.post("/import/preview")
async def import_preview(files: list[UploadFile] = File(...),
                         size: str = "", color: str = "",
                         user=Depends(require_perms("user_manage", "import"))):
    """批量上传证件照并预览匹配结果（不落正式位置）。

    文件名（不含扩展名）作为匹配键，按 证件号 > 用户名/学号 > 手机号 > 姓名 依次尝试；
    姓名重名会判为「多人匹配」并拒绝自动覆盖。

    ``size`` / ``color`` 非空时，每张照片先按规格排版换底再入库
    （与「证件照制作」同一套引擎），管理员不必事先在外面处理好。
    """
    if not files:
        raise ApiError("请先选择照片文件")
    if len(files) > MAX_FILES:
        raise ApiError(f"单次最多上传 {MAX_FILES} 张照片")
    if size and size not in ("one_inch", "two_inch", "original"):
        raise ApiError("证件照尺寸不合法")
    if color and color not in ("keep", "white", "blue", "red"):
        raise ApiError("证件照底色不合法")
    make_photo = bool(size or color)

    _cleanup_tmp()
    token = uuid.uuid4().hex[:16]
    tmp = PHOTO_TMP_DIR / token
    tmp.mkdir(parents=True, exist_ok=True)

    items, seen_keys = [], {}
    for idx, f in enumerate(files):
        raw_name = Path(f.filename or "").name          # 丢掉客户端可能带来的路径
        key = raw_name.rsplit(".", 1)[0].strip() if "." in raw_name else raw_name.strip()
        data = await f.read()

        ext = _sniff(data)
        if not ext:
            items.append({"filename": raw_name, "key": key, "status": "invalid",
                          "by": "", "by_label": "", "user_id": 0, "username": "",
                          "real_name": "", "note": "不是 JPG / PNG 图片"})
            continue
        if len(data) > PHOTO_MAX_BYTES:
            items.append({"filename": raw_name, "key": key, "status": "invalid",
                          "by": "", "by_label": "", "user_id": 0, "username": "",
                          "real_name": "",
                          "note": f"超过 {PHOTO_MAX_BYTES // 1024 // 1024} MB"})
            continue

        if make_photo:
            try:
                r = _idphoto_make(data, size, color)
                data, ext = r["data"], r["ext"]
            except Exception as e:
                items.append({"filename": raw_name, "key": key, "status": "invalid",
                              "by": "", "by_label": "", "user_id": 0, "username": "",
                              "real_name": "", "note": f"证件照处理失败：{e}"})
                continue
            if len(data) > PHOTO_MAX_BYTES:
                items.append({"filename": raw_name, "key": key, "status": "invalid",
                              "by": "", "by_label": "", "user_id": 0, "username": "",
                              "real_name": "", "note": "制作后超过 5 MB"})
                continue

        status, row, field = _match_user(key)
        # 同一键出现多次（如 张三.jpg / 张三.png）→ 判重复，不猜用哪个
        if status == "matched" and key in seen_keys:
            status, row, field = "duplicate", None, ""
        # 权限组只说明「能导」，还要看这个账号在不在自己的数据范围内：
        # 否则班主任拿批量导入就能按文件名给全校任何人换证件照
        if status == "matched" and not can_access_user(user, row):
            status, row, field = "forbidden", None, ""
        if status == "matched":
            seen_keys[key] = row["id"]
            (tmp / f"{idx}.{ext}").write_bytes(data)
            items.append({"filename": raw_name, "key": key, "status": "matched",
                          "by": field, "by_label": _FIELD_LABEL.get(field, field),
                          "user_id": row["id"], "username": row["username"],
                          "real_name": row["real_name"], "note": "", "ext": ext,
                          "file": f"{idx}.{ext}"})
        else:
            note = {"duplicate": "同一姓名/编号出现多张，未采用",
                    "ambiguous": "匹配到多个账号，未采用（多为重名）",
                    "unmatched": "未匹配到账号",
                    "forbidden": "该账号不在你的数据范围内"}.get(status, "")
            if status == "matched":
                (tmp / f"{idx}.{ext}").write_bytes(data)
            items.append({"filename": raw_name, "key": key, "status": status,
                          "by": field, "by_label": _FIELD_LABEL.get(field, field),
                          "user_id": 0, "username": "", "real_name": "", "note": note,
                          "ext": ext, "file": f"{idx}.{ext}"})

    counts = {st: sum(1 for i in items if i["status"] == st)
              for st in ("matched", "ambiguous", "unmatched", "duplicate",
                         "invalid", "forbidden")}
    (tmp / "meta.json").write_text(
        json.dumps({"items": [i for i in items if i["status"] == "matched"]},
                   ensure_ascii=False), encoding="utf-8")

    return ok({"token": token, "total": len(items), "counts": counts, "items": items})


class PhotoImportIn(BaseModel):
    token: str = ""


@router.post("/import")
def import_photos(body: PhotoImportIn, user=Depends(require_perms("user_manage", "import"))):
    """按预览结果落盘并覆盖 users.photo。只处理预览中 matched 的项。"""
    token = (body.token or "").strip()
    if not re.match(r"^[0-9a-f]{16}$", token):
        raise ApiError("预览凭证无效，请重新上传")
    tmp = PHOTO_TMP_DIR / token
    meta_path = tmp / "meta.json"
    if not meta_path.exists():
        raise ApiError("预览已过期，请重新上传")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    done, failed = 0, []
    for it in meta.get("items", []):
        src = tmp / it.get("file", "")
        if not src.exists():
            failed.append(it.get("filename", ""))
            continue
        uid, ext = it.get("user_id"), it.get("ext") or "jpg"
        fname = f"u{uid}.{ext}"
        try:
            _clear_old(uid, fname)
            shutil.move(str(src), str(PHOTO_DIR / fname))
            db.execute("UPDATE users SET photo=?, updated_at=? WHERE id=?",
                       (fname, db.now_str(), uid))
            done += 1
        except Exception as e:                       # 单张失败不影响整批
            failed.append(f"{it.get('filename', '')}（{e}）")

    shutil.rmtree(tmp, ignore_errors=True)
    return ok({"updated": done, "failed": failed},
              f"已覆盖 {done} 张证件照" + (f"，{len(failed)} 张失败" if failed else ""))


# ------------------------------------------------------------------ 一键导出
#
# 目录：{考试批次}/{院系}/{班级}/{证件号码}.{jpg|png}
# 命名用证件号码（唯一且是官方口径），同目录重名自动加 _2 后缀。
# 打包成 zip 下载：目录结构只有压缩包能表达。

def _q(s: str) -> str:
    return quote(s)


def _safe_name(s: str, default: str = "未填写") -> str:
    """清洗为合法的目录名（去 Windows 非法字符与收尾点号）。"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "", (s or "").strip()).strip()
    return s.rstrip(".").strip() or default


def _table_of(exam_type: str) -> str:
    if exam_type == "computer":
        return "applications_computer"
    if exam_type == "mandarin":
        return "applications_mandarin"
    if exam_type == "generic":
        return "applications_generic"
    raise ApiError("报名类型不合法")


def _export_rows(exam_id: int, exam_type: str, audit_status: str, user,
                 class_name: str = "") -> list:
    """取该批次报名记录（含建目录与命名所需字段），并叠加数据范围过滤。"""
    tbl = _table_of(exam_type)
    where, params = ["exam_id=?"], [exam_id]
    if audit_status and audit_status != "all":
        where.append("audit_status=?")
        params.append(audit_status)
    if class_name:
        where.append("class_name=?")
        params.append(class_name)
    frag, sparams = scope_filter(user, tbl)
    return db.query(
        f"SELECT id,user_id,name,id_number,class_name,"
        f"{_college_column(tbl)} AS college FROM {tbl}"
        f" WHERE {' AND '.join(where)}{frag} ORDER BY class_name,id",
        tuple(params + sparams))


def _distinct_classes(exam_id: int, exam_type: str, user) -> list:
    """该批次（在数据范围内）出现过的班级，供超上限时按班级分批导出。"""
    tbl = _table_of(exam_type)
    frag, sparams = scope_filter(user, tbl)
    rows = db.query(
        f"SELECT DISTINCT class_name FROM {tbl} WHERE exam_id=?{frag} ORDER BY class_name",
        tuple([exam_id] + sparams))
    return [r["class_name"] for r in rows if (r["class_name"] or "").strip()]


def _photo_map(user_ids: list) -> dict:
    """批量取 users.photo，避免逐条查库。"""
    out = {}
    ids = [i for i in user_ids if i]
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        ph = ",".join("?" for _ in chunk)
        for r in db.query(f"SELECT id,photo,username FROM users WHERE id IN ({ph})", tuple(chunk)):
            out[r["id"]] = r
    return out


def _convert_image(data: bytes, fmt: str) -> bytes:
    """统一转成目标格式；已是目标格式则原样返回（不做无谓重编码）。"""
    if _sniff(data) == fmt:
        return data
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    if fmt == "jpg":
        # PNG 有透明通道，直接转 JPEG 会报错，先铺白底
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=92)
        return buf.getvalue()
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _cleanup_export_tmp():
    root = PHOTO_TMP_DIR
    if not root.exists():
        return
    now = time.time()
    for p in root.glob("photoexport*"):
        try:
            if now - p.stat().st_mtime > EXPORT_TMP_TTL:
                p.unlink()
        except OSError:
            pass


class PhotoExportIn(BaseModel):
    exam_id: int = 0
    fmt: str = "jpg"          # jpg / png
    audit_status: str = ""    # 空或 all = 全部
    class_name: str = ""      # 空 = 全部班级；用于超 MAX_EXPORT 时按班级分批


@router.get("/export/stat")
def export_stat(exam_id: int = Query(0), audit_status: str = Query(""),
                class_name: str = Query(""),
                user=Depends(require_perms("export"))):
    """导出前的数量预览：共多少条、其中多少人有证件照。"""
    if not exam_id:
        raise ApiError("请选择考试批次")
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)
    rows = _export_rows(exam_id, ET.base_type_of(exam["exam_type"]), audit_status, user, class_name)
    umap = _photo_map([r["user_id"] for r in rows])
    with_photo = 0
    for r in rows:
        u = umap.get(r["user_id"] or 0)
        fname = (u or {}).get("photo") or ""
        if fname and _SAFE_FILE.match(fname) and (PHOTO_DIR / fname).exists():
            with_photo += 1
    return ok({"exam_id": exam_id, "exam_name": exam["name"],
               "total": len(rows), "with_photo": with_photo,
               "without_photo": len(rows) - with_photo,
               "classes": _distinct_classes(exam_id, ET.base_type_of(exam["exam_type"]), user)},
              f"共 {len(rows)} 条报名，{with_photo} 人已上传证件照")


@router.post("/export")
def export_photos(body: PhotoExportIn, user=Depends(require_perms("export"))):
    """一键导出证件照：按 考试批次 / 院系 / 班级 逐级建目录，文件名用证件号码。"""
    exam_id = int(body.exam_id or 0)
    if not exam_id:
        raise ApiError("请选择考试批次")
    fmt = (body.fmt or "jpg").strip().lower()
    fmt = "jpg" if fmt == "jpeg" else fmt
    if fmt not in ("jpg", "png"):
        raise ApiError("导出格式只能是 JPG 或 PNG")
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)

    rows = _export_rows(exam_id, ET.base_type_of(exam["exam_type"]), body.audit_status or "", user,
                        (body.class_name or "").strip())
    if not rows:
        raise ApiError("该批次没有可导出的报名记录")
    if len(rows) > MAX_EXPORT:
        raise ApiError(f"单次最多导出 {MAX_EXPORT} 条，请按班级或审核状态分批导出")

    _cleanup_export_tmp()
    umap = _photo_map([r["user_id"] for r in rows])
    root = _safe_name(exam["name"], f"考试{exam_id}")

    PHOTO_TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="photoexport", suffix=".zip",
                                        dir=str(PHOTO_TMP_DIR))
        os.close(fd)
        done, missing, used = 0, [], set()

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for r in rows:
                uid = r["user_id"] or 0
                u = umap.get(uid)
                fname = (u or {}).get("photo") or ""
                idno = (r["id_number"] or "").strip()
                college = _safe_name(r["college"])
                klass = _safe_name(r["class_name"])

                if not uid:
                    missing.append([r["name"], idno, college, klass, "未关联账号（导入时未建号）"])
                    continue
                if not fname or not _SAFE_FILE.match(fname):
                    missing.append([r["name"], idno, college, klass, "未上传证件照"])
                    continue
                src = PHOTO_DIR / fname
                if not src.exists():
                    missing.append([r["name"], idno, college, klass, "证件照文件已丢失"])
                    continue

                # 证件号虽已由 ID_RE 限为字母数字，仍清洗一次，避免拼出越界路径
                base = _safe_name(idno, default="") or f"u{uid}"
                rel = f"{root}/{college}/{klass}/{base}.{fmt}"
                n = 2
                while rel.lower() in used:                 # 同目录重名加后缀
                    rel = f"{root}/{college}/{klass}/{base}_{n}.{fmt}"
                    n += 1
                used.add(rel.lower())
                try:
                    data = _convert_image(src.read_bytes(), fmt)
                except Exception as e:                     # 单张失败不中断整批
                    missing.append([r["name"], idno, college, klass, f"格式转换失败：{e}"])
                    continue

                zi = zipfile.ZipInfo(rel, date_time=time.localtime()[:6])
                zi.flag_bits |= 0x800                      # 标记 UTF-8，避免中文名乱码
                zi.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zi, data)
                done += 1

            # 附一份说明与缺照片名单，方便补拍
            lines = [
                "证件照导出说明",
                f"考试批次：{exam['name']}",
                f"导出时间：{db.now_str()}",
                f"导出格式：{fmt.upper()}",
                f"目录结构：{root} / 院系 / 班级 / 证件号码.{fmt}",
                f"应导出 {len(rows)} 条，成功 {done} 张，未导出 {len(missing)} 人",
                "",
            ]
            if missing:
                lines.append("未导出名单（姓名 / 证件号码 / 院系 / 班级 / 原因）：")
                for m in missing:
                    lines.append("  " + " / ".join(str(x) for x in m))
            zi = zipfile.ZipInfo(f"{root}/_导出说明.txt", date_time=time.localtime()[:6])
            zi.flag_bits |= 0x800
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, "\n".join(lines).encode("utf-8-sig"))

        if not done:
            raise ApiError(f"该批次没有可导出的证件照（{len(missing)} 人未上传）")

        name = f"证件照-{root}.zip"

        def _iter():
            try:
                with open(tmp_path, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        yield chunk
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        return StreamingResponse(
            _iter(), media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=utf-8''{_q(name)}",
                     "X-Exported-Count": str(done),
                     "X-Missing-Count": str(len(missing))})
    except Exception:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise
