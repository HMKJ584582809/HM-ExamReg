# 考试报名信息采集与审核管理系统

> Windows 桌面版（exe 内核 + Web 操作）· v1.1.0
> 数据模型与校验规则对齐两份官方模板：`报名导入模板-计算机.xlsx`、`考生报名模板-普通话.xlsx`

一套面向学校/教务的报名信息采集与审核系统：考生在线填报 → 批量导入 → 分级审核 →
按官方模板汇总导出 → 统计分析与数据大屏。单机绿色运行，无需安装 Python / Node / 数据库。

---

## 一、交付形态

| 项 | 说明 |
|---|---|
| 核心程序 | `考试报名系统.exe`（PyInstaller 单文件，约 70 MB，内含本地抠图模型） |
| 运行方式 | 双击 exe → 启动本地 Web 服务 → 自动打开浏览器 |
| 数据存储 | exe 同级目录 `data/app.db`（SQLite / WAL），`data/exports/` 存导出文件 |
| 运行环境 | Windows 10/11 x64 |
| 端口 | 默认 8765，被占用自动顺延；`考试报名系统.exe --port 9000` 可指定 |
| 局域网 | 默认监听全部网卡，同事用带 IP 的地址即可访问；可在「系统维护」里关闭 |

> 首次运行自动创建管理员账号（`admin` / `Admin@123`，**请登录后立即改密码**）。
> 删除 `data/` 目录即恢复出厂状态。

### 下载

从本仓库右侧 **Releases** 下载最新版 exe，不要从仓库里翻源码自己拼——
`backend/app/assets/idphoto/hivision_modnet.onnx`（25 MB 抠图模型）必须随程序一起分发，
少了它证件照制作会静默退回远程引擎。

> Release 里的资产名是 **`ExamRegistration-<版本>.exe`**（英文），
> 因为 GitHub Release 资产不支持中文文件名（中文名会被存成 `default.exe`）。
> 下载后可直接改回 `考试报名系统.exe`，不影响运行。

---

## 二、角色与权限

采用「角色 → 权限组 → 数据范围」三层模型，**全部由后端强制核验**，前端只按权限渲染菜单。

| 角色 | 主要能力 | 数据范围（默认） |
|---|---|---|
| 管理员 `admin` | 全部功能（批次、字典、用户权限、导入、审核、导出、分析、大屏、系统维护） | 全校 |
| 审核员 `reviewer` | 审核、导出、分析、大屏、实名审核 | 全校 |
| 二级学院审核 `college_reviewer` | 审核、导出、分析、大屏、实名审核（无批量导入） | 本院系 |
| 班主任 `head_teacher` | 报名、批量导入、导出、分析、大屏 | 本班级（可管多个班） |
| 考生 `candidate` | 在线报名、证件照制作、实名认证、AI 助手 | 仅本人 |

- **14 个功能权限组**：在线报名、信息审核、批量导入、汇总导出、数据分析、考试批次管理、
  字典与考试类型维护、用户与权限管理、数据看板、证件照制作、实名认证、实名信息审核、
  AI 助手、系统维护。管理员可对单个账号逐项开关（每个开关都带一句话说明）。
- **4 档数据范围**：本班级 → 本年级 → 本院系 → 全校。可对单个账号**收窄**，但不得放宽。

---

## 三、功能模块

| # | 模块 | 要点 |
|---|---|---|
| 1 | 注册登录 | 验证方式由后台开关决定（图形验证码 / 短信 / 邮箱，至少保留一种）；JWT + 记住我；登录限流 |
| 2 | 考试批次 | 类型+年月自动生成批次名；状态单向 `draft→open→closed→archived`；**可设置照片要求（尺寸/底色）** |
| 3 | 在线报名 | 按类型渲染 13 / 20 字段动态表单；省市区三级联动；同批次唯一约束；报名期内可改可撤 |
| 4 | 信息审核 | 通过 / 驳回 / 退回（意见必填）、批量审核（≤500 条）、写审核轨迹 |
| 5 | 汇总导出 | 表头逐字对齐官方模板；官方模板填充；一键导出证件照（按 考试/院系/班级 三级目录） |
| 6 | 数据分析 | 汇总指标 + 多维图表 + 深度层（审核时效、驳回原因、填报完整度） |
| 7 | 数据大屏 | 16:9 深色大屏，KPI + 趋势 + 构成 + 排行 + 深度指标，自动刷新、可全屏 |
| 8 | 用户与权限 | 开通账号、改角色、单独授权、单独收窄范围、权限组核验、批量导入证件照 |
| 9 | 批量导入 | 智能列识别（任意表头）、合班识别、单双周位掩码、导入预览与失败明细 |
| 10 | AI 助手 | 智能客服 + 智能分析；零配置可用，也可接外部大模型（含代理与连接诊断） |
| 11 | 实名认证 | 本人自填 + 管理员审核；可作为找回密码的核验依据与报名门禁 |
| 12 | 证件照制作 | 本地 ONNX 抠图（离线，照片不出本机）/ 可选远程引擎；抠图阈值、边缘羽化、底部补底可调 |
| 13 | 系统维护 | 网关配置、数据清理、局域网开关、**测试模式（模拟数据，仅管理员）** |

内置三套基础模板：计算机类、普通话水平测试、通用模板（支持自定义字段），
管理员还可基于它们扩展自定义考试类型。

---

## 四、技术实现

| 层 | 选型 |
|---|---|
| 后端 | Python 3.13 + FastAPI + Uvicorn |
| 数据库 | SQLite 3（WAL），**每线程一条连接** |
| 鉴权 | JWT（Access 12h + Refresh 7d）；密码 PBKDF2-HMAC-SHA256 |
| 前端 | Vue 3 + Vue Router 4 + Element Plus 2 + ECharts 5，**免构建**（vendor 本地化，离线可用、无 CDN） |
| 导出 | openpyxl；官方模板模式按**表头名称**定位列（不是按列号，避免错位） |
| 抠图 | 内置 MODNet ONNX + onnxruntime，后处理纯 Pillow/numpy（不引入 opencv/rembg/scipy） |
| 打包 | PyInstaller 单文件模式，静态资源、模板与模型随包内置 |

---

## 五、目录结构

```
exam-signup-system/
├─ backend/
│  ├─ launcher.py                # exe 入口：选端口 → 起服务 → 开浏览器
│  └─ app/
│     ├─ main.py  config.py  db.py  security.py  deps.py  permissions.py
│     ├─ exam_types.py  import_map.py  validators.py  netutil.py  realname.py
│     ├─ idphoto/                # 模块12：specs / matting / maker
│     ├─ routers/                # auth users dicts exams applications export
│     │                          # analysis dashboard ai system photos idphoto exam_types
│     ├─ data/dicts.json         # 由官方模板解析出的字典
│     ├─ assets/                 # idphoto 模型 + 官方模板
│     └─ static/                 # 前端 SPA（index.html + css + js/pages/* + vendor）
├─ tools/                        # 构建与测试脚本（build_exe.py、21 套验证脚本…）
├─ docs/使用说明.md               # 面向使用者的完整说明
├─ docs/开发文档.md               # 架构、接口、权限模型与踩坑记录
└─ README.md
```

---

## 六、从源码运行与构建

```bash
# 1) 依赖
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/pip install pyinstaller

# 2) 开发模式启动（EXAM_DEV=1 回显验证码；EXAM_SEED_DEMO=1 灌演示数据）
cd backend && EXAM_DEV=1 EXAM_SEED_DEMO=1 python -m uvicorn app.main:app --port 8791
#   浏览器打开 http://127.0.0.1:8791

# 3) 打包 exe
python tools/build_exe.py        # 产物在 dist/
```

### 自测

全套 **21 个脚本 / 1280 项**断言，详见 `docs/开发文档.md` §9.4。常用几套：

```bash
python tools/smoke_test.py   http://127.0.0.1:8791          # 后端接口 268 项
node   tools/fe_check.mjs    .                              # 前端 SPA 206 项（需 jsdom/esbuild，BASE_URL 指向服务）
python tools/verify_round11.py      http://127.0.0.1:8942   # 需求专项 57 项
python tools/verify_perms_refine.py http://127.0.0.1:8944   # 权限细化 47 项
python tools/verify_packaged.py --base http://127.0.0.1:8801  # 打包产物新鲜度 61 项
```

> 多套脚本共用一套数据库会互相干扰：**每套测试前请停掉旧服务、删掉 `data/` 再起**。
> `smoke_test.py` 结尾会清空账号，因此它要最后跑（或跑在 fe_check 之后并另起新库）。

---

## 七、预置账号

仅当以 `EXAM_SEED_DEMO=1` 启动（演示模式）时才会有：

| 角色 | 账号 | 密码 |
|---|---|---|
| 管理员 | `admin` | `Admin@123` |
| 审核员 | `reviewer` / `reviewer2` | `Reviewer@123` |
| 二级学院审核 | `college` | `College@123` |
| 班主任 | `teacher` / `teacher2` | `Teacher@123` |
| 考生 | `candidate` / `stu101`… | `Candidate@123` |

**正式环境不含任何演示账号**，首次运行只自动创建管理员。
管理员 / 审核员 / 班主任由管理员在后台开通，考生可在注册页自助注册。

---

## 八、文档

- `docs/使用说明.md` —— 给使用者：启动、角色、报名、审核、导入导出、证件照、大屏、系统维护
- `docs/开发文档.md` —— 给开发：架构、权限模型、接口、数据库、构建测试与踩坑记录
- 程序内「使用说明 / 开发文档」菜单也能直接查看这两份文档

---

## 九、许可与致谢

- 抠图算法源自 [HivisionIDPhotos](https://github.com/Zeyi-Lin/HivisionIDPhotos)（Apache-2.0），
  本项目以 Pillow + numpy 等价替换了其中的 opencv 调用，避免重型依赖链拖大 exe
- 前端依赖（Vue / Element Plus / ECharts）已本地化到 `backend/app/static/vendor`，离线可用
