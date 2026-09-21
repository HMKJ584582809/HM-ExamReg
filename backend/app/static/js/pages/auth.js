/* 模块1：登录 / 注册 */
import { api, handleErr, msg, session } from '../api.js';
import { loadDicts, setUser } from '../store.js';

const { ref, reactive, computed, onMounted, onUnmounted } = Vue;

export const LoginPage = {
  template: `
  <div class="auth-wrap">
    <div class="auth-box">
      <div class="auth-side">
        <h1>考试报名信息<br>采集与审核管理系统</h1>
        <div class="sub">支持计算机类考试与普通话水平测试两类官方模板的<br>在线报名、信息审核、汇总导出与数据分析。</div>
        <ul>
          <li>考生在线填报，双模板动态表单</li>
          <li>审核员通过 / 驳回 / 退回全流程</li>
          <li>导出结果严格对齐官方模板表头</li>
          <li>数据看板 16:9 大屏实时刷新</li>
        </ul>
      </div>
      <div class="auth-main">
        <h2>账号登录</h2>
        <div class="tip">请输入用户名或手机号登录系统</div>
        <el-form :model="form" :rules="rules" ref="formRef" label-position="top" @keyup.enter="submit">
          <el-form-item label="用户名 / 手机号" prop="account">
            <el-input v-model="form.account" placeholder="请输入用户名或手机号" size="large" clearable />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input v-model="form.password" type="password" show-password size="large"
                      placeholder="请输入密码" />
          </el-form-item>
          <el-form-item>
            <div class="login-opts">
              <el-checkbox v-model="form.remember">记住我（保持登录 12 小时）</el-checkbox>
              <span class="muted small">{{ form.remember ? '本机浏览器长期保留' : '关闭浏览器后需重新登录' }}</span>
            </div>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" size="large" style="width:100%" :loading="loading" @click="submit">
              登 录
            </el-button>
          </el-form-item>
        </el-form>
        <div class="link-row">
          <span class="muted">还没有账号？</span>
          <el-button link type="primary" @click="$router.push('/register')">立即注册</el-button>
          <el-button link type="info" @click="openReset">忘记密码</el-button>
        </div>
      </div>
    </div>

    <el-dialog v-model="resetDlg" title="找回密码" width="440px">
      <el-alert v-if="resetOpts && !resetOpts.enabled" type="warning" :closable="false"
                show-icon class="mb16" title="找回密码已关闭"
                description="管理员已关闭此功能，请联系管理员重置密码。" />
      <template v-if="!resetOpts || resetOpts.enabled">
        <el-alert type="info" :closable="false" show-icon class="mb16"
                  title="验证方式由管理员在后台设定：可用的方式才会出现在下面。"
                  description="「图形验证码 + 身份证号」核验通过即刻重置；若信息对不上，会自动转为待管理员审核的申请。" />
        <el-form :model="rf" label-position="top">
        <el-form-item label="验证方式">
          <el-radio-group v-model="rf.target_type" @change="onResetTypeChange">
            <el-radio-button v-if="resetOpts && resetOpts.captcha && resetOpts.captcha.enabled"
                             value="captcha">图形验证码 + 身份证号</el-radio-button>
            <el-radio-button v-if="resetOpts && resetOpts.sms && resetOpts.sms.enabled"
                             value="sms">手机验证码</el-radio-button>
            <el-radio-button v-if="resetOpts && resetOpts.email && resetOpts.email.enabled"
                             value="email">邮箱验证码</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <template v-if="rf.target_type === 'captcha'">
          <el-form-item label="账号">
            <el-input v-model="rf.username" placeholder="用户名或手机号" />
          </el-form-item>
          <el-form-item label="身份证号">
            <el-input v-model="rf.id_number" maxlength="30" placeholder="本人身份证号" />
          </el-form-item>
          <el-form-item label="图形验证码">
            <div class="captcha-row">
              <el-input v-model="rf.captcha_code" maxlength="4" placeholder="请输入右侧验证码"
                        style="flex:1" />
              <img class="captcha-img" :src="resetCaptcha.image" @click="loadResetCaptcha"
                   title="点击刷新" />
            </div>
          </el-form-item>
        </template>

        <template v-else>
        <el-form-item :label="rf.target_type === 'sms' ? '手机号' : '邮箱'">
          <el-input v-model="rf.target" :placeholder="rf.target_type === 'sms' ? '请输入绑定的手机号' : '请输入绑定的邮箱'" />
        </el-form-item>
        <el-form-item label="验证码">
          <div style="display:flex;gap:8px;width:100%">
            <el-input v-model="rf.code" placeholder="6 位验证码" style="flex:1" />
            <el-button :disabled="countdown > 0 || !rf.target" :loading="sending" @click="sendCode">
              {{ countdown > 0 ? countdown + ' 秒后重发' : '发送验证码' }}
            </el-button>
          </div>
          <div v-if="devCode" class="hint">演示模式验证码：{{ devCode }}</div>
        </el-form-item>
        </template>
        <el-form-item label="新密码">
          <el-input v-model="rf.new_password" type="password" show-password
                    placeholder="不少于 8 位，含两类以上字符" />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="rf.confirm_password" type="password" show-password
                    placeholder="请再次输入新密码" />
        </el-form-item>
        </el-form>
      </template>
      <template #footer>
        <el-button @click="resetDlg = false">取消</el-button>
        <el-button type="primary" :loading="resetting" @click="doReset">确认重置</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const formRef = ref(null);
    const loading = ref(false);
    const form = reactive({ account: '', password: '', remember: true });
    const rules = {
      account: [{ required: true, message: '请输入用户名或手机号', trigger: 'blur' }],
      password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
    };

    async function submit() {
      try {
        await formRef.value.validate();
      } catch (e) { return; }
      loading.value = true;
      try {
        const data = await api.post('/api/auth/login', {
          account: form.account, password: form.password, remember: !!form.remember,
        });
        session.save(data);
        setUser(data.user);
        await loadDicts();
        msg.ok('登录成功，欢迎 ' + (data.user.real_name || data.user.username));
        location.hash = '#/';
      } catch (e) {
        handleErr(e);
      } finally {
        loading.value = false;
      }
    }

    // ---------------------------------------------------------- 找回密码
    const resetDlg = ref(false);
    const sending = ref(false);
    const resetting = ref(false);
    const countdown = ref(0);
    const devCode = ref('');
    const rf = reactive({ target_type: 'captcha', target: '', code: '', username: '',
                          id_number: '', captcha_code: '',
                          new_password: '', confirm_password: '' });
    // 后台开关：决定找回密码能否用、以及有哪些验证方式
    const resetOpts = ref(null);
    const resetCaptcha = reactive({ captcha_id: '', image: '' });

    async function loadResetCaptcha() {
      const d = await api.get('/api/auth/captcha');
      resetCaptcha.captcha_id = d.captcha_id;
      resetCaptcha.image = d.image;
      rf.captcha_code = '';
    }

    async function loadResetOptions() {
      try {
        resetOpts.value = await api.get('/api/auth/verify-options?purpose=reset');
      } catch (e) { resetOpts.value = null; }
      // 默认选中第一个「后台已开启」的方式，避免停在未开启的项上
      const o = resetOpts.value || {};
      const first = ['captcha', 'sms', 'email'].find(
        (k) => o[k] && o[k].enabled);
      rf.target_type = first || 'captcha';
      if (rf.target_type === 'captcha') loadResetCaptcha();
    }

    function onResetTypeChange() {
      if (rf.target_type === 'captcha') loadResetCaptcha();
    }

    function openReset() {
      Object.assign(rf, { target: '', code: '', username: '', id_number: '',
                          captcha_code: '', new_password: '', confirm_password: '' });
      devCode.value = '';
      resetDlg.value = true;
      loadResetOptions();
    }

    async function sendCode() {
      if (!rf.target) { msg.warn('请先填写手机号或邮箱'); return; }
      sending.value = true;
      try {
        const d = await api.post('/api/auth/send-code', {
          target_type: rf.target_type, target: rf.target, purpose: 'reset',
        });
        devCode.value = d.code || '';   // 自测模式才会回显
        msg.ok('验证码已发送');
        countdown.value = d.resend_after || 60;
        const t = setInterval(() => {
          countdown.value -= 1;
          if (countdown.value <= 0) clearInterval(t);
        }, 1000);
      } catch (e) { handleErr(e); } finally { sending.value = false; }
    }

    async function doReset() {
      if (rf.target_type === 'captcha') {
        if (!rf.username || !rf.id_number || !rf.captcha_code) {
          msg.warn('请填写账号、身份证号与图形验证码'); return;
        }
      } else if (!rf.target || !rf.code) {
        msg.warn('请填写验证码'); return;
      }
      if (rf.new_password !== rf.confirm_password) { msg.warn('两次输入的新密码不一致'); return; }
      resetting.value = true;
      try {
        const payload = { ...rf, captcha_id: resetCaptcha.captcha_id };
        const r = await api.post('/api/auth/reset-password', payload);
        // 身份证核验不过时会转为待管理员审核的申请，这里要如实告诉用户
        if (r && r.pending) {
          msg.ok('身份信息未核验通过，已提交管理员审核，请耐心等待');
        } else {
          msg.ok('密码已重置，请使用新密码登录');
        }
        resetDlg.value = false;
      } catch (e) {
        handleErr(e);
        // 验证码一次性：失败后刷新，避免用户拿同一张图反复试
        if (rf.target_type === 'captcha') loadResetCaptcha();
      } finally { resetting.value = false; }
    }

    return { form, rules, formRef, loading, submit,
             resetDlg, rf, sending, resetting, countdown, devCode, openReset, sendCode, doReset,
             resetOpts, resetCaptcha, loadResetCaptcha, onResetTypeChange };
  },
};

export const RegisterPage = {
  template: `
  <div class="auth-wrap">
    <div class="auth-box">
      <div class="auth-side">
        <h1>考生注册</h1>
        <div class="sub">注册后即可在线报名开放中的考试批次。<br>审核员与管理员账号由管理员在后台开通。</div>
        <ul>
          <li>用户名 3-20 位字母/数字/下划线</li>
          <li>手机号需为 11 位数字</li>
          <li>密码不少于 8 位且含两类字符</li>
          <li>验证方式由管理员在后台设定，只显示已开启的</li>
        </ul>
      </div>
      <div class="auth-main">
        <h2>创建考生账号</h2>
        <div class="tip">以下均为必填项</div>
        <el-form :model="form" :rules="rules" ref="formRef" label-position="top">
          <el-row :gutter="14">
            <el-col :span="12">
              <el-form-item label="用户名 *" prop="username">
                <el-input v-model="form.username" placeholder="登录用户名" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="手机号 *" prop="phone">
                <el-input v-model="form.phone" maxlength="11" placeholder="11 位手机号" />
              </el-form-item>
            </el-col>
            <!-- 邮箱只在「邮箱验证」已开启时出现：没开这个方式就不需要填 -->
            <el-col :span="24" v-if="needEmail">
              <el-form-item label="邮箱 *" prop="email">
                <el-input v-model="form.email" placeholder="用于接收注册验证码" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="密码 *" prop="password">
                <el-input v-model="form.password" type="password" show-password placeholder="不少于 8 位" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="确认密码 *" prop="confirm_password">
                <el-input v-model="form.confirm_password" type="password" show-password placeholder="再次输入密码" />
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item label="验证方式">
                <el-radio-group v-model="form.verify_type" size="default" @change="onVerifyTypeChange">
                  <el-radio-button v-if="optOn('captcha')" value="captcha">图形验证码</el-radio-button>
                  <el-radio-button v-if="optOn('sms')" value="sms">手机验证码</el-radio-button>
                  <el-radio-button v-if="optOn('email')" value="email">邮箱验证码</el-radio-button>
                </el-radio-group>
                <div class="muted small" style="margin-top:6px">
                  只显示管理员已开启的方式
                  <span v-if="form.verify_type !== 'captcha' && !gatewayReady">
                    ｜{{ form.verify_type === 'email' ? '邮件' : '短信' }}网关未配置，验证码将在服务端控制台输出（演示模式）
                  </span>
                </div>
              </el-form-item>
            </el-col>

            <el-col :span="24" v-if="form.verify_type === 'captcha'">
              <el-form-item label="图形验证码 *" prop="captcha_code">
                <div class="captcha-row">
                  <el-input v-model="form.captcha_code" maxlength="4" placeholder="请输入右侧验证码"
                            style="flex:1" />
                  <img class="captcha-img" :src="captcha.image" @click="loadCaptcha" title="点击刷新" />
                </div>
              </el-form-item>
            </el-col>

            <el-col :span="24" v-else>
              <el-form-item :label="verifyType === 'email' ? '邮箱验证码 *' : '手机验证码 *'" prop="code">
                <div class="captcha-row">
                  <el-input v-model="form.code" maxlength="6" :placeholder="codePlaceholder"
                            style="flex:1" @keyup.enter="submit" />
                  <el-button :disabled="countdown > 0 || sending" :loading="sending"
                             @click="sendCode" style="width:150px">
                    {{ countdown > 0 ? countdown + ' 秒后重发' : '获取验证码' }}
                  </el-button>
                </div>
                <div class="muted small" style="margin-top:6px">
                  验证码将发送至 {{ codeTarget || '（请先填写' + (verifyType === 'email' ? '邮箱' : '手机号') + '）' }}
                  <span v-if="devCode" style="color:#e6a23c">｜演示验证码：{{ devCode }}</span>
                </div>
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" size="large" style="width:100%" :loading="loading" @click="submit">
              注 册
            </el-button>
          </el-form-item>
        </el-form>
        <div class="link-row">
          <span class="muted">已有账号？</span>
          <el-button link type="primary" @click="$router.push('/login')">返回登录</el-button>
        </div>
      </div>
    </div>
  </div>`,
  setup() {
    const formRef = ref(null);
    const loading = ref(false);
    const sending = ref(false);
    const countdown = ref(0);
    const devCode = ref('');
    // ⚠ 默认不能是「全开」：后端未开启的方式这里必须当作不存在，
    //   否则用户能看到并选中、点发送却被 403 拒（前后端口径不一致）。
    const options = ref({ email: { enabled: false, gateway_ready: false },
                          sms: { enabled: false, gateway_ready: false },
                          captcha: { enabled: true, gateway_ready: true } });
    const captcha = reactive({ captcha_id: '', image: '', debug_code: '' });
    const form = reactive({
      username: '', phone: '', email: '',
      password: '', confirm_password: '',
      verify_type: 'captcha', captcha_code: '', code_target: '', code: '',
    });

    let timer = null;

    const optOn = (k) => !!(options.value[k] && options.value[k].enabled);
    // 邮箱字段只在「邮箱验证」开启时才要求填：没开这个方式就不该逼用户填
    const needEmail = computed(() => optOn('email'));
    const verifyType = computed(() => form.verify_type);
    const codeTarget = computed(() =>
      form.verify_type === 'email' ? (form.email || '').trim() : (form.phone || '').trim());
    const gatewayReady = computed(() => {
      const o = options.value[form.verify_type];
      return o ? !!o.gateway_ready : false;
    });
    const codePlaceholder = computed(() =>
      form.verify_type === 'email' ? '请输入邮箱收到的 6 位验证码' : '请输入手机收到的 6 位验证码');

    const rules = {
      username: [
        { required: true, message: '请输入用户名', trigger: 'blur' },
        { pattern: /^[A-Za-z0-9_]{3,20}$/, message: '3-20 位字母、数字或下划线', trigger: 'blur' },
      ],
      phone: [
        { required: true, message: '请输入手机号', trigger: 'blur' },
        { pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: 'blur' },
      ],
      email: [{
        validator: (r, v, cb) => {
          if (!needEmail.value) return cb();          // 未开启邮箱验证时不要求
          if (!v) return cb(new Error('请输入邮箱'));
          return /^[\w.\-+]+@[\w\-]+(\.[\w\-]+)+$/.test(v) ? cb() : cb(new Error('邮箱格式不正确'));
        },
        trigger: 'blur',
      }],
      password: [
        { required: true, message: '请输入密码', trigger: 'blur' },
        { min: 8, message: '密码不少于 8 位', trigger: 'blur' },
      ],
      confirm_password: [
        { required: true, message: '请再次输入密码', trigger: 'blur' },
        {
          validator: (r, v, cb) => (v === form.password ? cb() : cb(new Error('两次输入的密码不一致'))),
          trigger: 'blur',
        },
      ],
      captcha_code: [{ required: true, message: '请输入图形验证码', trigger: 'blur' }],
      code: [{
        validator: (r, v, cb) => {
          if (form.verify_type === 'captcha') return cb();
          if (!v) return cb(new Error('请输入验证码'));
          cb();
        },
        trigger: 'blur',
      }],
    };

    function startCountdown(sec) {
      countdown.value = sec || 60;
      if (timer) clearInterval(timer);
      timer = setInterval(() => {
        countdown.value -= 1;
        if (countdown.value <= 0) { clearInterval(timer); timer = null; countdown.value = 0; }
      }, 1000);
    }

    async function loadCaptcha() {
      const d = await api.get('/api/auth/captcha');
      captcha.captcha_id = d.captcha_id;
      captcha.image = d.image;
      captcha.debug_code = d.debug_code || '';
      form.captcha_code = '';
    }

    async function loadOptions() {
      try {
        const d = (await api.get('/api/auth/verify-options')) || {};
        options.value = {
          captcha: d.captcha || { enabled: true },
          email: d.email || { enabled: false },
          sms: d.sms || { enabled: false },
        };
        // 默认选中第一个「后台已开启」的方式；一个都没开（异常配置）时回落到图形验证码
        const first = ['captcha', 'sms', 'email'].find(optOn);
        form.verify_type = first || 'captcha';
        if (form.verify_type === 'captcha') loadCaptcha();
      } catch (e) { /* 忽略：回退到图形验证码 */ }
    }

    function onVerifyTypeChange() {
      form.code = '';
      devCode.value = '';
      if (form.verify_type === 'captcha') loadCaptcha();
    }

    async function sendCode() {
      const target = codeTarget.value;
      if (!target) {
        msg.warn('请先填写' + (form.verify_type === 'email' ? '邮箱' : '手机号'));
        return;
      }
      sending.value = true;
      try {
        const d = await api.post('/api/auth/send-code', {
          target_type: form.verify_type, target, purpose: 'register',
        });
        form.code_target = target;
        devCode.value = d.code || '';
        msg.ok('验证码已发送（' + (d.delivery === 'console' ? '演示模式：见下方提示' : '请注意查收') + '）');
        startCountdown(d.resend_after || 60);
      } catch (e) {
        handleErr(e);
        if (e && e.code === 429) startCountdown(60);
      } finally {
        sending.value = false;
      }
    }

    async function submit() {
      try { await formRef.value.validate(); } catch (e) { return; }
      if (form.verify_type !== 'captcha' && !form.code_target) {
        msg.warn('请先获取验证码');
        return;
      }
      loading.value = true;
      try {
        const payload = {
          username: form.username, phone: form.phone, email: form.email,
          password: form.password, confirm_password: form.confirm_password,
          verify_type: form.verify_type,
        };
        if (form.verify_type === 'captcha') {
          payload.captcha_id = captcha.captcha_id;
          payload.captcha_code = form.captcha_code;
        } else {
          payload.code_target = form.code_target;
          payload.code = form.code;
        }
        await api.post('/api/auth/register', payload);
        msg.ok('注册成功，请使用新账号登录');
        location.hash = '#/login';
      } catch (e) {
        handleErr(e);
        if (form.verify_type === 'captcha') loadCaptcha();
      } finally {
        loading.value = false;
      }
    }

    onMounted(() => { loadCaptcha(); loadOptions(); });
    onUnmounted(() => { if (timer) clearInterval(timer); });

    return {
      form, rules, formRef, loading, sending, countdown, devCode, captcha, options,
      verifyType, codeTarget, gatewayReady, codePlaceholder,
      loadCaptcha, sendCode, submit, onVerifyTypeChange, optOn, needEmail,
    };
  },
};
