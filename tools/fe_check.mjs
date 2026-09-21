/**
 * 前端 SPA 冒烟测试：用 jsdom 真实挂载 Vue3 + Element Plus 应用，
 * 捕获编译/运行期错误，并断言各页面（含考生报名表单）渲染出关键内容。
 */
import fs from 'node:fs';
import path from 'node:path';
import { JSDOM, VirtualConsole } from 'jsdom';
import * as esbuild from 'esbuild';

const ROOT = process.argv[2] || '.';
const STATIC = path.join(ROOT, 'backend', 'app', 'static');
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8791';

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond, detail });
  console.log((cond ? '  [PASS] ' : '  [FAIL] ') + name + (cond ? '' : '  -> ' + detail));
}

const bundle = await esbuild.build({
  entryPoints: [path.join(STATIC, 'js', 'app.js')],
  bundle: true, write: false, format: 'iife', target: 'es2020', logLevel: 'silent',
});
const bundleCode = bundle.outputFiles[0].text;
const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8')
  .replace(/<script[^>]*><\/script>/g, '');
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function newSession(auth, startHash) {
  const vc = new VirtualConsole();
  const errors = [];
  vc.on('jsdomError', (e) => errors.push('jsdomError: ' + (e.message || e)));
  vc.on('error', (...a) => errors.push('console.error: ' + a.join(' ')));

  const dom = new JSDOM(html, {
    runScripts: 'dangerously', pretendToBeVisual: true,
    url: BASE + '/' + (startHash || '#/login'), virtualConsole: vc,
  });
  const { window } = dom;
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  window.matchMedia = window.matchMedia || (() => ({
    matches: false, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {},
  }));
  window.echarts = {
    init: () => ({ setOption() {}, resize() {}, dispose() {} }),
    graphic: { LinearGradient: function () {} },
  };
  // jsdom 不提供 fetch，代理到 Node 的 fetch（相对路径补全为后端地址）
  window.fetch = (input, init) => {
    const url = typeof input === 'string' && input.startsWith('/') ? BASE + input : input;
    return fetch(url, init);
  };
  if (!window.URL.createObjectURL) window.URL.createObjectURL = () => 'blob:mock';
  if (!window.URL.revokeObjectURL) window.URL.revokeObjectURL = () => {};
  window.addEventListener('error', (e) => errors.push('window.onerror: ' + (e.message || e)));

  if (auth) {
    window.localStorage.setItem('exam_access_token', auth.access_token);
    window.localStorage.setItem('exam_refresh_token', auth.refresh_token);
    window.localStorage.setItem('exam_user', JSON.stringify(auth.user));
  }

  const run = (code) => {
    const s = window.document.createElement('script');
    s.textContent = code;
    window.document.body.appendChild(s);
  };
  for (const f of ['vue.global.prod.js', 'vue-router.global.prod.js',
    'element-plus.full.min.js', 'element-plus-locale-zh-cn.min.js']) {
    run(fs.readFileSync(path.join(STATIC, 'vendor', f), 'utf8'));
  }
  try { run(bundleCode); } catch (e) { errors.push('bundle 执行异常: ' + e.message); }
  await wait(1500);

  return {
    window, errors,
    text: () => (window.document.querySelector('#app') || {}).textContent || '',
    async goto(hash, ms = 1100) { window.location.hash = hash; await wait(ms); },
    /* 轮询等待条件成立，避免列表尚未渲染完就点击导致的偶发失败 */
    async waitFor(fn, ms = 8000) {
      const t0 = Date.now();
      for (;;) {
        try { if (fn()) return true; } catch (e) { /* 忽略求值异常，继续等 */ }
        if (Date.now() - t0 > ms) return false;
        await wait(200);
      }
    },
    /* 按可见文本点击元素（用于模拟切换验证方式等交互） */
    // 轮询直到出现「整个文本恰好等于 text」的元素再点。
    // 一次性查找是偶发失败的真因：等待条件用的是 includes('编辑')，
    // 只要页面上先出现「批量编辑」之类包含匹配项，等待就通过了，
    // 但此时还没有文本恰好为「编辑」的元素，点击必然空手而归。
    async clickText(text, ms = 700, retryMs = 6000) {
      const deadline = Date.now() + retryMs;
      for (;;) {
        const doc = window.document;
        const all = [...doc.querySelectorAll('span,div,button,label,li')];
        const el = all.reverse().find((e) => e.textContent.trim() === text);
        if (el) {
          el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
          await wait(ms);
          return true;
        }
        if (Date.now() > deadline) return false;
        await wait(250);
      }
    },
    close: () => window.close(),
  };
}

async function login(account, password) {
  const r = await fetch(BASE + '/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ account, password }),
  });
  return (await r.json()).data;
}

console.log('前端 SPA 冒烟测试（jsdom）\n[会话一] 未登录 + 管理员');

const anon = await newSession(null);
check('应用成功挂载（无致命错误）', anon.errors.length === 0, anon.errors.slice(0, 3).join(' | '));
check('登录页渲染标题', anon.text().includes('账号登录'), anon.text().slice(0, 120));
// 交付版不得在登录页暴露任何账号/密码（安全要求）：既不能有密码明文，也不能列出账号名
check('登录页不再显示预置账号密码提示', !anon.text().includes('Admin@123')
  && !anon.text().includes('Reviewer@123') && !anon.text().includes('Candidate@123'),
  anon.text().slice(0, 200));
check('登录页不列出任何演示账号名', !/管理员：\s*admin|审核员：\s*reviewer/.test(anon.text()),
  anon.text().slice(0, 200));
check('登录页渲染注册入口', anon.text().includes('立即注册'));
check('登录页渲染「记住我」选项', anon.text().includes('记住我'), anon.text().slice(0, 200));
await anon.goto('#/dashboard');
check('未登录访问 /dashboard 被重定向到登录页', anon.text().includes('账号登录'));

await anon.goto('#/register');
const rt = anon.text();
check('注册页默认渲染图形验证码输入', rt.includes('图形验证码 *'), rt.slice(0, 300));
// 验证方式改为「后台开什么才显示什么」，不能再断言三种都在：
// 页面必须与 /api/auth/verify-options 的 enabled 严格一致（关掉的不许露出来），
// 否则等于谎报通道 —— 用户点了没开的通道只会拿到 403。
const vres = await fetch(BASE + '/api/auth/verify-options?purpose=register');
const vo = ((await vres.json()) || {}).data || {};
const VLABEL = { captcha: '图形验证码', sms: '手机验证码', email: '邮箱验证码' };
const onKeys = ['captcha', 'sms', 'email'].filter((k) => vo[k] && vo[k].enabled);
const offKeys = ['captcha', 'sms', 'email'].filter((k) => !(vo[k] && vo[k].enabled));
check('注册选项接口至少开放一种验证方式', onKeys.length > 0, JSON.stringify(vo));
check('注册页渲染全部已开启的验证方式',
  onKeys.every((k) => rt.includes(VLABEL[k])), rt.slice(0, 260) + ' | on=' + onKeys.join(','));
check('注册页不显示未开启的验证方式',
  offKeys.every((k) => !rt.includes(VLABEL[k])),
  rt.slice(0, 260) + ' | off=' + offKeys.join(','));
check('注册页标注验证方式由后台设定', rt.includes('只显示管理员已开启的方式'), rt.slice(0, 300));
anon.close();

const adminAuth = await login('admin', 'Admin@123');
const adm = await newSession(adminAuth, '#/');
check('登录后首页渲染开放批次', adm.text().includes('开放报名批次'), adm.text().slice(0, 160));
check('管理员菜单含全部入口',
  adm.text().includes('考试批次管理') && adm.text().includes('数据看板大屏')
  && adm.text().includes('汇总导出') && adm.text().includes('报名信息审核')
  && adm.text().includes('批量导入') && adm.text().includes('用户与权限管理')
  && adm.text().includes('系统维护'),
  adm.text().slice(0, 300));

for (const [hash, keyword, label] of [
  ['#/exams', '考试批次管理', '考试批次管理页'],
  ['#/exam-types', '考试类型管理', '考试类型管理页'],
  ['#/users', '用户与权限管理', '用户与权限管理页'],
  ['#/import', '批量导入报名数据', '批量导入页'],
  ['#/review', '报名信息审核', '审核列表页'],
  ['#/export', '汇总导出', '导出中心页'],
  ['#/analysis', '数据分析', '数据分析页'],
  ['#/dashboard', '考试报名数据看板', '数据大屏页'],
  ['#/system', '系统维护', '系统维护页'],
  ['#/ai', 'AI 智能助手', 'AI 智能助手页'],
  ['#/profile', '个人中心', '个人中心页'],
]) {
  await adm.goto(hash);
  check(label + '渲染正常', adm.text().includes(keyword), adm.text().slice(0, 140));
}

await adm.goto('#/users');
check('用户管理页渲染权限组核验入口', adm.text().includes('权限组核验'), adm.text().slice(0, 200));
await adm.goto('#/users');
check('用户管理页渲染四类角色', adm.text().includes('班主任') && !adm.text().includes('辅导员'),
  adm.text().slice(0, 300));
// 角色标签页：管理员 / 审核员 / 班主任 / 二级学院 / 学生（+ 全部）
const usrT = adm.text();
check('用户管理按角色分页签', ['管理员', '审核员', '班主任', '二级学院', '学生']
  .every((r) => usrT.includes(r)), usrT.slice(0, 300));
check('用户管理页签上方显示当前范围统计', usrT.includes('当前显示'), usrT.slice(0, 300));

// 单独授权：编辑弹窗里的功能权限开关（异步加载权限组，给足等待时间）
await adm.clickText('编辑', 1500);
check('编辑弹窗含功能权限开关区', adm.text().includes('功能权限'), adm.text().slice(0, 300));
check('功能开关逐项渲染', adm.text().includes('批量导入') && adm.text().includes('汇总导出')
  && adm.text().includes('数据看板'), adm.text().slice(0, 400));
check('提供恢复角色默认入口', adm.text().includes('恢复角色默认权限'), adm.text().slice(0, 200));
await adm.goto('#/analysis');
check('分析页渲染数据范围标签', adm.text().includes('数据分析'), adm.text().slice(0, 160));
await adm.goto('#/system');
check('系统维护页渲染清理选项', adm.text().includes('数据清理') && adm.text().includes('确认清空'),
  adm.text().slice(0, 260));

// 考试类型管理：内置类型只读保护 + 自定义类型新建
await adm.goto('#/exam-types');
const etText = adm.text();
check('考试类型页列出两套内置模板',
  etText.includes('计算机类考试') && etText.includes('普通话水平测试'), etText.slice(0, 260));
check('内置类型标注不可删除', etText.includes('内置'), etText.slice(0, 260));
check('自定义类型入口可用', etText.includes('新建考试类型'), etText.slice(0, 260));
await adm.clickText('新建考试类型', 1200);
const etNew = adm.text();
check('新建弹窗可选基础模板',
  etNew.includes('基础模板') && etNew.includes('计算机类考试'), etNew.slice(0, 300));
check('新建弹窗含字段配置区',
  etNew.includes('采集') && etNew.includes('必填'), etNew.slice(0, 300));

// 经接口创建的自定义类型，前端列表立即可见（前端不再写死两种类型）
const newType = await (await fetch(BASE + '/api/exam-types', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + adminAuth.access_token },
  body: JSON.stringify({ code: 'fe_english', name: '前端自测类型', base_type: 'computer' }),
})).json();
check('接口创建自定义类型成功', newType && newType.code === 0, JSON.stringify(newType).slice(0, 200));
await adm.goto('#/');            // 先离开，确保回到类型页时组件重新挂载并重新拉列表
await adm.goto('#/exam-types');
check('自定义类型出现在类型列表', adm.text().includes('前端自测类型'), adm.text().slice(0, 300));
await adm.goto('#/exams');
await adm.clickText('新建考试批次', 1200);
check('新建批次弹窗类型选项含自定义类型',
  adm.text().includes('前端自测类型'), adm.text().slice(0, 300));
if (newType && newType.data && newType.data.id) {
  await fetch(BASE + '/api/exam-types/' + newType.data.id, {
    method: 'DELETE', headers: { Authorization: 'Bearer ' + adminAuth.access_token } });
}

await adm.goto('#/import');
check('批量导入页渲染上传区', adm.text().includes('导入须知') && adm.text().includes('导入记录'),
  adm.text().slice(0, 260));

// AI 智能助手：分析结论 / 处置建议 / 体检表 / 客服 / 大模型配置
await adm.goto('#/');
await adm.goto('#/ai', 1800);
const aiText = adm.text();
check('AI 页渲染引擎标签', aiText.includes('本地规则引擎'), aiText.slice(0, 200));
check('AI 页渲染分析结论与体检', aiText.includes('分析结论') && aiText.includes('数据体检')
  && aiText.includes('处置建议'), aiText.slice(0, 300));
check('AI 页渲染 KPI 与瓶颈', aiText.includes('数据健康分') && aiText.includes('审核瓶颈')
  && aiText.includes('近 14 天报名趋势'), aiText.slice(0, 300));
check('AI 页体检项含手机号与身份证检查',
  aiText.includes('手机号码格式异常') && aiText.includes('证件号码不合法'), aiText.slice(0, 400));
await adm.clickText('AI 客服', 900);
const chatText = adm.text();
check('切换到 AI 客服页', chatText.includes('清空') && chatText.includes('发送'),
  chatText.slice(0, 260));
check('客服页渲染推荐提问', chatText.includes('怎么报名？'), chatText.slice(0, 300));
check('客服页渲染开场白', chatText.includes('智能助手'), chatText.slice(0, 300));
await adm.clickText('大模型配置', 1200);
const cfgText = adm.text();
check('大模型配置页渲染表单', cfgText.includes('启用大模型') && cfgText.includes('服务地址')
  && cfgText.includes('模型名称') && cfgText.includes('附加提示词'), cfgText.slice(0, 300));
check('大模型配置页说明默认不联网', cfgText.includes('无需任何配置'), cfgText.slice(0, 300));
// 需求2：预设厂商 + 按返回状态给可操作提示
check('配置页提供服务商预设下拉', cfgText.includes('服务商'), cfgText.slice(0, 300));
// ⚠ 不在这里断言下拉里出现了 DeepSeek/豆包：el-select 的选项是**懒渲染**的，
//    且被 teleport 到 body，页面文本里读不到。厂商清单改到下面以后端 /providers 为准。
check('提供「测试连接」且说明可不保存先验证', cfgText.includes('测试连接'), cfgText.slice(0, 300));
check('服务地址处说明只填到版本目录',
  cfgText.includes('/chat/completions'), cfgText.slice(0, 400));
check('模型名可从候选里选', cfgText.includes('选择或输入模型名'), cfgText.slice(0, 300));

const provRes = await (await fetch(BASE + '/api/ai/config', {
  headers: { Authorization: 'Bearer ' + adminAuth.access_token } })).json();
const provList = ((provRes.data || {}).providers) || [];
check('后端返回预设厂商列表', provList.length >= 8, String(provList.length));
check('预设厂商含 OpenAI / DeepSeek / 豆包',
  ['openai', 'deepseek', 'doubao'].every((k) => provList.some((p) => p.key === k)),
  JSON.stringify(provList.map((p) => p.key)));
check('预设厂商含本地 Ollama 且标注无需密钥',
  provList.some((p) => p.key === 'ollama' && p.no_key), JSON.stringify(provList.map((p) => p.key)));
check('预设厂商含 OpenAI 并标注境外', provList.some((p) => p.key === 'openai' && p.overseas));
check('预设厂商都带服务地址（自定义除外）',
  provList.filter((p) => p.key !== 'custom').every((p) => !!p.base_url));

const t1 = await (await fetch(BASE + '/api/ai/test', {
  method: 'POST', headers: { 'Content-Type': 'application/json',
    Authorization: 'Bearer ' + adminAuth.access_token },
  body: JSON.stringify({}) })).json();
check('参数自检：缺地址时明确指出缺什么',
  t1.data && t1.data.ok === false && String(t1.data.code).startsWith('incomplete_')
  && !!t1.data.hint, JSON.stringify(t1.data && t1.data.message));
const t2 = await (await fetch(BASE + '/api/ai/test', {
  method: 'POST', headers: { 'Content-Type': 'application/json',
    Authorization: 'Bearer ' + adminAuth.access_token },
  body: JSON.stringify({ base_url: 'https://api.openai.com/v1', api_key: 'sk-x',
    model: 'gpt-4o-mini', proxy: '' }) })).json();
check('境外厂商未配代理时给出提示', t2.data && t2.data.code === 'incomplete_proxy',
  JSON.stringify(t2.data && t2.data.message));

check('管理员会话无运行期错误', adm.errors.length === 0, adm.errors.slice(0, 3).join(' | '));
adm.close();

console.log('\n[会话二] 考生报名流程');
const candAuth = await login('candidate', 'Candidate@123');
const cand = await newSession(candAuth, '#/');
check('考生首页渲染开放批次', cand.text().includes('开放报名批次'));
check('考生菜单仅含报名相关入口',
  cand.text().includes('我的报名') && !cand.text().includes('考试批次管理')
  && !cand.text().includes('数据看板大屏') && !cand.text().includes('批量导入')
  && !cand.text().includes('报名信息审核'), cand.text().slice(0, 260));

const exams = await (await fetch(BASE + '/api/exams', {
  headers: { Authorization: 'Bearer ' + candAuth.access_token } })).json();
const compExam = exams.data.list.find((e) => e.exam_type === 'computer');
const mdExam = exams.data.list.find((e) => e.exam_type === 'mandarin');

await cand.goto('#/apply/' + compExam.id);
const ct = cand.text();
check('计算机类报名表单渲染（13 字段）',
  ct.includes('考试机构编码') && ct.includes('考点编码') && ct.includes('报考科目')
  && ct.includes('就读或者毕业院校') && ct.includes('学历') && ct.includes('通讯地址'),
  ct.slice(0, 200));
check('计算机表单含机构/科目下拉说明',
  ct.includes('本次报名共 13 个采集字段') && ct.includes('下拉字典'), ct.slice(0, 160));

await cand.goto('#/apply/' + mdExam.id);
const mt = cand.text();
check('普通话报名表单渲染（20 字段）',
  mt.includes('考生民族') && mt.includes('从事职业') && mt.includes('考生学号')
  && mt.includes('考生院系') && mt.includes('邮政编码') && mt.includes('邮寄地址'),
  mt.slice(0, 200));
check('普通话表单渲染出生地/现居住地三级联动',
  mt.includes('出生所在省') && mt.includes('出生所在城市') && mt.includes('出生所在县(区)')
  && mt.includes('现居住省') && mt.includes('现居住城市') && mt.includes('现居住县(区)'),
  mt.slice(0, 200));

// 所在单位：必填星号 + 管理员配置的默认值预填
const mdDetail = await (await fetch(`${BASE}/api/exams/${mdExam.id}`, {
  headers: { Authorization: 'Bearer ' + candAuth.access_token } })).json();
const empDefault = ((mdDetail.data || {}).defaults || {}).employer || '';
// ⚠ 源码公开，出厂不预填任何单位名；本地部署时由仓库外的 local_defaults.json 注入。
// 所以这里只校验「字段存在」，不校验具体值——否则本地版和开源版会有一个跑不过。
check('批次详情下发报名默认值（所在单位，出厂为空）',
  typeof empDefault === 'string' && empDefault === '', JSON.stringify(empDefault));

const mdDoc = cand.window.document;
const empItem = [...mdDoc.querySelectorAll('.el-form-item')]
  .find((e) => (e.textContent || '').includes('所在单位'));
check('所在单位标注为必填（带星号）',
  !!empItem && empItem.classList.contains('is-required'),
  empItem ? empItem.className : '（未找到该表单项）');
const empInput = empItem && empItem.querySelector('input');
check('所在单位已预填默认值', !!empInput && empInput.value === empDefault,
  empInput ? empInput.value : '（无输入框）');

await cand.goto('#/my-applications');
check('我的报名页渲染', cand.text().includes('我的报名'), cand.text().slice(0, 120));
await cand.goto('#/exams');
check('考生访问管理员页面被拦截', !cand.text().includes('新建考试批次'), cand.text().slice(0, 120));
await cand.goto('#/users');
check('考生访问用户管理被拦截', !cand.text().includes('开通账号'), cand.text().slice(0, 120));
await cand.goto('#/import');
check('考生访问批量导入被拦截', !cand.text().includes('导入须知'), cand.text().slice(0, 140));
check('考生会话无运行期错误', cand.errors.length === 0, cand.errors.slice(0, 3).join(' | '));
cand.close();

console.log('\n[会话三] 班主任（本班数据范围）');
const teacherAuth = await login('teacher', 'Teacher@123');
if (!teacherAuth) {
  check('班主任演示账号可用', false, '未获取到登录令牌');
} else {
  check('班主任登录成功', teacherAuth.user.role === 'head_teacher', teacherAuth.user.role);
  check('班主任权限组含批量导入',
    (teacherAuth.user.perms || []).includes('import'), teacherAuth.user.perms);
  const tc = await newSession(teacherAuth, '#/');
  check('班主任菜单含批量导入', tc.text().includes('批量导入'), tc.text().slice(0, 260));
  check('班主任菜单无用户管理', !tc.text().includes('用户与权限管理'), tc.text().slice(0, 260));
  // 权限细化后班主任有 dashboard 权限组（能看本班范围的大屏），不再是没有大屏
  check('班主任菜单含数据大屏', tc.text().includes('数据看板大屏'), tc.text().slice(0, 260));
  check('侧边栏显示数据范围', tc.text().includes('本班级') || tc.text().includes('计算机2101'),
    tc.text().slice(0, 300));

  await tc.goto('#/review');
  const rv = tc.text();
  check('班主任进入审核页显示「查看」语义', rv.includes('报名数据查看'), rv.slice(0, 160));
  check('班主任审核页隐藏审核按钮', !rv.includes('批量通过'), rv.slice(0, 300));

  await tc.goto('#/import');
  check('班主任批量导入页渲染', tc.text().includes('批量导入报名数据'), tc.text().slice(0, 160));
  await tc.goto('#/dashboard', 2000);
  // 班主任有 dashboard 权限组，且大屏已修掉 JOIN 下的 ambiguous column name，
  // 这里必须真的渲染出看板标题，而不是 500 或空页
  check('班主任可访问本班范围大屏', tc.text().includes('考试报名数据看板'), tc.text().slice(0, 160));
  await tc.goto('#/users');
  check('班主任访问用户管理被拦截', !tc.text().includes('开通账号'), tc.text().slice(0, 160));
  check('班主任会话无运行期错误', tc.errors.length === 0, tc.errors.slice(0, 3).join(' | '));
  tc.close();
}

console.log('\n[会话四] 班主任多班级数据范围');
const coAuth = await login('teacher', 'Teacher@123');
check('班主任多班级登录成功', coAuth && coAuth.user.role === 'head_teacher', coAuth && coAuth.user.role);
check('班主任数据范围=本班级', coAuth && coAuth.user.scope === 'scope_class', coAuth && coAuth.user.scope);
const cls = (coAuth && (coAuth.user.classes || '').split(/[,，]/).filter(Boolean)) || [];
check('班主任管理多个班级', cls.length >= 2, cls);
const co = await newSession(coAuth, '#/');
check('班主任菜单含批量导入', co.text().includes('批量导入'), co.text().slice(0, 260));
check('侧边栏显示多班级范围',
  co.text().includes('计算机2101') && co.text().includes('软件工程2202'), co.text().slice(0, 320));
await co.goto('#/import');
check('班主任批量导入页渲染范围提示',
  co.text().includes('管理范围') || co.text().includes('班级'), co.text().slice(0, 300));
check('班主任会话无运行期错误', co.errors.length === 0, co.errors.slice(0, 3).join(' | '));
co.close();

console.log('\n[会话五] 审核员');
const revAuth = await login('reviewer', 'Reviewer@123');
const rev = await newSession(revAuth, '#/');
check('审核员菜单含审核/导出/分析',
  rev.text().includes('报名信息审核') && rev.text().includes('汇总导出')
  && rev.text().includes('数据分析'), rev.text().slice(0, 260));
check('审核员菜单无批量导入', !rev.text().includes('批量导入'), rev.text().slice(0, 260));
await rev.goto('#/review');
check('审核员审核页含批量操作按钮', rev.text().includes('批量通过'), rev.text().slice(0, 200));
await rev.goto('#/import');
check('审核员访问批量导入被拦截', !rev.text().includes('导入须知'), rev.text().slice(0, 160));
check('审核员会话无运行期错误', rev.errors.length === 0, rev.errors.slice(0, 3).join(' | '));
rev.close();

console.log('\n[会话六] 单独授权后菜单即时反映（无需重新登录）');
// 管理员新建一个审核员，再用「单独授权」关掉它的 汇总导出 / 数据分析，
// 该账号新开会话时菜单必须立刻少掉这两项 —— 验证前端不是只认登录时的权限快照。
const admAuth = await login('admin', 'Admin@123');
const tmpName = 'permui' + Math.floor(Math.random() * 90000 + 10000);
const mk = await fetch(BASE + '/api/users', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + admAuth.access_token },
  body: JSON.stringify({
    username: tmpName, real_name: '权限UI校验', role: 'reviewer',
    password: 'PermUI@123', phone: '138' + Math.floor(Math.random() * 9e7 + 1e7),
  }),
});
const mkJson = await mk.json();
check('创建待授权账号', mk.status === 200 && mkJson.code === 0, JSON.stringify(mkJson).slice(0, 160));

let tmpUid = mkJson.data && mkJson.data.id;
if (!tmpUid) {
  const lu = await (await fetch(BASE + '/api/users?keyword=' + tmpName, {
    headers: { Authorization: 'Bearer ' + admAuth.access_token },
  })).json();
  tmpUid = lu.data.list[0].id;
}

// 授权前：审核员默认可见 汇总导出 / 数据分析
const beforeAuth = await login(tmpName, 'PermUI@123');
const sBefore = await newSession(beforeAuth, '#/');
check('授权前菜单含 汇总导出 与 数据分析',
  sBefore.text().includes('汇总导出') && sBefore.text().includes('数据分析'),
  sBefore.text().slice(0, 240));
sBefore.close();

// 单独授权：仅保留 信息审核
const pu = await fetch(BASE + `/api/users/${tmpUid}/perms`, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + admAuth.access_token },
  body: JSON.stringify({ perms: ['audit'] }),
});
const puJson = await pu.json();
check('单独授权为「仅信息审核」', pu.status === 200 && puJson.code === 0,
  JSON.stringify(puJson).slice(0, 160));

// 关键：故意复用「授权前」取得的会话（其 localStorage 快照里仍带着 export/analysis），
// 不重新登录。若前端只认登录快照，菜单会依旧显示已关闭的功能 —— 这里断言它没显示，
// 即证明应用启动时会拉取最新权限（app.js 的 refreshMe）。
const sAfter = await newSession(beforeAuth, '#/');
const tAfter = sAfter.text();
check('未重新登录（沿用旧会话快照）也能反映新权限：菜单不再含 汇总导出',
  !tAfter.includes('汇总导出'), tAfter.slice(0, 240));
check('授权后菜单不再含 数据分析', !tAfter.includes('数据分析'), tAfter.slice(0, 240));
check('授权后菜单仍含 报名信息审核（保留的功能）', tAfter.includes('报名信息审核'),
  tAfter.slice(0, 240));
await sAfter.goto('#/export');
check('授权后访问 /export 被拦截', !sAfter.text().includes('导出范围'),
  sAfter.text().slice(0, 160));
check('单独授权会话无运行期错误', sAfter.errors.length === 0, sAfter.errors.slice(0, 3).join(' | '));
sAfter.close();

console.log('\n[会话七] 证件照');
const admS = await newSession(admAuth, '#/profile');
const pt = admS.text();
check('个人中心渲染证件照卡片', pt.includes('证件照'), pt.slice(0, 200));
check('证件照区提供上传入口', pt.includes('上传照片') || pt.includes('更换照片'),
  pt.slice(0, 260));
check('证件照区标注了格式与大小限制',
  pt.includes('JPG / PNG') && pt.includes('5 MB'), pt.slice(0, 320));

await admS.goto('#/users', 1500);
const ut = admS.text();
check('用户管理页有「批量导入证件照」入口', ut.includes('批量导入证件照'), ut.slice(0, 220));
const openedPhoto = await admS.clickText('批量导入证件照', 1200);
const dt = admS.text();
check('点击后打开批量导入弹窗', openedPhoto && dt.includes('按文件名匹配账号'),
  dt.slice(0, 220));
check('弹窗说明匹配优先级（证件号→学号→手机号→姓名）',
  dt.includes('证件号码') && dt.includes('用户名/学号') && dt.includes('手机号'),
  dt.slice(0, 420));
check('弹窗说明重名不会自动覆盖', dt.includes('重名'), dt.slice(0, 420));
check('证件照会话无运行期错误', admS.errors.length === 0, admS.errors.slice(0, 3).join(' | '));
admS.close();

console.log('\n[会话八] 数据范围与二级学院审核');
const clgAuth = await login('college', 'College@123');
check('二级学院审核演示账号可登录', !!clgAuth, clgAuth ? '' : '登录失败');
if (clgAuth) {
  check('角色标签为「二级学院审核」', clgAuth.user.role_label === '二级学院审核',
    clgAuth.user.role_label);
  check('默认数据范围为本院系', clgAuth.user.scope === 'scope_college', clgAuth.user.scope);
  const clg = await newSession(clgAuth, '#/');
  const ctxt = clg.text();
  check('侧栏展示数据范围「本院系」', ctxt.includes('本院系'), ctxt.slice(0, 300));
  check('范围标签带出院系名（计算机学院）', ctxt.includes('计算机学院'), ctxt.slice(0, 300));
  await clg.goto('#/review', 1600);
  check('可进入报名信息审核页', clg.text().includes('报名信息审核'), clg.text().slice(0, 200));
  await clg.goto('#/analysis', 1600);
  check('可进入数据分析页', clg.text().includes('数据分析'), clg.text().slice(0, 200));
  await clg.goto('#/import', 1400);
  check('看不到批量导入（无 import 权限）', !clg.text().includes('导入范围'),
    clg.text().slice(0, 200));
  check('二级学院审核会话无运行期错误', clg.errors.length === 0,
    clg.errors.slice(0, 3).join(' | '));
  clg.close();
}

// 管理员：编辑弹窗里的数据范围选择
const admU = await newSession(admAuth, '#/users', 2000);
const ulistTxt = admU.text();
check('用户管理页说明四档数据范围',
  ulistTxt.includes('本班级') && ulistTxt.includes('本年级')
  && ulistTxt.includes('本院系') && ulistTxt.includes('全校'), ulistTxt.slice(0, 300));
// 必须同时排除 Element Plus 的空态文案「No Data」：只排除中文「暂无数据」时，
// 列表还没渲染完就会被判定为已就绪，接着点「编辑」必然落空（偶发失败的真因）
await admU.waitFor(() => admU.text().includes('编辑') && !admU.text().includes('暂无数据')
  && !admU.text().includes('No Data'), 12000);
const openedEdit = await admU.clickText('编辑', 1600);
const editTxt = admU.text();
check('可打开账号编辑弹窗', openedEdit, editTxt.slice(0, 160));
check('编辑弹窗含「数据范围」分区', editTxt.includes('数据范围（可见哪些数据）'),
  editTxt.slice(0, 400));
check('范围提供「跟随角色」选项', editTxt.includes('跟随角色'), editTxt.slice(0, 400));
check('范围仅列出不超过角色上限的档位',
  editTxt.includes('全校') || editTxt.includes('本班级'), editTxt.slice(0, 400));
check('范围分区给出各档说明', editTxt.includes('只能看') || editTxt.includes('可见全校'),
  editTxt.slice(0, 500));
check('用户管理会话无运行期错误', admU.errors.length === 0, admU.errors.slice(0, 3).join(' | '));

// 需求9：用户列表直接展示实名状态，并提供实名审核入口
check('用户页提供实名审核入口', admU.text().includes('实名审核'), admU.text().slice(0, 300));
check('用户列表含实名状态列', admU.text().includes('实名'), admU.text().slice(0, 300));
check('后端用户列表返回实名状态字段',
  (await (await fetch(BASE + '/api/users?page_size=3', {
    headers: { Authorization: 'Bearer ' + admAuth.access_token } })).json())
    .data.list.every((u) => 'realname_status' in u), 'realname_status 缺失');
admU.close();

console.log('\n[会话九] 证件照一键导出');
const expS = await newSession(admAuth, '#/export', 2000);
const et = expS.text();
check('导出页含「证件照一键导出」区块', et.includes('证件照一键导出'), et.slice(0, 300));
check('区块标注按 考试/院系/班级 建目录',
  et.includes('考试') && et.includes('院系') && et.includes('班级'), et.slice(0, 400));
check('提供 JPG / PNG 两种格式', et.includes('JPG') && et.includes('PNG'), et.slice(0, 400));
check('提供「一键导出」按钮', et.includes('一键导出'), et.slice(0, 400));
check('说明文件名用证件号码', et.includes('证件号码'), et.slice(0, 500));
check('说明缺照片名单会写进压缩包', et.includes('_导出说明'), et.slice(0, 500));
check('证件照导出会话无运行期错误', expS.errors.length === 0, expS.errors.slice(0, 3).join(' | '));
expS.close();

console.log('\n[会话十] 通用考试报名模板（自定义字段）');

async function apiCall(token, method, p, body) {
  const r = await fetch(BASE + p, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { st: r.status, j: await r.json() };
}

const admTok = adminAuth.access_token;
const uniq = 'g' + Date.now().toString().slice(-6);
const pad = (n) => String(n).padStart(2, '0');
const fmt = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
  + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

const tNew = await apiCall(admTok, 'POST', '/api/exam-types',
  { code: uniq, name: '通用演示考试', base_type: 'generic', description: 'fe_check' });
const gTid = tNew.j && tNew.j.data && tNew.j.data.id;
await apiCall(admTok, 'POST', `/api/exam-types/${gTid}/fields`,
  { field_key: 'glevel', label: '报考等级', field_type: 'select', required: true,
    options: ['A级', 'B级'] });
await apiCall(admTok, 'POST', `/api/exam-types/${gTid}/fields`,
  { field_key: 'gdate', label: '意向日期', field_type: 'date', required: false });
const eNew = await apiCall(admTok, 'POST', '/api/exams', {
  name: '通用模板演示批次' + uniq, exam_type: uniq, exam_year: new Date().getFullYear(),
  exam_month: 6, signup_start_at: fmt(new Date(Date.now() - 86400000)),
  signup_end_at: fmt(new Date(Date.now() + 86400000 * 30)), status: 'open',
});
const gEid = eNew.j && eNew.j.data && eNew.j.data.id;

const gt = await newSession(adminAuth, '#/exam-types', 1800);
check('类型管理页列出「通用考试报名」模板',
  gt.text().includes('通用考试报名'), gt.text().slice(0, 200));
check('类型列表标注该类型的自定义字段数',
  /\+\s*2\s*自定义/.test(gt.text()), gt.text().slice(0, 400));
gt.close();

const ap = await newSession(adminAuth, `#/apply/${gEid}`, 2600);
const apT = ap.text();
check('通用模板报名页渲染核心字段',
  apT.includes('姓名') && apT.includes('手机号码') && apT.includes('院系'),
  apT.slice(0, 300));
check('通用模板报名页渲染自定义字段标签',
  apT.includes('报考等级') && apT.includes('意向日期'), apT.slice(0, 400));
check('报名页说明自定义字段的来源与数量',
  apT.includes('个是管理员为该类型配置的自定义字段'), apT.slice(0, 500));
check('通用模板报名表单无运行期错误', ap.errors.length === 0,
  ap.errors.slice(0, 3).join(' | '));
ap.close();

// ============================================================ 字典维护（审计 #8）
const dic = await newSession(adminAuth, '#/dicts', 2000);
const dicT = dic.text();
check('字典维护页渲染三类可维护字典',
  dicT.includes('考试机构') && dicT.includes('报考科目') && dicT.includes('从事职业'),
  dicT.slice(0, 300));
check('字典维护页说明被引用条目不可删除',
  dicT.includes('被引用的条目不能删除'), dicT.slice(0, 400));
check('字典维护列表渲染了数据行与操作列',
  dicT.includes('被引用') && dicT.includes('编辑') && dicT.includes('删除'),
  dicT.slice(0, 300));
check('字典维护页无运行期错误', dic.errors.length === 0, dic.errors.slice(0, 3).join(' | '));
check('字典维护新增「二级学院」「部门」两类',
  dicT.includes('二级学院') && dicT.includes('部门'), dicT.slice(0, 300));
// 部门可「归属到某个学院」（平铺单层，不是树）：切到部门页签应出现「所属学院」列
await dic.clickText('部门');
await dic.waitFor(() => dic.text().includes('所属学院'), 8000);
const depT = dic.text();
check('部门页签显示「所属学院」列', depT.includes('所属学院'), depT.slice(0, 300));
check('未归属学院的部门标注为校级', depT.includes('校级'), depT.slice(0, 300));
check('字典维护页无运行期错误（切到部门后）', dic.errors.length === 0,
  dic.errors.slice(0, 3).join(' | '));
// 后端接口为准：部门带 college，且可写回
const depFull = await (await fetch(BASE + '/api/dicts/department/full',
  { headers: { Authorization: 'Bearer ' + adminAuth.access_token } })).json();
const depList = (depFull || {}).data || [];
check('部门接口返回所属学院字段', depList.length > 0
  && depList.every((d) => typeof d.college === 'string'),
  JSON.stringify(depList.slice(0, 3)));
const target = depList.find((d) => d.name.includes('学工办')) || depList[0];
const colleges = await (await fetch(BASE + '/api/dicts/college',
  { headers: { Authorization: 'Bearer ' + adminAuth.access_token } })).json();
const collegeName = ((colleges || {}).data || [])[0] || '';
const putRes = await fetch(BASE + '/api/dicts/manage/department/' + target.id, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json',
    Authorization: 'Bearer ' + adminAuth.access_token },
  body: JSON.stringify({ name: target.name, college: collegeName }),
});
const putJson = await putRes.json();
check('可把部门归属到学院', putRes.status === 200
  && (putJson.data || {}).college === collegeName, JSON.stringify(putJson).slice(0, 200));
// 反证：不存在的学院必须被拒，否则部门会永远挂不到任何学院下面还查不出来
const badRes = await fetch(BASE + '/api/dicts/manage/department/' + target.id, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json',
    Authorization: 'Bearer ' + adminAuth.access_token },
  body: JSON.stringify({ name: target.name, college: '不存在的学院' }),
});
check('归属到不存在的学院被拒绝', badRes.status === 400, 'HTTP ' + badRes.status);
dic.close();

// 数据看板：全屏展示按钮（jsdom 不实现 Fullscreen API，只验证入口与状态文案渲染）
const dash = await newSession(adminAuth, '#/dashboard', 2000);
const dashT = dash.text();
check('数据看板提供全屏展示入口', dashT.includes('全屏展示'), dashT.slice(0, 200));
check('数据看板无运行期错误', dash.errors.length === 0, dash.errors.slice(0, 3).join(' | '));

// -------- 需求4：大屏深度层（运行质量 / 找瓶颈）
check('大屏补充过程指标 KPI（审核耗时 / 已审核率 / 驳回率 / 照片完整率）',
  dashT.includes('平均审核耗时') && dashT.includes('已审核率')
  && dashT.includes('驳回率') && dashT.includes('证件照完整率'), dashT.slice(0, 400));
check('大屏给出审核时效的均值/中位/P90',
  /均\s*[\d.]+\s*h/.test(dashT) && dashT.includes('中位') && dashT.includes('P90'),
  dashT.slice(0, 600));
check('大屏含运行质量分组（时效分布 / 驳回原因 / 考生画像）',
  dashT.includes('运行质量') && dashT.includes('审核时效分布')
  && dashT.includes('驳回 / 退回原因归类') && dashT.includes('考生画像'),
  dashT.slice(0, 600));
check('大屏含报名提交时段分布', dashT.includes('报名提交时段分布'), dashT.slice(0, 400));
check('大屏含单位/院系→班级两级分布', dashT.includes('单位 / 院系 → 班级分布'), dashT.slice(0, 400));
check('大屏含各批次审核进度', dashT.includes('各批次审核进度'), dashT.slice(0, 400));
check('大屏含审核员工作量表', dashT.includes('审核员工作量'), dashT.slice(0, 400));
check('大屏补充考点与考试机构分布',
  dashT.includes('考点分布') && dashT.includes('考试机构'), dashT.slice(0, 400));
check('大屏含必填项缺失提示（填报质量）', dashT.includes('必填项缺失'), dashT.slice(0, 400));
check('大屏底部标注数据范围', dashT.includes('数据范围：'), dashT.slice(-260));
dash.close();

// ============================================================ 找回密码（文档承诺）
const lg = await newSession(null, '#/login', 1800);
check('登录页提供「忘记密码」入口', lg.text().includes('忘记密码'), lg.text().slice(0, 200));
await lg.clickText('忘记密码', 1200);
// 弹窗被 Element Plus teleport 到 body，必须读 body 而非 #app
const lgT = lg.window.document.body.textContent || '';
// 验证方式改为「后台开关决定」，默认只开「图形验证码 + 身份证号」，
// 因此不再保证出现「发送验证码」按钮（那是短信/邮箱方式才有）。
check('找回密码弹窗含验证凭据与验证码入口',
  lgT.includes('找回密码') && lgT.includes('验证方式') && lgT.includes('验证码'),
  lgT.slice(0, 400));
check('找回密码默认提供「图形验证码 + 身份证号」方式',
  lgT.includes('图形验证码') && lgT.includes('身份证'),
  lgT.slice(0, 400));
check('找回密码弹窗含新密码与确认新密码',
  lgT.includes('新密码') && lgT.includes('确认新密码') && lgT.includes('确认重置'),
  lgT.slice(0, 400));
lg.close();

// ============================================================ 审核代撤回（审计 #9）
const rv = await newSession(adminAuth, '#/review', 2400);
const rvT = rv.text();
check('审核页列出待审核记录', rvT.includes('待审核') || rvT.includes('审核'), rvT.slice(0, 200));
// 定位第一条「待审核」记录并打开其详情（撤回按钮仅对待审核展示）
const rdoc = rv.window.document;
const prow = [...rdoc.querySelectorAll('tbody tr')]
  .find((tr) => tr.textContent.includes('待审核'));
let opened = false;
if (prow) {
  const btn = [...prow.querySelectorAll('button')]
    .find((b) => b.textContent.trim() === '详情');
  if (btn) {
    btn.dispatchEvent(new rdoc.defaultView.MouseEvent('click', { bubbles: true }));
    opened = true;
  }
}
await wait(2000);
// 抽屉被 Element Plus teleport 到 body，必须读 body 而非 #app
const rvT2 = rdoc.body.textContent || '';
check('打开待审核记录的详情抽屉', opened && rvT2.includes('审核意见'), rvT2.slice(0, 300));
check('审核详情提供「代考生撤回」操作',
  rvT2.includes('代考生撤回报名') && rvT2.includes('与「驳回」'),
  rvT2.slice(0, 600));
rv.close();

// ============================================================ 内置文档（菜单 + 渲染）
// jsdom 加载 vendor/marked.min.js 是异步资源，新 session 首屏常处于竞态，
// 所以用宽松的「页面含 markdown 渲染后的特征文本」而非依赖 .md-body h1/table/code
const du = await newSession(adminAuth, '#/docs/usage', 6000);
const duDoc = du.window.document;
const duMenu = [...duDoc.querySelectorAll('a, .menu-item, [class*="menu"]')]
  .map((e) => (e.textContent || '').trim())
  .join(' ');
check('菜单有「使用说明」入口', duMenu.includes('使用说明'), duMenu.slice(0, 200));
check('菜单有「开发文档」入口', duMenu.includes('开发文档'), duMenu.slice(0, 200));
const duBody = duDoc.body.textContent || '';
check('使用说明页含正文（markdown 渲染后特征文本）',
      /启动系统/.test(duBody) || /局域网访问/.test(duBody) || /找回密码/.test(duBody),
      duBody.slice(0, 200));
du.close();

const dd = await newSession(adminAuth, '#/docs/dev', 6000);
const ddDoc = dd.window.document;
const ddBody = ddDoc.body.textContent || '';
check('开发文档页含正文', /权限模型/.test(ddBody) || /技术选型/.test(ddBody),
      ddBody.slice(0, 200));
dd.close();

// ============================================================ 需求8：建号只填用户名 / 密码 / 手机号
const cu = await newSession(admAuth, '#/users', 2600);
await cu.waitFor(() => cu.text().includes('开通账号'), 12000);
const openedNew = await cu.clickText('开通账号', 1500);
// 页面上有三个 el-dialog（开通账号 / 密码重置申请 / 批量导入证件照），
// 必须按内容挑，直接取第一个会拿到别的弹窗
const dlg = [...cu.window.document.querySelectorAll('.el-dialog')]
  .find((d) => /开通账号/.test(d.textContent) && /初始密码/.test(d.textContent));
const dlgTxt = dlg ? dlg.textContent : '';
check('可打开「开通账号」弹窗', openedNew && !!dlg, dlgTxt.slice(0, 200));
// 需求8：姓名/邮箱等非必要项收进折叠区，主区只直接展示三项
const mainPart = dlgTxt.split('补充信息')[0];
check('建号主区直接展示用户名 / 手机号 / 初始密码',
      /用户名/.test(mainPart) && /手机号/.test(mainPart) && /初始密码/.test(mainPart),
      mainPart.slice(0, 300));
check('建号主区不直接出现「真实姓名」', !/真实姓名/.test(mainPart), mainPart.slice(0, 300));
const colTxt = dlg && dlg.querySelector('.el-collapse')
  ? dlg.querySelector('.el-collapse').textContent : '';
check('姓名与邮箱收进「补充信息（可选）」折叠区',
      /补充信息（可选）/.test(dlgTxt) && /真实姓名/.test(colTxt) && /邮箱/.test(colTxt),
      colTxt.slice(0, 200));
cu.close();

// ============================================================ 需求3：大屏全屏时「只」展示大屏
// jsdom 没有 Fullscreen API，所以打桩验证**请求全屏的目标是誰** —— 这才是需求的核心：
// 对 documentElement 全屏时侧边栏和顶栏仍在，达不到「全屏时只展示大屏」。
// （只断言源码里出现 requestFullscreen 证明不了这一点。）
const fsS = await newSession(adminAuth, '#/dashboard', 3000);
const fsW = fsS.window;
let reqOnDoc = 0, reqOnEl = 0, exitCalled = 0;
fsW.Element.prototype.requestFullscreen = function () {
  if (this === fsW.document.documentElement) reqOnDoc++; else reqOnEl++;
  Object.defineProperty(fsW.document, 'fullscreenElement', { value: this, configurable: true });
  fsW.document.dispatchEvent(new fsW.Event('fullscreenchange'));
  return Promise.resolve();
};
fsW.document.exitFullscreen = function () {
  exitCalled++;
  Object.defineProperty(fsW.document, 'fullscreenElement', { value: null, configurable: true });
  fsW.document.dispatchEvent(new fsW.Event('fullscreenchange'));
  return Promise.resolve();
};
const fsClicked = await fsS.clickText('全屏展示', 1200);
check('大屏提供「全屏展示」入口', fsClicked, fsS.text().slice(0, 200));
check('点击后确实发起了 requestFullscreen', reqOnDoc + reqOnEl > 0,
      `容器 ${reqOnEl} 次 / 整篇文档 ${reqOnDoc} 次`);
check('全屏目标是「大屏容器」而非整篇文档（否则侧边栏顶栏仍在）',
      reqOnEl === 1 && reqOnDoc === 0, `容器 ${reqOnEl} 次 / 整篇文档 ${reqOnDoc} 次`);
await wait(300);
const fsExit = await fsS.clickText('退出全屏', 1200);
check('再次点击可退出全屏', fsExit && exitCalled === 1, `exitFullscreen ${exitCalled} 次`);
fsS.close();

// ============================================================ 证件照制作页（模块 12）
const ip = await newSession(adminAuth, '#/idphoto', 2600);
const ipT = ip.text();
check('证件照制作页渲染正常', ipT.includes('证件照制作'), ipT.slice(0, 200));
check('三步引导齐全（选择照片 / 规格与底色 / 成品）',
      ipT.includes('选择照片') && ipT.includes('规格与底色') && ipT.includes('成品'),
      ipT.slice(0, 300));
check('尺寸选项含一寸与二寸（带毫米标注）',
      ipT.includes('25×35mm') && ipT.includes('35×49mm'), ipT.slice(0, 400));
check('底色选项含白底 / 蓝底 / 红底 / 透明底',
      ipT.includes('白底') && ipT.includes('蓝底') && ipT.includes('红底')
      && ipT.includes('透明底'), ipT.slice(0, 400));
// 打包防线：模型没随 exe 带进去时页面会显示这条告警，并静默退回远程引擎
check('本地抠图引擎可用（未出现「模型不可用」告警）',
      !ipT.includes('本地抠图模型不可用'), ipT.slice(0, 400));
check('提供拖拽/点选上传提示', ipT.includes('点击选择，或将照片拖到这里'), ipT.slice(0, 400));
ip.close();

// 该页不属于任何权限组（考生自己做证件照），路由 meta 无 perm
const ipc = await newSession(candAuth, '#/idphoto', 2600);
check('考生也能打开证件照制作页（对所有登录用户开放）',
      ipc.text().includes('证件照制作'), ipc.text().slice(0, 200));
ipc.close();

// ============================================================ 实名认证页（模块 11）
const rn = await newSession(adminAuth, '#/realname', 2600);
const rnT = rn.text();
check('实名认证页渲染正常', rnT.includes('实名认证'), rnT.slice(0, 200));
check('展示「我的实名状态」', rnT.includes('我的实名状态'), rnT.slice(0, 300));
check('提供自填实名表单（真实姓名 / 证件类型 / 证件号码）',
      rnT.includes('真实姓名') && rnT.includes('证件类型') && rnT.includes('证件号码'),
      rnT.slice(0, 400));
check('提供「提交审核」入口', rnT.includes('提交审核'), rnT.slice(0, 400));
check('页面说明实名与找回密码的关系',
      rnT.includes('找回密码'), rnT.slice(0, 400));
rn.close();

// ============================================================ 静态扫描：JS 正则不得被双重转义
// 真实缺陷：profile.js 手机号规则写成 /^1[3-9]\\d{9}$/ —— 在 .js 文件里 \\d 等于
// 「字面反斜杠 + d」，任何正确手机号都匹配不上；apply.js 邮编、home.js 图表标签同类。
const jsBad = [];
(function walkJs(d) {
  for (const f of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, f.name);
    if (f.isDirectory()) walkJs(p);
    else if (f.name.endsWith('.js') && /\\\\[dwsDSb]/.test(fs.readFileSync(p, 'utf8'))) {
      jsBad.push(path.relative(STATIC, p));
    }
  }
})(path.join(STATIC, 'js'));
check('JS 中无被双重转义的正则字面量（\\\\d 之类）', jsBad.length === 0, jsBad.join(', '));

// ============================================================ 静态扫描：不得对已解包的结果再取 .data
// 真实缺陷：idphoto.js 写成 `const r = await api.get(...); const d = r.data` ——
// api.get/post/put/del 返回的**已经是 data**（只有 api.upload 才返回 {data,message}），
// 再取一次 .data 恒为 undefined。后端完全正常，因此任何后端测试都测不出来。
const apiBad = [];
for (const f of fs.readdirSync(path.join(STATIC, 'js', 'pages'))) {
  if (!f.endsWith('.js')) continue;
  const src = fs.readFileSync(path.join(STATIC, 'js', 'pages', f), 'utf8');
  const re = /const\s+(\w+)\s*=\s*(?:await\s+)?api\.(?:get|post|put|del|raw)\s*\(/g;
  let m;
  while ((m = re.exec(src))) {
    if (new RegExp('\\b' + m[1] + '\\.data\\b').test(src)) {
      apiBad.push(f + ' → ' + m[1] + '.data');
    }
  }
}
check('页面未对 api.get/post/put/del 的结果再取 .data（已解包）',
      apiBad.length === 0, apiBad.join(', '));

console.log('-'.repeat(62));
const failed = results.filter((r) => !r.ok);
console.log(`通过 ${results.length - failed.length} 项，失败 ${failed.length} 项`);
if (failed.length) failed.forEach((f) => console.log('  -', f.name));
process.exit(failed.length ? 1 : 0);
