# -*- coding: utf-8 -*-
"""重新生成「导出示例-*.xlsx」模板（含合法示例数据行）。

用途
----
字典或导入模板结构变化后，重新生成带示例数据的模板文件。
示例数据**全部满足后端校验**（身份证含正确校验位、手机号合法、字典值有效），
用户照示例填写即可直接导入成功——这点与旧示例不同，旧示例的身份证号是随机数，
开启身份证合法性校验后反而无法导入。

用法：
  python tools/make_template_samples.py <输出目录> [每类示例行数]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import load_dicts                    # noqa: E402
from app.routers.export import build_workbook        # noqa: E402

# 省份名 -> 身份证前两位代码（与示例数据里的省保持一致）
PROV_CODE = {"北京市": "11", "江苏省": "32", "浙江省": "33", "广东省": "44", "四川省": "51"}
_W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_C = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]

NAMES = ["张伟", "王芳", "李强", "刘洋", "陈静", "杨帆", "赵磊", "黄敏",
         "周涛", "吴桐", "徐静", "孙浩", "马丽", "朱林", "胡军"]
CLASSES = ["计算机2101", "软件工程2202", "网络工程2301", "大数据2201", "人工智能2302"]
COLLEGES = ["计算机学院", "文学院", "外国语学院", "教育学院", "商学院"]
POSTCODES = ["210000", "100000", "310000", "510000", "610000"]


def mk_id(prov_name, birth, seq):
    """生成校验位合法的居民身份证号（GB 11643）。"""
    code = PROV_CODE.get(prov_name, "32")
    pre = "%s0101%s%03d" % (code, birth, seq)   # 6 位地址 + 8 位生日 + 3 位顺序 = 17
    return pre + _C[sum(int(pre[i]) * _W[i] for i in range(17)) % 11]


def pick_region(d, idx):
    r = d["mandarin"]["regions"][idx % len(d["mandarin"]["regions"])]
    city = r["cities"][0]
    county = (city.get("counties") or [""])[0] or ""
    return r["province"], city["name"], county


def computer_rows(d, n):
    orgs = d["computer"]["orgs"]
    subjects = d["computer"]["subjects"]
    edus = d["educations"]
    rows = []
    for i in range(n):
        org = orgs[i % len(orgs)]["code"]
        prov = "江苏省"
        rows.append({
            "org_code": org,
            "exam_site_code": "%s01" % org,
            "name": NAMES[i % len(NAMES)],
            "gender": "男" if i % 2 == 0 else "女",
            "id_type": "1",
            "id_number": mk_id(prov, "20030101", 100 + i),
            "subject": subjects[i % len(subjects)],
            "school": COLLEGES[i % len(COLLEGES)],
            "class_name": CLASSES[i % len(CLASSES)],
            "education": edus[2 + (i % 3)],
            "phone": "139%08d" % (10000001 + i),
            "email": "sample%d@exam.local" % (i + 1),
            "address": "江苏省南京市鼓楼区%d号" % (10 + i),
        })
    return rows


def mandarin_rows(d, n):
    occs = d["mandarin"]["occupations"]
    eths = d["ethnicities"]
    rows = []
    for i in range(n):
        prov, city, county = pick_region(d, i)
        rows.append({
            "name": NAMES[(i + 5) % len(NAMES)],
            "gender": "女" if i % 2 == 0 else "男",
            "ethnicity": eths[i % len(eths)] if i % 3 else "汉族",
            "id_type": "1",
            "id_number": mk_id(prov, "20030506", 200 + i),
            "occupation": occs[i % len(occs)],
            "employer": "%s示例单位" % prov,
            "phone": "138%08d" % (20000001 + i),
            "student_no": "2021%04d" % (1001 + i),
            "class_name": CLASSES[i % len(CLASSES)],
            "department": COLLEGES[i % len(COLLEGES)],
            "contact_address": "%s%s%s示例路%d号" % (prov, city, county, 20 + i),
            "mail_address": "%s%s%s示例大道%d号" % (prov, city, county, 30 + i),
            "postcode": POSTCODES[i % len(POSTCODES)],
            "birth_province": prov, "birth_city": city, "birth_county": county,
            "live_province": prov, "live_city": city, "live_county": county,
        })
    return rows


SPECS = [
    ("computer", "导出示例-计算机类考试-报名导入模板.xlsx", computer_rows),
    ("mandarin", "导出示例-普通话水平测试-考生报名模板.xlsx", mandarin_rows),
]


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    out.mkdir(parents=True, exist_ok=True)
    d = load_dicts()
    for et, fname, builder in SPECS:
        rows = builder(d, n)
        target = out / fname
        exam = {"id": 0, "exam_type": et, "name": target.stem}
        cnt = build_workbook(exam, rows, target, len(rows))
        print("已生成：%s（示例数据行 %d）" % (target.name, cnt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
