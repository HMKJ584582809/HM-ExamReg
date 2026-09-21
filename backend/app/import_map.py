"""智能导入：表头模糊识别 + 数据自动转换。

目标：用户可以直接上传「自己手里的表格」（花名册、教务导出、官方模板等），
不必严格对齐官方模板表头；系统自动识别每一列对应的字段，并把值转换成系统标准格式。

三层匹配（置信度递减）：
  1. 精确匹配   —— 归一化后与别名完全相同          conf = 1.00
  2. 包含匹配   —— 别名与表头互为子串              conf = 0.70~0.90
  3. 关键词匹配 —— 表头命中字段关键词              conf = 0.55

匹配结果一律可在前端预览界面手工调整，识别错了不会静默写错数据。
"""
import re

from . import db

# ------------------------------------------------------------------ 归一化

_PUNCT = re.compile(r"[\s\*（）()【】\[\]<>《》｛｛{}：:、，,；;/\\\-—_．.。'\"“”‘’?？!！]+")
_TAIL = re.compile(r"(必填|选填|非必填|必填项|选填项|如有|请填写|请如实填写|备注|说明)$")
_PAREN_BODY = re.compile(r"[（(【\[][^）)】\]]{0,12}[）)】\]]")


def norm(s) -> str:
    """表头归一化：去标点、空格、括号说明、必填后缀，并转小写。"""
    s = str(s or "").strip()
    s = _PAREN_BODY.sub("", s)          # 去掉「（必填）」「(选填)」等括号内容
    s = _PUNCT.sub("", s)
    s = _TAIL.sub("", s)
    return s.strip().lower()


# ------------------------------------------------------------------ 别名库

# 字段 -> 别名（越靠前越优先；表头千变万化，宁可多写也不要漏）
ALIASES = {
    # ---- 通用 ----
    "name": ["姓名", "考生姓名", "学生姓名", "名字", "中文姓名", "姓名name", "name",
             "学生名字", "姓名全称", "真实姓名"],
    "gender": ["性别", "性別", "考生性别", "学生性别", "gender", "sex"],
    "id_type": ["证件类型", "身份证件类型", "证件种类", "证件类别", "证件类型代码",
                "身份证件种类", "idtype"],
    "id_number": ["证件号码", "证件号", "身份证号", "身份证号码", "身份证", "证件编号",
                  "公民身份号码", "居民身份证号", "身份证编号", "证件号码id", "idcard",
                  "身份证号(必填)", "证件号(必填)"],
    "phone": ["手机号码", "手机号", "联系电话", "电话", "移动电话", "手机", "联系方式",
              "联系手机", "电话号码", "移动电话号", "手机联系方式", "联系电话手机",
              "tel", "phone", "mobile"],
    "email": ["电子邮箱", "邮箱", "电子邮件", "邮箱地址", "email", "mail", "e-mail",
              "电子邮件地址"],
    "class_name": ["班级", "所在班级", "班", "专业班级", "班级名称", "教学班", "行政班级",
                   "学生班级", "班次", "所在班", "班级(行政班)", "class"],
    "grade": ["年级", "所在年级", "就读年级", "学生年级", "年级名称", "所在级",
              "入学年级", "年级(入学年份)", "grade"],
    "student_no": ["学号", "学籍号", "学生学号", "学生证号", "编号", "studentno"],
    "postcode": ["邮政编码", "邮编", "邮编号码", "邮政编号", "zip", "postcode"],

    # ---- 计算机类 ----
    "org_code": ["考试机构编码", "机构编码", "考试机构代码", "机构代码", "考点机构编码",
                 "承办机构编码", "承办机构代码", "orgcode"],
    "exam_site_code": ["考点编码", "考点代码", "考点", "考试地点编码", "考点名称代码",
                       "考试地点代码", "sitecode"],
    "subject": ["报考科目", "考试科目", "科目", "报考科目名称", "考试项目", "报考项目",
                "subject"],
    "school": ["就读或毕业院校", "学校", "院校", "所在学校", "毕业院校", "就读院校",
               "学校名称", "就读学校", "毕业学校", "所在院校", "高校名称", "school",
               "学院", "院系", "所在院系", "所属院系", "系别", "系"],
    "education": ["学历", "文化程度", "教育程度", "学历层次", "最高学历", "education"],
    "address": ["通讯地址", "联系地址", "地址", "家庭住址", "住址", "通信地址",
                "现居住地址", "address"],

    # ---- 普通话 ----
    "ethnicity": ["民族", "考生民族", "民族成份", "族别", "民族类别", "ethnicity"],
    "occupation": ["从事职业", "职业", "现从事职业", "职业类别", "从事职业类别",
                   "occupation", "job"],
    "employer": ["所在单位", "工作单位", "单位", "就职单位", "所在单位名称", "工作单位名称",
                 "所属单位", "employer"],
    "department": ["院系", "所在院系", "学院", "系别", "所在学院", "专业学院", "系",
                   "所属院系", "department", "college"],
    "contact_address": ["联系地址", "通讯地址", "通信地址", "地址", "现居住地址",
                        "详细通讯地址", "contactaddress"],
    "mail_address": ["邮寄地址", "收件地址", "邮件地址", "通讯地址", "邮寄通讯地址",
                     "证书邮寄地址", "mailaddress"],
    "birth_province": ["出生地省", "出生省", "出生所在省", "籍贯省", "出生地省级",
                       "出生地(省)", "出生地省份", "籍贯省份", "birthprovince"],
    "birth_city": ["出生地市", "出生市", "出生所在市", "出生所在城市", "籍贯市", "出生地市级",
                   "出生地(市)", "出生地城市", "籍贯城市", "birthcity"],
    "birth_county": ["出生地县区", "出生县区", "出生地区县", "出生所在县区", "出生所在县(区)",
                     "籍贯县区", "出生地(县区)", "出生地区县", "籍贯区县", "birthcounty"],
    "live_province": ["现居住地省", "现居住省", "现居地省", "居住省", "现居住地省级",
                      "现居住地(省)", "现居省份", "常住省份", "liveprovince"],
    "live_city": ["现居住地市", "现居住市", "现居地市", "居住市", "现居住城市", "现所在地市",
                  "现居住地市级", "现居住地(市)", "现居城市", "常住城市", "livecity"],
    "live_county": ["现居住地县区", "现居住县区", "现居地县区", "居住县区", "现居住县区",
                    "现居住县(区)", "现所在地县区", "现居住地(县区)", "现居区县", "常住区县",
                    "livecounty"],

    # ---- 通用模板 ----
    "college": ["院系", "所在院系", "学院", "系别", "所在学院", "专业学院", "系",
                "所属院系", "department", "college", "就读院系"],
}

# 关键词兜底（表头命中即得分，用于别名库没覆盖的写法）
KEYWORDS = {
    "id_number": ["身份", "证件", "证号"],
    "phone": ["手机", "电话", "联系方式"],
    "email": ["邮箱", "邮件"],
    "name": ["姓名"],
    "gender": ["性别"],
    "class_name": ["班级"],
    "grade": ["年级"],
    "student_no": ["学号"],
    "postcode": ["邮编", "邮政"],
    "subject": ["科目"],
    "school": ["院校", "学校"],
    "department": ["院系", "学院"],
    "employer": ["单位"],
    "education": ["学历", "文化程度"],
    "ethnicity": ["民族"],
    "occupation": ["职业"],
    "org_code": ["机构编码", "机构代码"],
    "exam_site_code": ["考点"],
    "address": ["地址", "住址"],
    "contact_address": ["地址", "住址"],
    "mail_address": ["邮寄", "收件"],
    "birth_province": ["出生", "籍贯"],
    "live_province": ["现居", "居住", "常住"],
    "college": ["院系", "学院"],
}

# 说明：grade（年级）是「虚拟字段」——报名表没有年级列，也不会写进报名记录，
# 只用于在导入建号时把年级写到考生账号上，从而让「本年级」数据范围能覆盖到导入生。
COMPUTER_FIELDS = ["org_code", "exam_site_code", "name", "gender", "id_type", "id_number",
                   "subject", "school", "class_name", "grade", "education", "phone",
                   "email", "address"]
MANDARIN_FIELDS = ["name", "gender", "ethnicity", "id_type", "id_number", "occupation",
                   "employer", "phone", "student_no", "class_name", "grade", "department",
                   "contact_address", "mail_address", "postcode", "birth_province",
                   "birth_city", "birth_county", "live_province", "live_city", "live_county"]
# 通用模板：固定字段 + grade 虚拟字段；自定义字段由调用方通过 extra_fields 追加
GENERIC_FIELDS = ["name", "gender", "id_type", "id_number", "phone", "email",
                  "class_name", "college", "grade"]

FIELD_LABEL = {
    "org_code": "考试机构编码", "exam_site_code": "考点编码", "name": "姓名",
    "gender": "性别", "id_type": "证件类型", "id_number": "证件号码", "subject": "报考科目",
    "school": "就读或毕业院校", "class_name": "班级", "grade": "年级", "education": "学历",
    "phone": "手机号码", "email": "Email", "address": "通讯地址",
    "ethnicity": "考生民族", "occupation": "从事职业", "employer": "所在单位",
    "student_no": "学号", "department": "院系", "contact_address": "联系地址",
    "mail_address": "邮寄地址", "postcode": "邮政编码",
    "birth_province": "出生地省", "birth_city": "出生地市", "birth_county": "出生地县区",
    "live_province": "现居住地省", "live_city": "现居住地市", "live_county": "现居住地县区",
}


def fields_of(exam_type: str, extra_fields: list = None) -> list:
    """该基础类型的可导入字段。

    extra_fields：通用模板的自定义字段 key（动态定义，只能由调用方传入）。
    """
    if exam_type == "computer":
        base = list(COMPUTER_FIELDS)
    elif exam_type == "generic":
        base = list(GENERIC_FIELDS)
    else:
        base = list(MANDARIN_FIELDS)
    for f in (extra_fields or []):
        if f not in base:
            base.append(f)
    return base


# ------------------------------------------------------------------ 表头匹配

_NORM_ALIAS = {}


def _norm_aliases(f: str) -> list:
    if f not in _NORM_ALIAS:
        _NORM_ALIAS[f] = [norm(a) for a in ALIASES.get(f, []) if norm(a)]
    return _NORM_ALIAS[f]


def match_header(header, exam_type: str, extra_aliases: dict = None,
                 extra_fields: list = None):
    """返回 (字段 or None, 置信度 0~1)。

    extra_aliases：{字段: [归一化后的别名]}，用于把考试类型里被管理员改名的
    显示名也纳入识别（改名后导出的模板仍能自动识别列）。
    extra_fields：通用模板的自定义字段 key。
    """
    h = norm(header)
    if not h:
        return None, 0.0
    fields = fields_of(exam_type, extra_fields)
    best, best_conf = None, 0.0

    for f in fields:
        conf = 0.0
        for na in _norm_aliases(f) + ((extra_aliases or {}).get(f) or []):
            if not na:
                continue
            if h == na:
                conf = max(conf, 1.0)
            elif na in h or h in na:
                # 互为子串：按长度占比给分，越接近越可信
                ratio = min(len(h), len(na)) / max(len(h), len(na))
                conf = max(conf, 0.70 + 0.20 * ratio)
        if conf < 1.0:
            for kw in KEYWORDS.get(f, []):
                nk = norm(kw)
                if nk and nk in h:
                    conf = max(conf, 0.55)
        if conf > best_conf:
            best, best_conf = f, conf

    return (best, best_conf) if best_conf >= 0.55 else (None, 0.0)


def detect_header_row(rows: list, exam_type: str, extra_fields: list = None) -> int:
    """在前 5 行里挑最像表头的一行（有些文件首行是大标题）。"""
    best_idx, best_score = 0, -1
    for i, row in enumerate(rows[:5]):
        if not row:
            continue
        score = sum(1 for c in row if match_header(c, exam_type, None, extra_fields)[0])
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx if best_score > 0 else 0


def auto_map(headers, exam_type: str, field_labels: dict = None,
             extra_fields: list = None) -> dict:
    """返回 {列下标: {"field":..., "confidence":..., "header":...}}，同一字段只保留最优列。

    field_labels：{字段: 显示名}，把该考试类型自定义的名称也作为别名参与匹配。
    extra_fields：通用模板的自定义字段 key。
    """
    extra = {}
    for f, lb in (field_labels or {}).items():
        n = norm(lb)
        if n:
            extra[f] = [n]
    result, taken = {}, {}
    for idx, h in enumerate(headers or []):
        f, conf = match_header(h, exam_type, extra, extra_fields)
        if not f:
            continue
        prev = taken.get(f)
        if prev is None or conf > result[prev]["confidence"]:
            if prev is not None:
                result.pop(prev, None)
            result[idx] = {"field": f, "confidence": round(conf, 2),
                           "header": str(h or "")}
            taken[f] = idx
    return result


# ------------------------------------------------------------------ 值转换

_GENDER_MAP = {
    "男": "男", "男性": "男", "m": "男", "male": "男", "1": "男", "先生": "男", "boy": "男",
    "女": "女", "女性": "女", "f": "女", "female": "女", "2": "女", "女士": "女", "girl": "女",
}
_REGION_SUFFIX = re.compile(
    r"(壮族自治区|回族自治区|维吾尔自治区|特别行政区|自治区|自治州|自治县|自治旗|"
    r"地区|市辖区|特区|林区|新区|省|市|县|区|旗|盟|州|街道|镇|乡|村)$")

_id_type_cache = None
_region_cache = None
_dict_cache = {}


def _id_types() -> list:
    global _id_type_cache
    if _id_type_cache is None:
        try:
            _id_type_cache = db.query("SELECT code,name FROM dict_id_type")
        except Exception:
            _id_type_cache = []
    return _id_type_cache


def _dict_values(col: str, table: str) -> list:
    key = (col, table)
    if key not in _dict_cache:
        try:
            _dict_cache[key] = [r[col] for r in db.query(f"SELECT {col} FROM {table}")
                                if r[col]]
        except Exception:
            _dict_cache[key] = []
    return _dict_cache[key]


def _regions() -> list:
    global _region_cache
    if _region_cache is None:
        try:
            _region_cache = db.query("SELECT province,city,county FROM dict_region")
        except Exception:
            _region_cache = []
    return _region_cache


def _strip_region_suffix(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _REGION_SUFFIX.sub("", s)
    return s.strip()


def _canon_region(value, level: str, province: str = "", city: str = "") -> str:
    """把带后缀/别名的地名规范成字典里的标准名称；匹配不上则原样返回。"""
    s = str(value or "").strip()
    if not s:
        return ""
    raw = _strip_region_suffix(s)
    if not raw:
        return s
    # 优先精确匹配（含后缀也允许）
    for r in _regions():
        if level == "province":
            if r["province"] == s:
                return r["province"]
        elif level == "city":
            if r["city"] == s:
                return r["city"]
        else:
            if r["county"] == s:
                return r["county"]
    # 去后缀后匹配
    cands = []
    for r in _regions():
        if level == "province":
            v = r["province"]
        elif level == "city":
            if province and r["province"] != province:
                continue
            v = r["city"]
        else:
            if province and r["province"] != province:
                continue
            if city and r["city"] != city:
                continue
            v = r["county"]
        if not v:
            continue
        if _strip_region_suffix(v) == raw or raw in _strip_region_suffix(v) \
                or _strip_region_suffix(v) in raw:
            cands.append(v)
    return cands[0] if cands else s


def _best_match(s: str, candidates: list, code_of: dict = None, threshold: float = 0.5):
    """在候选值里按「重叠率」选最贴切的一项。

    重叠率 = min(len) / max(len)，优先完全相同，其次取重叠率最高者。
    这样「身份证」→「居民身份证」(3/5)，而「香港居民身份证」(7) 不会被短名抢走。
    """
    if not s or not candidates:
        return None
    for c in candidates:
        if c == s:
            return code_of[c] if code_of else c
    best, best_ratio = None, 0.0
    for c in candidates:
        if s in c or c in s:
            ratio = min(len(s), len(c)) / max(len(s), len(c))
            if ratio > best_ratio:
                best, best_ratio = c, ratio
    if best is None or best_ratio < threshold:
        return None
    return code_of[best] if code_of else best


def _fuzzy_dict(value, candidates) -> str:
    """在字典候选值里找最贴近的；匹配不上则原样返回（交给后续校验报错）。"""
    s = str(value or "").strip()
    if not s or not candidates:
        return s
    return _best_match(s, candidates) or s


def _clean_phone(v) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    if isinstance(v, float) and v.is_integer():
        s = str(int(v))
    s = re.sub(r"[\s\-()（）]", "", s)
    s = re.sub(r"^\+?86", "", s)
    return s


def _clean_id(v) -> str:
    s = str(v or "").strip()
    if isinstance(v, float) and v.is_integer():
        s = str(int(v))
    s = re.sub(r"[\s\-]", "", s)
    return s.upper()


def convert_value(field: str, value, exam_type: str, row: dict = None):
    """把单个值转换成系统标准格式，返回 (新值, 是否发生过转换)。"""
    row = row or {}
    if value is None:
        return "", False
    # Excel 日期单元格读出来是 datetime：统一成 YYYY-MM-DD，
    # 否则自定义字段的 date 类型会因「2024-01-01 00:00:00」校验失败
    if hasattr(value, "strftime"):
        try:
            value = value.strftime("%Y-%m-%d")
        except Exception:
            value = str(value)
    orig = value
    v = value

    if field == "gender":
        s = str(v).strip()
        v = _GENDER_MAP.get(s.lower(), _GENDER_MAP.get(s, s))
    elif field == "id_type":
        s = str(v).strip()
        if s and not s.isdigit():
            # 用「重叠率」选最贴切的一项，避免「香港居民身份证」被短名「居民身份证」抢先命中
            v = _best_match(s, [it["name"] for it in _id_types()],
                            {it["name"]: it["code"] for it in _id_types()}) or s
    elif field == "id_number":
        v = _clean_id(v)
    elif field == "phone":
        v = _clean_phone(v)
    elif field == "postcode":
        v = re.sub(r"\s+", "", str(v).strip())
    elif field == "subject":
        v = _fuzzy_dict(v, _dict_values("name", "dict_subject"))
    elif field == "occupation":
        v = _fuzzy_dict(v, _dict_values("name", "dict_occupation"))
    elif field == "education":
        v = _fuzzy_dict(v, ["博士研究生", "硕士研究生", "本科", "专科", "中专", "高中",
                            "初中", "其他"])
    elif field == "ethnicity":
        from .config import load_dicts
        v = _fuzzy_dict(v, load_dicts()["ethnicities"])
    elif field == "birth_province":
        v = _canon_region(v, "province")
    elif field == "birth_city":
        v = _canon_region(v, "city", province=str(row.get("birth_province") or "").strip())
    elif field == "birth_county":
        v = _canon_region(v, "county", province=str(row.get("birth_province") or "").strip(),
                          city=str(row.get("birth_city") or "").strip())
    elif field == "live_province":
        v = _canon_region(v, "province")
    elif field == "live_city":
        v = _canon_region(v, "city", province=str(row.get("live_province") or "").strip())
    elif field == "live_county":
        v = _canon_region(v, "county", province=str(row.get("live_province") or "").strip(),
                          city=str(row.get("live_city") or "").strip())
    else:
        v = re.sub(r"\s+", " ", str(v).strip()) if isinstance(v, str) else v

    return v, str(orig) != str(v)


def convert_row(data: dict, exam_type: str):
    """按依赖顺序转换整行（省市县有级联关系），返回 (新 data, 转换说明列表)。"""
    order = ["birth_province", "birth_city", "birth_county",
             "live_province", "live_city", "live_county"]
    notes = []
    out = dict(data)
    # 先转换非级联字段
    for k in list(out.keys()):
        if k in order:
            continue
        out[k], changed = convert_value(k, out.get(k), exam_type, out)
        if changed:
            notes.append(k)
    # 再按 省→市→县 顺序转换，保证上级已规范化
    for k in order:
        if k in out:
            out[k], changed = convert_value(k, out.get(k), exam_type, out)
            if changed:
                notes.append(k)
    return out, notes
