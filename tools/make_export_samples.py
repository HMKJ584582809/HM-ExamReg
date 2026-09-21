# -*- coding: utf-8 -*-
"""从运行中的服务导出两种考试类型的示例文件（用于交付演示）。"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8802"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")


def call(method, path, token=None, body=None, raw=False):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        return data if raw else json.loads(data.decode("utf-8"))


def main():
    token = call("POST", "/api/auth/login",
                 body={"account": "reviewer", "password": "Reviewer@123"})["data"]["access_token"]
    exams = call("GET", "/api/exams?page_size=100", token=token)["data"]["list"]
    OUT.mkdir(parents=True, exist_ok=True)
    for e in exams:
        if e["status"] not in ("open", "closed"):
            continue
        name = ("导出示例-计算机类考试-报名导入模板.xlsx" if e["exam_type"] == "computer"
                else "导出示例-普通话水平测试-考生报名模板.xlsx")
        target = OUT / name
        if target.exists():
            continue
        qs = urllib.parse.urlencode({"exam_id": e["id"], "audit_status": "all"})
        content = call("GET", "/api/export/download?" + qs, token=token, raw=True)
        target.write_bytes(content)
        print("已导出：", target, "%.1f KB" % (len(content) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
