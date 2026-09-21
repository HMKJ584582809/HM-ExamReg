# -*- coding: utf-8 -*-
"""老库升级验证：旧版 users 表（无 photo 列）升级到新版后应保留账号并补上列。

交付必须支持既有部署直接覆盖升级，故每次动 users 表结构都要跑一遍：
构造「缺列」的旧库 → 调 init_db() → 校验列已补、账号数据不丢。

用法：
    python tools/verify_migration.py
"""
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 旧版 users 表：没有 photo 列（模拟升级前现场）
OLD_USERS = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    real_name TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    role TEXT NOT NULL DEFAULT 'candidate',
    id_number TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    college TEXT DEFAULT '',
    class_name TEXT DEFAULT '',
    classes TEXT DEFAULT '',
    perms TEXT DEFAULT '',
    status INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {extra}"))


def main():
    data_dir = ROOT / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    db_path = data_dir / "app.db"

    print("=" * 66)
    print("老库升级验证（users 表缺 photo 列）\n")

    conn = sqlite3.connect(str(db_path))
    conn.executescript(OLD_USERS)
    conn.execute(
        "INSERT INTO users(username,password_hash,real_name,phone,email,role,grade,college,"
        "class_name,classes,perms,status,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
        ("oldadmin", "pbkdf2_sha256$200000$abcdefgh$x", "老库管理员", "13900000000",
         "old@test.com", "admin", "2026级", "计算机学院", "计科1班", "计科1班", "",
         "2026-01-01 00:00:00", "2026-01-01 00:00:00"))
    conn.commit()
    before_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    conn.close()
    check("旧库已构造且无 photo 列", "photo" not in before_cols, before_cols)

    # 触发迁移
    from backend.app import db
    db.init_db()

    conn = sqlite3.connect(str(db_path))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    check("升级后 photo 列已补上", "photo" in cols, cols)

    row = conn.execute(
        "SELECT username, real_name, role, college, class_name, classes, perms, photo"
        " FROM users WHERE username='oldadmin'").fetchone()
    check("老账号完整保留", bool(row), "账号丢失！")
    if row:
        check("账号字段未串位（角色/院系/班级/权限）",
              row[2] == "admin" and row[3] == "计算机学院" and row[4] == "计科1班"
              and row[6] == "", row)
        check("新增 photo 列默认为空", (row[7] or "") == "", row[7])

    # 其它表应被正常创建（老库可能只有 users）
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    need = {"users", "exams", "applications_computer", "applications_mandarin", "audits"}
    check("缺失的业务表已自动补齐", need.issubset(tables), sorted(need - tables))
    conn.close()

    print("\n" + "=" * 66)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
