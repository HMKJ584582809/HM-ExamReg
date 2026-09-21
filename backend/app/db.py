# -*- coding: utf-8 -*-
"""SQLite 数据访问层：建表、字典导入、演示数据初始化。"""
import json
import random
import sqlite3
import threading
from datetime import datetime, timedelta

from .config import (DB_PATH, DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME,
                     LOCAL_DEFAULTS, SEED_DEMO, ensure_dirs, load_dicts)

_lock = threading.RLock()
_local = threading.local()      # 每线程一条 SQLite 连接（见 get_conn 的说明）
_all_conns = []                 # 已建连接，供 close_all() 统一关闭
_conn = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    real_name TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    role TEXT NOT NULL DEFAULT 'candidate'
        CHECK (role IN ('candidate','head_teacher','college_reviewer','reviewer','admin')),
    id_number TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    college TEXT DEFAULT '',
    department TEXT DEFAULT '',
    class_name TEXT DEFAULT '',
    classes TEXT DEFAULT '',
    perms TEXT DEFAULT '',
    photo TEXT DEFAULT '',
    scope TEXT DEFAULT '',
    status INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_id_number
    ON users(id_number) WHERE id_number <> '';

CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL,
    operator_id INTEGER NOT NULL,
    operator_name TEXT DEFAULT '',
    filename TEXT DEFAULT '',
    total INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    row_no INTEGER NOT NULL,
    name TEXT DEFAULT '',
    id_number TEXT DEFAULT '',
    reason TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_import_err_batch ON import_errors(batch_id);

CREATE TABLE IF NOT EXISTS exam_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    base_type TEXT NOT NULL,
    description TEXT DEFAULT '',
    fields_config TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    sort INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

/* 通用模板的自定义字段定义：每个类型一套，报名值存在 applications_generic.extra */
CREATE TABLE IF NOT EXISTS exam_type_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type_code TEXT NOT NULL,
    field_key TEXT NOT NULL,
    label TEXT NOT NULL,
    field_type TEXT NOT NULL DEFAULT 'text',
    required INTEGER NOT NULL DEFAULT 0,
    options TEXT DEFAULT '',
    placeholder TEXT DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (type_code, field_key)
);

CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    exam_type TEXT NOT NULL,
    exam_year INTEGER NOT NULL,
    exam_month INTEGER NOT NULL,
    signup_start_at TEXT NOT NULL,
    signup_end_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','open','closed','archived')),
    description TEXT DEFAULT '',
    -- 本场考试的照片要求：尺寸 one_inch/two_inch/original，底色 white/blue/red/transparent；
    -- 空串表示「不限制」。取值与 app/idphoto/specs.py 保持一致（唯一事实源）
    photo_size TEXT DEFAULT '',
    photo_color TEXT DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications_computer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    org_code TEXT DEFAULT '',
    org_name TEXT DEFAULT '',
    exam_site_code TEXT DEFAULT '',
    name TEXT NOT NULL,
    gender TEXT DEFAULT '',
    id_type TEXT DEFAULT '1',
    id_number TEXT NOT NULL,
    subject TEXT DEFAULT '',
    school TEXT DEFAULT '',
    class_name TEXT DEFAULT '',
    education TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    address TEXT DEFAULT '',
    audit_status TEXT NOT NULL DEFAULT 'pending',
    audit_comment TEXT DEFAULT '',
    reviewed_by INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (exam_id, user_id)
);

CREATE TABLE IF NOT EXISTS applications_mandarin (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    gender TEXT DEFAULT '',
    ethnicity TEXT DEFAULT '',
    id_type TEXT DEFAULT '1',
    id_number TEXT NOT NULL,
    occupation TEXT DEFAULT '',
    employer TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    student_no TEXT DEFAULT '',
    class_name TEXT DEFAULT '',
    department TEXT DEFAULT '',
    contact_address TEXT DEFAULT '',
    mail_address TEXT DEFAULT '',
    postcode TEXT DEFAULT '',
    birth_province TEXT DEFAULT '',
    birth_city TEXT DEFAULT '',
    birth_county TEXT DEFAULT '',
    live_province TEXT DEFAULT '',
    live_city TEXT DEFAULT '',
    live_county TEXT DEFAULT '',
    audit_status TEXT NOT NULL DEFAULT 'pending',
    audit_comment TEXT DEFAULT '',
    reviewed_by INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (exam_id, user_id)
);

/* 通用考试报名模板：固定核心字段 + extra(JSON) 保存自定义字段的值 */
CREATE TABLE IF NOT EXISTS applications_generic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    gender TEXT DEFAULT '',
    id_type TEXT DEFAULT '1',
    id_number TEXT NOT NULL,
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    class_name TEXT DEFAULT '',
    college TEXT DEFAULT '',
    extra TEXT DEFAULT '',
    audit_status TEXT NOT NULL DEFAULT 'pending',
    audit_comment TEXT DEFAULT '',
    reviewed_by INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (exam_id, user_id)
);

CREATE TABLE IF NOT EXISTS audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_type TEXT NOT NULL,
    app_id INTEGER NOT NULL,
    reviewer_id INTEGER NOT NULL,
    reviewer_name TEXT DEFAULT '',
    action TEXT NOT NULL CHECK (action IN ('approved','rejected','returned')),
    comment TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dict_exam_org (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dict_subject (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS dict_occupation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
-- 二级学院：院系字段（报名院系 + 账号院系）的候选值
CREATE TABLE IF NOT EXISTS dict_college (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
-- 部门：账号所属部门（users.department）的候选值
-- college = 归属二级学院（空串表示校级/通用）；结构是「二级学院下再分部门」的平铺单层，
-- 不做树形嵌套。name 仍全局唯一，因此「学工办（辅导员）」「专职教师」这类学院内岗位
-- 各只存一条（适用任意学院），不按学院重复展开。
CREATE TABLE IF NOT EXISTS dict_department (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    college TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS dict_region (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    province TEXT NOT NULL,
    city TEXT DEFAULT '',
    county TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS dict_id_type (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
-- 找回密码申请：只有「图形验证码」方式且身份证核验未通过时才落到这里，
-- 由管理员在后台审核后手动重置（审核结果不回写密码，只记录处理结论）
CREATE TABLE IF NOT EXISTS password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 0,
    username TEXT NOT NULL,
    real_name TEXT DEFAULT '',
    id_number TEXT DEFAULT '',
    contact TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected')),
    admin_id INTEGER DEFAULT 0,
    admin_name TEXT DEFAULT '',
    admin_note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    handled_at TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_pwreset_status ON password_resets(status);

-- 实名信息：一人一条，重复提交走更新而不是插新行
CREATE TABLE IF NOT EXISTS realname_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    real_name TEXT NOT NULL DEFAULT '',
    id_type TEXT NOT NULL DEFAULT 'mainland_id',
    id_number TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected')),
    channel TEXT NOT NULL DEFAULT 'self',      -- self / alipay / wechat
    provider_uid TEXT DEFAULT '',              -- 第三方返回的唯一标识（脱敏后存储）
    trans_id TEXT DEFAULT '',                  -- 第三方认证流水号
    reviewer_id INTEGER DEFAULT 0,
    reviewer_name TEXT DEFAULT '',
    review_note TEXT DEFAULT '',
    submitted_at TEXT NOT NULL,
    reviewed_at TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_realname_status ON realname_profiles(status);

CREATE INDEX IF NOT EXISTS idx_appc_exam ON applications_computer(exam_id, audit_status);
CREATE INDEX IF NOT EXISTS idx_appm_exam ON applications_mandarin(exam_id, audit_status);
CREATE INDEX IF NOT EXISTS idx_appg_exam ON applications_generic(exam_id, audit_status);
CREATE INDEX IF NOT EXISTS idx_etf_type ON exam_type_fields(type_code, sort);
CREATE INDEX IF NOT EXISTS idx_audits_app ON audits(app_type, app_id);
CREATE INDEX IF NOT EXISTS idx_region_pc ON dict_region(province, city);
"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn() -> sqlite3.Connection:
    """当前线程的连接（按需创建，每线程一条）。

    ⚠ 这里**不能**退回「全局单连接 + check_same_thread=False」：
    FastAPI 把 `def` 接口丢进线程池执行，前端又常常并发发好几个请求
    （用户管理页就同时打 /api/users 与 /api/users/stats），
    两条 SQL 在同一条共享连接上交错执行会抛
    ``sqlite3.InterfaceError: bad parameter or other API misuse``，
    表现为某个接口**随机 500**、单线程重放却永远正常。
    改成线程各持一条连接后跨线程共享彻底消失；WAL 模式下多连接并发读不互相阻塞，
    写操作仍由 `_lock` 串行（见 execute()）。
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    with _lock:
        ensure_dirs()
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
        _all_conns.append(conn)
        # 主线程第一条连接沿用 _conn，兼容历史上直接引用 db._conn 的写法
        if _conn is None:
            globals()["_conn"] = conn
    return conn


def close_all():
    """关闭所有线程连接（退出/重建库前调用；正常退出靠 GC 也行）。"""
    with _lock:
        for c in list(_all_conns):
            try:
                c.close()
            except Exception:
                pass
        _all_conns.clear()
        globals()["_conn"] = None
        if hasattr(_local, "conn"):
            _local.conn = None


def query(sql: str, params=()) -> list:
    cur = get_conn().execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return [dict(r) for r in rows]


def query_one(sql: str, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=()):
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        lastrowid = cur.lastrowid
        cur.close()
        return lastrowid


def executemany(sql: str, seq):
    with _lock:
        conn = get_conn()
        cur = conn.executemany(sql, seq)
        conn.commit()
        cur.close()


# ---------------------------------------------------------------- 初始化

# 二级学院 / 部门**一律不预置**，三个清单都保持空。
#
# ⚠ 别再往里填具体院校的名字：这份代码是公开的（GitHub 公开仓库），
#   预置什么就等于把使用单位的组织架构、院系设置、内设部门全抖出去。
#   部署方在「系统维护 → 字典维护」里按自己的情况录入即可，
#   录入后不会被重启覆盖（_seed_dict_defaults 只在表为空时写）。
#
# 历史上这里放过某校的 9 个二级学院 + 18 个部门，已全部移除。
DEFAULT_COLLEGES: list[str] = []

# 校级部门默认候选值（同上，不预置）
DEFAULT_DEPARTMENTS: list[str] = []

# 二级学院内部再分的两类岗位：学工办（辅导员）与专职教师（同上，不预置）
DEFAULT_COLLEGE_DEPARTMENTS: list[str] = []


def _ensure_department_college_col():
    """老库 dict_department 缺 college 列时补上（新库由 SCHEMA 直接建好）。

    用 ADD COLUMN 而不是重建表：加列是 SQLite 原生支持的轻量操作，
    重建反而要处理 UNIQUE 约束与既有数据搬运，风险更高。
    """
    try:
        cols = {r["name"] for r in query("PRAGMA table_info(dict_department)")}
    except Exception:
        return
    if "college" in cols:
        return
    execute("ALTER TABLE dict_department ADD COLUMN college TEXT DEFAULT ''")


def _ensure_exam_photo_cols():
    """老库 exams 缺 photo_size / photo_color 时补上。

    ⚠ 不能改 `_drop_type_checks()` 里那段硬编码的 exams 建表 DDL：
    它用于给带 CHECK 的老库重建表，列数必须与原表一致（12 列），
    否则 `INSERT INTO exams SELECT * FROM exams_old` 会因列数不匹配直接报错。
    老库的这两个字段统一由本函数 ADD COLUMN 补上。
    """
    try:
        cols = {r["name"] for r in query("PRAGMA table_info(exams)")}
    except Exception:
        return
    for col in ("photo_size", "photo_color"):
        if col not in cols:
            execute(f"ALTER TABLE exams ADD COLUMN {col} TEXT DEFAULT ''")


def _migrate_perm_groups():
    """权限细化迁移：给「单独授权过」的老账号补齐新拆出来的权限组。

    背景：字典维护原先借 exam_manage 的门、系统维护借 user_manage 的门。
    拆成 dict_manage / system_manage 之后，沿用角色默认的账号会自动拿到新组，
    但**单独授权过**的账号存的是旧清单，会突然少了这些能力。
    这里按 PERM_MIGRATE_MAP 补一遍，保证升级前后行为一致。
    """
    try:
        from ..permissions import PERM_MIGRATE_MAP, FUNC_GROUPS
    except Exception:
        return
    changed = 0
    for r in query("SELECT id, perms FROM users WHERE perms IS NOT NULL AND perms<>''"):
        try:
            cur = json.loads(r["perms"] or "[]")
        except Exception:
            continue
        if not isinstance(cur, list):
            continue
        add = []
        for new, olds in PERM_MIGRATE_MAP.items():
            if new in cur:
                continue
            if any(o in cur for o in olds):
                add.append(new)
        # 证件照 / 实名：老版本对所有登录用户开放，这里保持开放
        for g in ("idphoto", "realname", "ai"):
            if g not in cur and g in FUNC_GROUPS:
                add.append(g)
        if not add:
            continue
        cur = [p for p in cur if p in FUNC_GROUPS or str(p).startswith("scope_")]
        cur += [g for g in add if g not in cur]
        execute("UPDATE users SET perms=? WHERE id=?",
                (json.dumps(cur, ensure_ascii=False), r["id"]))
        changed += 1
    if changed:
        print(f"[migrate] 权限组细化：已为 {changed} 个账号补齐新权限组")


def _seed_dict_defaults():
    """写入二级学院 / 部门默认值（当前三个清单都是空的，等于什么也不写）。

    只在表为空时写入 —— 管理员之后自行增删改不会被重启覆盖。
    保留这个函数是为了部署方想预置时，改上面的 DEFAULT_* 常量即可，不用动调用点。
    """
    # 源码里的 DEFAULT_* 恒为空；本单位部署时由仓库外的 local_defaults.json 追加，
    # 这样「开源版零预置」和「本地版开箱即用」能共用同一份代码。
    plan = (("dict_college", DEFAULT_COLLEGES + LOCAL_DEFAULTS.get("colleges", [])),
            ("dict_department", DEFAULT_DEPARTMENTS + DEFAULT_COLLEGE_DEPARTMENTS
             + LOCAL_DEFAULTS.get("departments", [])
             + LOCAL_DEFAULTS.get("college_departments", [])))
    for tbl, names in plan:
        try:
            row = query_one(f"SELECT COUNT(*) c FROM {tbl}")
        except Exception:
            continue          # 表还没建好（理论上不会），跳过
        if row and row["c"]:
            continue          # 已有数据，尊重管理员的改动
        for nm in names:
            try:
                execute(f"INSERT OR IGNORE INTO {tbl}(name) VALUES(?)", (nm,))
            except Exception:
                pass


def init_db():
    ensure_dirs()
    conn = get_conn()
    with _lock:
        conn.executescript(SCHEMA)
        conn.commit()
    # 先清掉已废弃角色再迁移：否则老库里的非法角色值会在重建表时被新 CHECK 约束卡住
    _purge_removed_roles()
    _drop_type_checks()
    _ensure_generic_schema()
    _migrate()
    _seed_exam_types()
    _import_dicts()
    _ensure_department_college_col()   # 老库补 college 列
    _ensure_exam_photo_cols()          # 老库补考试照片要求字段
    _migrate_perm_groups()      # 权限细化：给老账号补齐新拆出来的权限组
    _seed_dict_defaults()      # 二级学院 / 部门默认值（仅在表为空时写入）
    _seed_admin()
    if SEED_DEMO:
        _seed_demo_users()
        _seed_exams_and_apps()


def _migrate():
    """轻量迁移：老版本 users 表缺少管理范围 / 证件号字段、或缺少 classes 列时重建。"""
    cols = {r["name"] for r in query("PRAGMA table_info(users)")}
    need = {"grade", "college", "department", "class_name", "id_number", "classes",
            "perms", "photo", "scope"}
    if need.issubset(cols):
        return
    conn = get_conn()
    with _lock:
        conn.execute("ALTER TABLE users RENAME TO users_old")
        conn.executescript(SCHEMA.split("CREATE TABLE IF NOT EXISTS exams")[0])
        keep = [c for c in ("id", "username", "password_hash", "real_name", "phone", "email",
                            "role", "id_number", "grade", "college", "department", "class_name",
                            "classes", "perms", "photo", "scope", "status", "created_at",
                            "updated_at") if c in cols]
        conn.execute(
            f"INSERT INTO users({','.join(keep)}) SELECT {','.join(keep)} FROM users_old")
        # 已有班主任：把原单班级 class_name 迁移进 classes，保证历史数据不丢
        if "class_name" in cols and "classes" not in cols:
            for r in query("SELECT id, class_name FROM users_old WHERE role='head_teacher'"):
                cn = (r["class_name"] or "").strip()
                if cn:
                    conn.execute("UPDATE users SET classes=? WHERE id=?", (cn, r["id"]))
        conn.execute("DROP TABLE users_old")
        conn.commit()


def _drop_type_checks():
    """老库 exams.exam_type / audits.app_type 带 CHECK 限定两种类型，
    会挡住自定义类型编码。这里重建两表去掉 CHECK（数据全量保留）。"""
    specs = (
        ("exams", "exam_type", """CREATE TABLE exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    exam_type TEXT NOT NULL,
    exam_year INTEGER NOT NULL,
    exam_month INTEGER NOT NULL,
    signup_start_at TEXT NOT NULL,
    signup_end_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','open','closed','archived')),
    description TEXT DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""),
        ("audits", "app_type", """CREATE TABLE audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_type TEXT NOT NULL,
    app_id INTEGER NOT NULL,
    reviewer_id INTEGER NOT NULL,
    reviewer_name TEXT DEFAULT '',
    action TEXT NOT NULL CHECK (action IN ('approved','rejected','returned')),
    comment TEXT DEFAULT '',
    created_at TEXT NOT NULL
)"""),
    )
    for tbl, col, ddl in specs:
        row = query_one("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
        if not row or not row["sql"]:
            continue
        if col + " IN ('computer','mandarin')" not in row["sql"]:
            continue
        conn = get_conn()
        with _lock:
            conn.execute(f"ALTER TABLE {tbl} RENAME TO {tbl}_old")
            conn.execute(ddl)
            conn.execute(f"INSERT INTO {tbl} SELECT * FROM {tbl}_old")
            conn.execute(f"DROP TABLE {tbl}_old")
            if tbl == "audits":
                conn.execute("CREATE INDEX IF NOT EXISTS idx_audits_app ON audits(app_type, app_id)")
            conn.commit()


def _ensure_generic_schema():
    """老库 exam_types.base_type 带 CHECK 限定两种模板，会挡住新增的 generic。

    这里重建该表去掉 CHECK（数据全量保留）；两张新表由 SCHEMA 的
    CREATE TABLE IF NOT EXISTS 负责补齐。
    """
    row = query_one("SELECT sql FROM sqlite_master WHERE type='table' AND name='exam_types'")
    sql = (row or {}).get("sql") or ""
    if "base_type IN ('computer','mandarin')" not in sql:
        return
    ddl = """CREATE TABLE exam_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    base_type TEXT NOT NULL,
    description TEXT DEFAULT '',
    fields_config TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    sort INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""
    conn = get_conn()
    with _lock:
        conn.execute("ALTER TABLE exam_types RENAME TO exam_types_old")
        conn.execute(ddl)
        conn.execute("INSERT INTO exam_types SELECT * FROM exam_types_old")
        conn.execute("DROP TABLE exam_types_old")
        conn.commit()


def _seed_exam_types():
    """内置两种考试类型；管理员可在后台新增更多类型。"""
    ts = now_str()
    presets = (
        ("computer", "计算机类考试", "computer", "全国计算机等级考试等计算机类考试", 10),
        ("mandarin", "普通话水平测试", "mandarin", "普通话水平测试报名", 20),
        ("generic", "通用考试报名", "generic",
         "通用模板：固定核心字段 + 可自由增删的自定义字段", 30),
    )
    for code, name, base, desc, sort in presets:
        if query_one("SELECT id FROM exam_types WHERE code=?", (code,)):
            continue
        execute("INSERT INTO exam_types(code,name,base_type,description,fields_config,"
                "enabled,is_builtin,sort,created_at,updated_at) VALUES(?,?,?,?,'',1,1,?,?,?)",
                (code, name, base, desc, sort, ts, ts))


def _purge_removed_roles():
    """删除已废弃的角色（如辅导员），避免孤立账号卡在权限校验。幂等、仅影响被移除角色。"""
    try:
        execute("DELETE FROM users WHERE role='counselor'")
    except Exception:
        pass


def _seed_admin():
    """正式版本只保留一个初始管理员账号；其余账号由管理员在后台开通。"""
    from .security import hash_password
    if query_one("SELECT COUNT(*) AS c FROM users")["c"] > 0:
        return
    ts = now_str()
    execute(
        "INSERT INTO users(username,password_hash,real_name,phone,email,role,status,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
        (DEFAULT_ADMIN_USERNAME, hash_password(DEFAULT_ADMIN_PASSWORD), "系统管理员",
         "", "", "admin", ts, ts))


def _seed_demo_users():
    from .security import hash_password
    ts = now_str()
    presets = [
        ("reviewer", "Reviewer@123", "审核员-演示1", "13800000002", "reviewer@exam.local",
         "reviewer", "", "", "", ""),
        ("reviewer2", "Reviewer@123", "审核员-演示2", "13800000003", "reviewer2@exam.local",
         "reviewer", "", "", "", ""),
        ("teacher", "Teacher@123", "班主任-演示1", "13800000005", "teacher@exam.local",
         "head_teacher", "2022级", "计算机学院", "计算机2101,软件工程2202", ""),
        ("teacher2", "Teacher@123", "班主任-演示2", "13800000006", "teacher2@exam.local",
         "head_teacher", "2022级", "外国语学院", "英语2201,翻译2201", ""),
        # 二级学院审核：只看本院系（计算机学院），介于班主任（本班）与审核员（全校）之间
        # ⚠ 演示账号一律用「演示N」占位，不放任何真实姓名
        ("college", "College@123", "二级学院审核-演示3", "13800000007", "college@exam.local",
         "college_reviewer", "", "计算机学院", "", ""),
    ]
    for u, p, rn, ph, em, role, grade, college, classes, klass in presets:
        if query_one("SELECT id FROM users WHERE username=?", (u,)):
            continue
        execute(
            "INSERT INTO users(username,password_hash,real_name,phone,email,role,grade,college,"
            "class_name,classes,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (u, hash_password(p), rn, ph, em, role, grade, college, klass, classes, ts, ts))
    names = ["张三", "李四", "王五", "赵六", "陈晨", "刘洋", "周敏", "吴磊", "郑爽", "孙悦",
             "马晓", "朱琳", "胡兵", "林芳", "何伟", "高翔", "罗静", "梁宇", "宋佳", "谢涛",
             "唐宁", "许阳", "邓丽", "冯超", "曾毅", "彭亮", "蒋雯", "韩雪", "董明", "袁华",
             "潘婷", "于洋", "蒋磊", "蔡明", "余霞", "杜鹏", "叶青", "程勇", "苏晴", "魏强"]
    if not query_one("SELECT id FROM users WHERE username=?", ("candidate",)):
        execute(
            "INSERT INTO users(username,password_hash,real_name,phone,email,role,status,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
            ("candidate", hash_password("Candidate@123"), "演示考生-张三", "13900000001",
             "candidate@exam.local", "candidate", ts, ts))
    classes = ["计算机2101", "软件工程2202", "网络工程2301", "大数据2201", "人工智能2302"]
    colleges = ["计算机学院", "文学院", "外国语学院", "教育学院", "商学院"]
    seq = 100
    for i, nm in enumerate(names):
        seq += 1
        if query_one("SELECT id FROM users WHERE username=?", (f"stu{seq}",)):
            continue
        execute(
            "INSERT INTO users(username,password_hash,real_name,phone,email,role,grade,college,"
            "class_name,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
            (f"stu{seq}", hash_password("Student@123"), nm, f"139{seq:08d}",
             f"stu{seq}@exam.local", "candidate", "2022级",
             colleges[i % len(colleges)], classes[i % len(classes)], ts, ts))


def _seed_exams_and_apps():
    if query_one("SELECT COUNT(*) AS c FROM exams")["c"] > 0:
        return


def _import_dicts():
    d = load_dicts()
    if query_one("SELECT COUNT(*) AS c FROM dict_exam_org")["c"] == 0:
        executemany("INSERT OR IGNORE INTO dict_exam_org(code,name) VALUES(?,?)",
                    [(o["code"], o["name"]) for o in d["computer"]["orgs"]])
    if query_one("SELECT COUNT(*) AS c FROM dict_subject")["c"] == 0:
        executemany("INSERT OR IGNORE INTO dict_subject(name) VALUES(?)",
                    [(s,) for s in d["computer"]["subjects"]])
    if query_one("SELECT COUNT(*) AS c FROM dict_occupation")["c"] == 0:
        executemany("INSERT OR IGNORE INTO dict_occupation(name) VALUES(?)",
                    [(s,) for s in d["mandarin"]["occupations"]])
    if query_one("SELECT COUNT(*) AS c FROM dict_id_type")["c"] == 0:
        executemany("INSERT OR IGNORE INTO dict_id_type(code,name) VALUES(?,?)",
                    [(i["code"], i["name"]) for i in d["id_types"]])
    if query_one("SELECT COUNT(*) AS c FROM dict_region")["c"] == 0:
        rows = []
        for r in d["mandarin"]["regions"]:
            if not r["cities"]:
                rows.append((r["province"], "", ""))
                continue
            for c in r["cities"]:
                if not c["counties"]:
                    rows.append((r["province"], c["name"], ""))
                for county in c["counties"]:
                    rows.append((r["province"], c["name"], county))
        executemany("INSERT INTO dict_region(province,city,county) VALUES(?,?,?)", rows)


def _fake_id(rnd) -> str:
    """生成能通过 GB 11643 校验的演示身份证号。

    早期演示数据用随机数拼 18 位，校验位与省份代码几乎必然非法，
    会让「AI 数据体检」把整库都判成异常，失去演示意义。
    """
    from .validators import _ID_CHECK_CODES, _ID_PROVINCES, _ID_WEIGHTS
    prov = rnd.choice(sorted(_ID_PROVINCES))
    y, m, d = rnd.randint(1975, 2005), rnd.randint(1, 12), rnd.randint(1, 28)
    body = f"{prov}{rnd.randint(1, 99):02d}{rnd.randint(1, 99):02d}{y}{m:02d}{d:02d}" \
           f"{rnd.randint(1, 999):03d}"
    total = sum(int(body[i]) * _ID_WEIGHTS[i] for i in range(17))
    return body + _ID_CHECK_CODES[total % 11]


def _gender_of(id_number: str) -> str:
    """按身份证第 17 位奇偶取性别，保证演示数据自洽。"""
    return "男" if len(id_number) == 18 and int(id_number[16]) % 2 else "女"


def _fake_reviewed_at(rnd, created_dt: "datetime") -> str:
    """演示用的审核完成时间：在提交时间上加一段真实感的延迟。

    早期演示数据直接把 reviewed_at 写成等于 created_at，
    导致「平均审核耗时」恒为 0、时效分布全落在「1 小时内」，
    大屏和分析页的时效指标看着像坏了。这里按 0.3 小时~5 天分档抽样。
    """
    r = rnd.random()
    if r < 0.40:
        hours = rnd.uniform(0.3, 6)        # 当天审完
    elif r < 0.70:
        hours = rnd.uniform(6, 24)         # 次日审完
    elif r < 0.90:
        hours = rnd.uniform(24, 72)        # 拖到 1-3 天
    else:
        hours = rnd.uniform(72, 120)       # 少数积压 3 天以上
    dt = created_dt + timedelta(hours=hours)
    now = datetime.now()
    # 不能超过当前时间，否则会出现「未来的审核时间」
    return min(dt, now).strftime("%Y-%m-%d %H:%M:%S")


def _seed_demo_photos(rate: float = 0.78):
    """给一部分演示考生生成占位证件照，让「证件照完整率」有真实分布。

    演示库里一张照片都没有时，完整率恒为 0%、缺照片名单等于全部考生，
    看不出这个功能在正常状态下长什么样。
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:      # 没有 Pillow 就不造照片，功能本身不受影响
        return 0
    try:
        from .config import PHOTO_DIR
    except ImportError:
        return 0
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(20260916)
    users = query("SELECT id,real_name FROM users WHERE role='candidate'")
    made = 0
    for u in users:
        if rnd.random() > rate:
            continue
        name = f"u{u['id']}.jpg"
        path = PHOTO_DIR / name
        if path.exists():
            execute("UPDATE users SET photo=? WHERE id=? AND (photo='' OR photo IS NULL)",
                    (name, u["id"]))
            made += 1
            continue
        # 浅蓝底 + 灰色人像剪影，一眼能认出是「占位证件照」
        img = Image.new("RGB", (295, 413), (rnd.randint(210, 235), 232, 245))
        dr = ImageDraw.Draw(img)
        dr.ellipse((92, 70, 203, 181), fill=(150, 158, 175))     # 头
        dr.ellipse((60, 190, 235, 380), fill=(120, 128, 148))    # 肩
        img.save(path, "JPEG", quality=82)
        execute("UPDATE users SET photo=? WHERE id=?", (name, u["id"]))
        made += 1
    return made


# 模拟数据（性能 / 压力测试用）：用户名前缀，便于一键清除
MOCK_PREFIX = "mock_"


def seed_mock_data(count: int = 5000) -> dict:
    """批量模拟数据：**只用于性能 / 压力测试**。

    按「不需要生成完整数据」的要求：只填必要字段、executemany 批量写入，
    全部挂在同一场考试下，便于列表 / 导出 / 统计的压测。
    """
    count = max(1, min(int(count or 5000), 50000))
    row = query_one("SELECT id, exam_type FROM exams ORDER BY id LIMIT 1")
    if not row:
        raise RuntimeError("尚无考试，请先创建一场考试再生成模拟数据")
    exam_id, exam_type = row["id"], row["exam_type"]
    from .exam_types import table_of
    tbl = table_of(exam_type)
    ts = now_str()
    rnd = random.Random()
    tag = "%06x" % rnd.randrange(0, 16 ** 6)     # 每次运行不同，避免与历史数据重名
    # 复用已有密码哈希：避免为上万个账号逐个做慢哈希（压测不关心密码）
    ph = (query_one("SELECT password_hash FROM users ORDER BY id LIMIT 1")
          or {}).get("password_hash") or ""
    base_phone = rnd.randrange(0, 90000000)

    users = []
    for i in range(count):
        seq = f"{tag}{i:05d}"
        users.append((f"{MOCK_PREFIX}{seq}", ph, f"模拟考生{seq}",
                      f"137{(base_phone + i) % 100000000:08d}", "",
                      "candidate", ts, ts))
    executemany(
        "INSERT INTO users(username,password_hash,real_name,phone,email,role,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?)", users)

    ids = [r["id"] for r in query("SELECT id FROM users WHERE username LIKE ?",
                                  (f"{MOCK_PREFIX}{tag}%",))]
    # 三套模板的报名表列不同 → 按目标表实际列裁剪，只写共有或存在的字段
    cols = {r["name"] for r in query(f"PRAGMA table_info({tbl})")}
    keys = [k for k in ("exam_id", "user_id", "name", "gender", "id_type", "id_number",
                        "school", "class_name", "phone", "audit_status", "audit_comment",
                        "reviewed_by", "reviewed_at", "created_at", "updated_at") if k in cols]
    sql = f"INSERT INTO {tbl}({','.join(keys)}) VALUES({','.join(['?'] * len(keys))})"
    apps = []
    for uid in ids:
        st = rnd.choice(("pending", "approved", "approved", "approved", "rejected"))
        vals = {"exam_id": exam_id, "user_id": uid, "name": f"模拟考生{uid}",
                "gender": rnd.choice(("男", "女")), "id_type": "1",
                "id_number": _fake_id(rnd), "school": "模拟学院",
                "class_name": "模拟班级",
                "phone": f"137{(base_phone + uid) % 100000000:08d}",
                "audit_status": st, "audit_comment": "", "reviewed_by": None,
                "reviewed_at": ts if st != "pending" else None,
                "created_at": ts, "updated_at": ts}
        apps.append(tuple(vals[k] for k in keys))
    executemany(sql, apps)
    return {"users": len(ids), "apps": len(apps), "exam_id": exam_id, "table": tbl}


def clear_mock_data() -> int:
    """清除模拟数据（按用户名前缀识别），用于压测后恢复干净环境。"""
    ids = [r["id"] for r in query("SELECT id FROM users WHERE username LIKE ?",
                                  (MOCK_PREFIX + "%",))]
    if not ids:
        return 0
    from .exam_types import APP_TABLE
    for start in range(0, len(ids), 400):       # 分批，避免 IN (...) 参数过多
        chunk = ids[start:start + 400]
        marks = ",".join(["?"] * len(chunk))
        for tbl in set(APP_TABLE.values()):
            execute(f"DELETE FROM {tbl} WHERE user_id IN ({marks})", chunk)
        execute(f"DELETE FROM users WHERE id IN ({marks})", chunk)
    return len(ids)


def mock_count() -> int:
    """当前库里模拟考生数量（供后台显示）。"""
    r = query_one("SELECT COUNT(*) c FROM users WHERE username LIKE ?", (MOCK_PREFIX + "%",))
    return (r or {}).get("c", 0) or 0


def _seed_exams_and_apps():
    if query_one("SELECT COUNT(*) AS c FROM exams")["c"] > 0:
        return
    d = load_dicts()
    ts = now_str()
    now = datetime.now()
    exams = [
        # name, type, year, month, 报名开始, 报名截止, 状态, 说明
        (f"{now.year}年{now.month}月计算机类考试", "computer", now.year, now.month,
         (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"),
         (now + timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S"), "open",
         "计算机类技术水平考试报名，请如实填写个人信息，报名信息需经审核。"),
        (f"{now.year}年{now.month}月普通话水平测试", "mandarin", now.year, now.month,
         (now - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S"),
         (now + timedelta(days=22)).strftime("%Y-%m-%d %H:%M:%S"), "open",
         "普通话水平测试报名，请准确填写出生地与现居住地（省/市/县区三级）。"),
        (f"{now.year}年{now.month - 1 if now.month > 1 else 12}月计算机类考试", "computer",
         now.year if now.month > 1 else now.year - 1, now.month - 1 if now.month > 1 else 12,
         (now - timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S"),
         (now - timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S"), "closed",
         "上一批次计算机类考试，报名窗口已关闭。"),
        (f"{now.year}年{now.month - 1 if now.month > 1 else 12}月普通话水平测试", "mandarin",
         now.year if now.month > 1 else now.year - 1, now.month - 1 if now.month > 1 else 12,
         (now - timedelta(days=50)).strftime("%Y-%m-%d %H:%M:%S"),
         (now - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S"), "closed",
         "上一批次普通话水平测试，报名窗口已关闭。"),
        (f"{now.year}年{now.month + 1 if now.month < 12 else 1}月普通话水平测试", "mandarin",
         now.year if now.month < 12 else now.year + 1, now.month + 1 if now.month < 12 else 1,
         (now + timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S"),
         (now + timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S"), "draft",
         "下月批次（草稿），尚未发布。"),
    ]
    for e in exams:
        execute("INSERT INTO exams(name,exam_type,exam_year,exam_month,signup_start_at,signup_end_at,"
                "status,description,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,1,?,?)",
                (*e, ts, ts))

    # 生成演示报名数据（保证分析/大屏有真实分布）
    rnd = random.Random(20260916)
    orgs = d["computer"]["orgs"]
    subjects = d["computer"]["subjects"]
    occs = d["mandarin"]["occupations"]
    regions = [r for r in d["mandarin"]["regions"] if r["cities"]]
    eths = d["ethnicities"]
    edus = d["educations"]
    users = query("SELECT id,username,real_name,phone,college,class_name FROM users"
                  " WHERE role='candidate' ORDER BY id")
    exams_open = query("SELECT * FROM exams WHERE exam_type='computer' AND status IN ('open','closed')")
    exams_md = query("SELECT * FROM exams WHERE exam_type='mandarin' AND status IN ('open','closed')")

    def pick_status():
        r = rnd.random()
        if r < 0.60:
            return "approved"
        if r < 0.80:
            return "pending"
        if r < 0.90:
            return "rejected"
        return "returned"

    comments = {
        "approved": "信息核对无误，审核通过。",
        "rejected": "证件号码与姓名不匹配，请核实后重新报名。",
        "returned": "手机号码格式有误，请修改后重新提交。",
    }
    # ⚠ 不放真实院校名（这些会被打进演示数据，而成品是对外分发的）
    schools = [f"示例院校{i}" for i in range(1, 9)]
    classes = ["计算机2101", "软件工程2202", "网络工程2301", "大数据2201", "人工智能2302"]
    created = 0
    for exam in exams_open:
        for _ in range(46):
            u = rnd.choice(users)
            org = rnd.choice(orgs)
            st = pick_status()
            sid, gender = _fake_id(rnd), None
            gender = _gender_of(sid)
            created_dt = datetime.now() - timedelta(days=rnd.randint(0, 25),
                                                    hours=rnd.randint(0, 23))
            t = created_dt.strftime("%Y-%m-%d %H:%M:%S")
            rv = _fake_reviewed_at(rnd, created_dt) if st != "pending" else None
            try:
                execute(
                    "INSERT INTO applications_computer(exam_id,user_id,org_code,org_name,exam_site_code,"
                    "name,gender,id_type,id_number,subject,school,class_name,education,phone,email,address,"
                    "audit_status,audit_comment,reviewed_by,reviewed_at,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (exam["id"], u["id"], org["code"], org["name"],
                     f"{org['code']}{rnd.randint(1, 9):02d}", u["real_name"],
                     gender, "1", sid,
                     rnd.choice(subjects), u["college"] or rnd.choice(schools),
                     u["class_name"] or rnd.choice(classes),
                     rnd.choice(edus), u["phone"], f"{u['username']}@exam.local",
                     f"江苏省南京市鼓楼区{rnd.randint(1, 200)}号",
                     st, comments[st] if st != "pending" else "",
                     2 if st != "pending" else None, rv, t, t))
                created += 1
            except sqlite3.IntegrityError:
                pass
    for exam in exams_md:
        for _ in range(52):
            u = rnd.choice(users)
            rg = rnd.choice(regions)
            city = rnd.choice(rg["cities"])
            county = rnd.choice(city["counties"]) if city["counties"] else ""
            rg2 = rnd.choice(regions)
            city2 = rnd.choice(rg2["cities"])
            county2 = rnd.choice(city2["counties"]) if city2["counties"] else ""
            st = pick_status()
            sid = _fake_id(rnd)
            gender = _gender_of(sid)
            created_dt = datetime.now() - timedelta(days=rnd.randint(0, 25),
                                                    hours=rnd.randint(0, 23))
            t = created_dt.strftime("%Y-%m-%d %H:%M:%S")
            rv = _fake_reviewed_at(rnd, created_dt) if st != "pending" else None
            try:
                execute(
                    "INSERT INTO applications_mandarin(exam_id,user_id,name,gender,ethnicity,id_type,"
                    "id_number,occupation,employer,phone,student_no,class_name,department,contact_address,"
                    "mail_address,postcode,birth_province,birth_city,birth_county,live_province,live_city,"
                    "live_county,audit_status,audit_comment,reviewed_by,reviewed_at,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (exam["id"], u["id"], u["real_name"], gender,
                     rnd.choice(eths), "1", sid,
                     rnd.choice(occs), u["college"] or rnd.choice(schools), u["phone"],
                     f"2022{rnd.randint(10000,99999)}", u["class_name"] or rnd.choice(classes),
                     u["college"] or rnd.choice(["计算机学院", "文学院", "外国语学院",
                                                 "教育学院", "商学院"]),
                     f"{rg['province']}{city['name']}{county}{rnd.randint(1, 100)}号",
                     f"{rg2['province']}{city2['name']}{county2}{rnd.randint(1, 100)}号",
                     f"{rnd.randint(100000, 999999)}",
                     rg["province"], city["name"], county,
                     rg2["province"], city2["name"], county2,
                     st, comments[st] if st != "pending" else "",
                     2 if st != "pending" else None, rv, t, t))
                created += 1
            except sqlite3.IntegrityError:
                pass
    _seed_demo_photos()
    return created
