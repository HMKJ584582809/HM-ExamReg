/* 模块9：系统维护 —— 运行信息、数据统计、测试数据清理（正式交付前使用） */
import { api, handleErr, msg } from '../api.js';

const { ref, reactive, computed, onMounted } = Vue;

export const SystemPage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>系统维护</h2>
        <div class="sub">查看运行信息与数据规模；正式交付前可在此一键清理测试数据与测试账号。</div>
      </div>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-row :gutter="16">
      <el-col :span="12">
        <div class="card">
          <h3 class="card-title">运行信息</h3>
          <el-descriptions :column="1" border size="small" v-if="info">
            <el-descriptions-item label="系统名称">{{ info.app_name }}</el-descriptions-item>
            <el-descriptions-item label="版本">{{ info.version }}</el-descriptions-item>
            <el-descriptions-item label="运行模式">
              <el-tag size="small" :type="info.seed_demo ? 'warning' : 'success'">
                {{ info.seed_demo ? '演示模式（含演示数据）' : '正式模式（不含测试数据）' }}
              </el-tag>
              <el-tag size="small" type="info" style="margin-left:6px" v-if="info.dev_mode">
                自测模式
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="数据库">
              <div class="mono small">{{ info.db_path }}</div>
              <div class="muted small">{{ fmtSize(info.storage.db_bytes) }}</div>
            </el-descriptions-item>
            <el-descriptions-item label="导出目录">
              <div class="mono small">{{ info.export_dir }}</div>
              <div class="muted small">{{ info.storage.export_files }} 个文件 ·
                {{ fmtSize(info.storage.export_bytes) }}</div>
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </el-col>

      <el-col :span="12">
        <div class="card">
          <h3 class="card-title">数据规模</h3>
          <div class="kpi-grid" v-if="info">
            <div class="kpi c-blue">
              <div class="k-label">账号</div><div class="k-value">{{ info.counts.users }}</div>
              <div class="k-sub">管理员 {{ info.counts.admins }} · 考生 {{ info.counts.candidates }}</div>
            </div>
            <div class="kpi c-green">
              <div class="k-label">考试批次</div><div class="k-value">{{ info.counts.exams }}</div>
            </div>
            <div class="kpi c-orange">
              <div class="k-label">报名数据</div><div class="k-value">{{ info.counts.applications }}</div>
            </div>
            <div class="kpi c-red">
              <div class="k-label">审核记录</div><div class="k-value">{{ info.counts.audits }}</div>
              <div class="k-sub">导入批次 {{ info.counts.import_batches }}</div>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>

    <div class="card mt16">
      <h3 class="card-title">邮件 / 短信网关（验证码投递）</h3>
      <el-alert type="info" show-icon :closable="false" class="mb16"
                title="未配置网关时，验证码只输出到服务端控制台（演示模式）。配置后注册、找回密码等场景会真实发送。" />

      <el-form :model="gw" label-width="130px" class="ai-form" v-if="gw.smtp">
        <div class="section-title">邮件（SMTP）</div>
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="服务器">
            <el-input v-model="gw.smtp.host" placeholder="smtp.qq.com" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="端口">
            <el-input-number v-model="gw.smtp.port" :min="1" :max="65535" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="启用 SSL">
            <el-switch v-model="gw.smtp.ssl" /></el-form-item></el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="账号">
            <el-input v-model="gw.smtp.user" placeholder="发件邮箱" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="授权码/密码">
            <el-input v-model="gw.smtp.password" type="password" show-password
                      :placeholder="gw.smtp.password_set ? '已保存，留空则不修改' : '未设置'" />
          </el-form-item></el-col>
          <el-col :span="8"><el-form-item label="发件人">
            <el-input v-model="gw.smtp.sender" placeholder="留空同账号" /></el-form-item></el-col>
        </el-row>

        <div class="section-title">短信</div>
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="服务商">
            <el-select v-model="gw.sms.provider" placeholder="不启用" clearable style="width:100%">
              <el-option label="阿里云短信" value="aliyun" />
              <el-option label="通用 HTTP 网关" value="generic" />
            </el-select></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="网关地址">
            <el-input v-model="gw.sms.endpoint"
                      :placeholder="gw.sms.provider === 'aliyun' ? '留空用默认 dysmsapi.aliyuncs.com' : 'https://...'" />
          </el-form-item></el-col>
          <el-col :span="8"><el-form-item label="密钥">
            <el-input v-model="gw.sms.api_key" type="password" show-password
                      :placeholder="gw.sms.api_key_set ? (gw.sms.provider === 'aliyun' ? '已保存 AccessKeyId:Secret' : '已保存') : '未设置'" />
          </el-form-item></el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="短信签名">
            <el-input v-model="gw.sms.sign" placeholder="阿里云必填" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="模板 CODE">
            <el-input v-model="gw.sms.template" placeholder="如 SMS_123456" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="当前状态">
            <el-tag size="small" :type="gw.sms_configured ? 'success' : 'info'">
              {{ gw.sms_configured ? '短信已启用' : '未启用（演示模式）' }}
            </el-tag>
            <el-tag size="small" :type="gw.mail_configured ? 'success' : 'info'" style="margin-left:6px">
              {{ gw.mail_configured ? '邮件已启用' : '邮件未启用' }}
            </el-tag>
          </el-form-item></el-col>
        </el-row>

        <el-form-item>
          <el-button type="primary" :loading="gwSaving" @click="saveGw">保存配置</el-button>
          <el-button v-if="gw.sms.api_key_set" @click="clearSmsKey">清除已保存的短信密钥</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="card mt16">
      <h3 class="card-title">
        局域网访问
        <el-tag size="small" :type="lan.on ? 'success' : 'info'">
          {{ lan.on ? '已开启' : '仅本机' }}
        </el-tag>
      </h3>
      <el-alert class="mb16" type="info" show-icon :closable="false"
                title="开启后同一局域网（如同一间机房、同一办公室网络）的其它电脑也能打开本系统，适合集中采集报名信息。修改并保存后需关闭程序重新启动才生效。" />

      <el-form label-width="130px" v-if="gw.network">
        <el-form-item label="允许局域网访问">
          <el-switch v-model="gw.network.lan_access" :active-value="1" :inactive-value="0" />
          <span class="muted small ml8">
            关闭 = 只监听 127.0.0.1，其它电脑一律访问不到
          </span>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="gwSaving" @click="saveGw">保存配置</el-button>
          <span class="muted small ml8">保存后请关闭黑窗口并重新双击 exe 启动</span>
        </el-form-item>
      </el-form>

      <div v-if="lan.urls.length" class="mt8">
        <div class="muted small mb8">当前可访问地址：</div>
        <div class="mono small" style="line-height:1.9">
          <div>本机：{{ lan.local_url }}</div>
          <div v-for="u in lan.urls" :key="u">{{ u }}</div>
        </div>
        <div class="muted small mt8">
          其它电脑请用上面带 IP 的地址打开；<b>0.0.0.0 是监听地址，不是访问地址，浏览器打不开</b>。
        </div>
      </div>
      <el-alert v-else-if="lan.pending" class="mt8" type="warning" show-icon :closable="false"
                title="开关已打开，但当前这一次启动仍是「仅本机」模式 —— 请关闭黑窗口并重新双击 exe 启动后才会生效。" />
      <div v-else class="muted small">
        当前为仅本机模式（开关关闭）。打开开关并保存、重启程序后即可用局域网地址访问。
      </div>

      <el-alert class="mt16" type="warning" show-icon :closable="false"
                title="若同事打不开：多半是 Windows 防火墙拦截。首次监听局域网端口时系统通常会弹窗，请点「允许访问」；也可由管理员执行：netsh advfirewall firewall add rule name=考试报名系统 dir=in action=allow protocol=TCP localport=端口" />
    </div>

    <div class="card mt16">
      <h3 class="card-title">报名表单默认值</h3>
      <el-alert class="mb16" type="info" show-icon :closable="false"
                title="整校 / 整班统一报名时，「所在单位」几乎相同。这里填好后考生打开报名表单会自动预填（仍可自行修改）；留空表示不预填。" />

      <el-form label-width="130px" v-if="gw.apply">
        <el-form-item label="所在单位默认值">
          <el-input v-model="gw.apply.employer_default" maxlength="100" style="max-width:420px"
                    placeholder="本单位名称，如「某某职业技术学院」；留空则不预填" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="gwSaving" @click="saveGw">保存配置</el-button>
          <span class="muted small ml8">保存后立即对新打开的报名表单生效，无需重启</span>
        </el-form-item>
      </el-form>
    </div>

    <div class="card mt16">
      <h3 class="card-title">注册验证方式</h3>
      <el-alert class="mb16" type="info" show-icon :closable="false"
                title="注册页只显示这里开启的验证方式。"
                description="默认只开图形验证码。开短信/邮箱前请先在上面的网关里配好，否则开了也发不出验证码；至少要保留一种。" />

      <el-form label-width="140px" v-if="gw.register">
        <el-form-item label="图形验证码">
          <el-switch v-model="gw.register.captcha" :active-value="1" :inactive-value="0" />
        </el-form-item>
        <el-form-item label="手机短信验证">
          <el-switch v-model="gw.register.sms" :active-value="1" :inactive-value="0" />
          <span v-if="gw.register.sms && !gw.register.sms_ready"
                class="ml8" style="color:#e6a23c;font-size:12px">短信网关未配置，开启后仍无法发送</span>
        </el-form-item>
        <el-form-item label="邮箱验证">
          <el-switch v-model="gw.register.email" :active-value="1" :inactive-value="0" />
          <span v-if="gw.register.email && !gw.register.email_ready"
                class="ml8" style="color:#e6a23c;font-size:12px">邮件服务未配置，开启后仍无法发送</span>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="gwSaving" @click="saveGw">保存配置</el-button>
          <span class="muted small ml8">至少要保留一种验证方式</span>
        </el-form-item>
      </el-form>
    </div>

    <div class="card mt16">
      <h3 class="card-title">找回密码</h3>
      <el-alert class="mb16" type="info" show-icon :closable="false"
                title="关闭后登录页不再提供找回密码入口，已打开的请求也会被后端拒绝。"
                description="验证方式按实际需要开启：默认只开「图形验证码 + 身份证号」；短信与邮箱还需要先在上方配好对应网关，否则开了也发不出验证码。" />

      <el-form label-width="140px" v-if="gw.reset_password">
        <el-form-item label="启用找回密码">
          <el-switch v-model="gw.reset_password.enable" :active-value="1" :inactive-value="0" />
          <span class="muted small ml8">{{ gw.reset_password.enable ? '已开启' : '已关闭' }}</span>
        </el-form-item>
        <el-form-item label="图形验证码 + 身份证号">
          <el-switch v-model="gw.reset_password.captcha" :active-value="1" :inactive-value="0"
                     :disabled="!gw.reset_password.enable" />
          <span class="muted small ml8">核验通过即重置；核验不过自动转为待审核申请</span>
        </el-form-item>
        <el-form-item label="手机短信验证">
          <el-switch v-model="gw.reset_password.sms" :active-value="1" :inactive-value="0"
                     :disabled="!gw.reset_password.enable" />
          <span v-if="gw.reset_password.sms && !gw.reset_password.sms_ready"
                class="ml8" style="color:#e6a23c;font-size:12px">短信网关未配置，开启后仍无法发送</span>
        </el-form-item>
        <el-form-item label="邮箱验证">
          <el-switch v-model="gw.reset_password.email" :active-value="1" :inactive-value="0"
                     :disabled="!gw.reset_password.enable" />
          <span v-if="gw.reset_password.email && !gw.reset_password.email_ready"
                class="ml8" style="color:#e6a23c;font-size:12px">邮件服务未配置，开启后仍无法发送</span>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="gwSaving" @click="saveGw">保存配置</el-button>
          <span class="muted small ml8">开启时至少要保留一种验证方式</span>
        </el-form-item>
      </el-form>
    </div>

    <div class="card mt16">
      <h3 class="card-title">证件照初始化参数</h3>
      <el-alert class="mb16" type="info" show-icon :closable="false"
                title="这里是证件照制作的默认值（初始化参数）。"
                description="用户可在「证件照制作」页临时调整并记在本机；点「恢复初始化参数」就回到这里的设定。" />

      <el-form label-width="140px" v-if="gw.idphoto">
        <el-form-item label="远程抠图接口">
          <el-input v-model="gw.idphoto.remote_url" placeholder="留空=禁用远程引擎，只用本机模型"
                    style="max-width:520px" />
        </el-form-item>
        <el-form-item label="抠图阈值">
          <el-slider v-model="gw.idphoto.params.alpha_threshold" :min="0" :max="255" show-input
                     :show-input-controls="false" style="max-width:520px" />
          <div class="muted small">alpha 低于此值一律当背景。调高可消除轮廓发灰与底部漏色；过高会啃掉头发丝。</div>
        </el-form-item>
        <el-form-item label="边缘羽化">
          <el-slider v-model="gw.idphoto.params.edge_feather" :min="0" :max="60" show-input
                     :show-input-controls="false" style="max-width:520px" />
          <div class="muted small">阈值之上保留的过渡带宽度，避免硬边锯齿。</div>
        </el-form-item>
        <el-form-item label="底部补底">
          <el-slider v-model="gw.idphoto.params.bottom_fill" :min="0" :max="60" show-input
                     :show-input-controls="false" style="max-width:520px" />
          <div class="muted small">底部百分之多少的行内非实心像素强制透明（用底色填满），专治底部漏色；0 = 关闭。</div>
        </el-form-item>
        <el-form-item label="留白填充">
          <el-radio-group v-model="gw.idphoto.params.blank_fill">
            <el-radio-button value="edge">取照片边缘色</el-radio-button>
            <el-radio-button value="white">纯白</el-radio-button>
          </el-radio-group>
          <div class="muted small">仅「不换底」时生效；换底色时留白一律用所选底色。</div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="gwSaving" @click="saveGw">保存配置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="card mt16">
      <h3 class="card-title">测试模式（模拟数据）</h3>
      <el-alert class="mb16" type="warning" show-icon :closable="false"
                title="仅管理员可用，用于列表 / 导出 / 大屏的性能压测。"
                description="开启后按设定条数批量生成模拟账号与报名数据（只填必要字段，不追求完整）；关闭时自动清除全部模拟数据（按 mock_ 前缀识别），正式环境不会残留。" />

      <el-form label-width="140px" v-if="gw.mock">
        <el-form-item label="模拟数据条数">
          <el-input-number v-model="gw.mock.count" :min="100" :max="50000" :step="500" />
          <span class="muted small ml8">当前库内模拟数据：{{ mockCount }} 条</span>
        </el-form-item>
        <el-form-item>
          <el-button type="danger" plain :loading="mockBusy" v-if="gw.mock.enable"
                     @click="toggleMock(false)">关闭并清除模拟数据</el-button>
          <el-button type="warning" :loading="mockBusy" v-else @click="toggleMock(true)">
            开启并生成模拟数据
          </el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="card mt16 danger-card">
      <h3 class="card-title">
        <el-icon color="#f56c6c"><WarningFilled /></el-icon>&nbsp;数据清理（危险操作）
      </h3>
      <el-alert type="error" show-icon :closable="false" class="mb16"
                title="清理后数据不可恢复！执行前请先在「汇总导出」中导出需要保留的数据，或手动备份 data/app.db 文件。" />

      <div class="mb16">
        <div class="muted small mb8">选择要清理的数据范围：</div>
        <el-checkbox-group v-model="scopes">
          <div v-for="s in scopes_list" :key="s.key" class="scope-option">
            <el-checkbox :value="s.key">
              <b>{{ scopeName(s.key) }}</b>
              <span class="muted small"> — {{ s.label }}</span>
            </el-checkbox>
          </div>
        </el-checkbox-group>
      </div>

      <div class="filter-bar">
        <span class="muted small">请输入 <b>确认清空</b> 以继续：</span>
        <el-input v-model="confirmText" placeholder="确认清空" style="width:220px" />
        <el-button type="danger" :disabled="confirmText !== '确认清空' || !scopes.length"
                   :loading="resetting" @click="doReset">
          立即清理
        </el-button>
      </div>

      <div v-if="resetResult" class="mt16">
        <el-alert type="success" show-icon :closable="false"
                  :title="'清理完成：' + resetResult.done.join('；')" />
        <el-table :data="diffRows" size="small" border class="mt16" style="max-width:520px">
          <el-table-column prop="label" label="数据项" />
          <el-table-column prop="before" label="清理前" width="110" />
          <el-table-column prop="after" label="清理后" width="110" />
        </el-table>
      </div>
    </div>
  </div>`,
  setup() {
    const info = ref(null);
    const scopes = ref([]);
    const scopes_list = ref([]);
    const confirmText = ref('');
    const resetting = ref(false);
    const resetResult = ref(null);
    const diffRows = ref([]);
    const gw = ref({ smtp: {}, sms: {}, network: { lan_access: 1 },
      apply: { employer_default: '' },
      idphoto: { remote_url: '', prefer: 'local',
        params: { alpha_threshold: 55, edge_feather: 10, bottom_fill: 12, blank_fill: 'edge' } },
      mock: { enable: 0, count: 5000 },
      register: { captcha: 1, sms: 0, email: 0 },
      reset_password: { enable: 1, captcha: 1, sms: 0, email: 0 } });
    const mockBusy = ref(false);
    const mockCount = ref(0);
    // 局域网状态：开关已开但当前进程仍只监听本机 → 提示重启
    const lan = computed(() => {
      const l = (gw.value && gw.value.listen) || {};
      const on = !!(l.lan_access || l.wildcard);
      return {
        on,
        // port 为 0 说明不是由启动器拉起（开发模式直跑 uvicorn），回显不出真实地址
        pending: !!l.lan_access && !l.wildcard && !!l.port,
        local_url: l.local_url || '',
        urls: l.wildcard ? (l.lan_urls || []) : [],
      };
    });
    const gwSaving = ref(false);

    const NAMES = {
      users: '账号', admins: '管理员', candidates: '考生', exams: '考试批次',
      applications: '报名数据', audits: '审核记录', import_batches: '导入批次',
    };

    function scopeName(k) {
      return { applications: '报名数据', users: '账号', exams: '考试批次',
        exports: '导出文件', factory: '全部（恢复出厂）' }[k] || k;
    }

    function fmtSize(n) {
      if (!n) return '0 B';
      if (n < 1024) return n + ' B';
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
      return (n / 1024 / 1024).toFixed(2) + ' MB';
    }

    async function load() {
      try {
        const d = await api.get('/api/system/info');
        info.value = d;
        scopes_list.value = d.reset_scopes;
      } catch (e) { handleErr(e); }
    }

    async function loadGw() {
      try {
        const d = await api.get('/api/system/settings');
        // 老库/旧配置缺段时补齐，避免模板里 v-if 段内取值报错
        d.idphoto = d.idphoto || {};
        d.idphoto.params = Object.assign(
          { alpha_threshold: 55, edge_feather: 10, bottom_fill: 12, blank_fill: 'edge' },
          d.idphoto.params || {});
        d.mock = d.mock || { enable: 0, count: 5000 };
        d.register = d.register || { captcha: 1, sms: 0, email: 0 };
        gw.value = d;
        mockCount.value = (d.mock && d.mock.current) || 0;
      } catch (e) { handleErr(e); }
    }

    async function saveGw() {
      gwSaving.value = true;
      try {
        const r = await api.put('/api/system/settings', {
          smtp: gw.value.smtp, sms: gw.value.sms,
          network: gw.value.network || {},
          apply: gw.value.apply || {},
          idphoto: gw.value.idphoto || {},
          register: gw.value.register || {},
          reset_password: gw.value.reset_password || {},
        });
        msg.ok('配置已保存（局域网开关需重启程序后生效）');
        if (r) { r.idphoto = r.idphoto || {}; r.idphoto.params = r.idphoto.params || gw.value.idphoto.params;
                 r.mock = r.mock || gw.value.mock; gw.value = r; }
        await load();
      } catch (e) { handleErr(e); } finally { gwSaving.value = false; }
    }

    /* 测试模式：开启=批量灌模拟数据，关闭=全部清除。仅管理员可调用。 */
    async function toggleMock(on) {
      if (on) {
        try {
          await ElementPlus.ElMessageBox.confirm(
            `将生成 ${gw.value.mock.count} 条模拟数据（账号与报名记录），用于性能测试。确认继续？`,
            '开启测试模式', { type: 'warning', confirmButtonText: '确认生成', cancelButtonText: '取消' });
        } catch (e) { return; }
      }
      mockBusy.value = true;
      try {
        const r = await api.post('/api/system/mock', { enable: !!on, count: gw.value.mock.count });
        gw.value.mock = Object.assign({}, gw.value.mock,
          { enable: r.enable ? 1 : 0, count: gw.value.mock.count, current: r.count || 0 });
        mockCount.value = r.count || 0;
        const g = r.generated || {};
        msg.ok(on ? `已生成 ${g.apps || 0} 条模拟报名数据（${g.users || 0} 个模拟考生）`
                  : `已清除 ${r.removed || 0} 条模拟数据`);
        await load();
      } catch (e) { handleErr(e); } finally { mockBusy.value = false; }
    }

    async function clearSmsKey() {
      gw.value.sms.api_key = '__clear__';
      await saveGw();
    }

    async function doReset() {
      try {
        await ElementPlus.ElMessageBox.confirm(
          '即将清理所选数据，且不可恢复。确认继续？', '再次确认',
          { type: 'warning', confirmButtonText: '确认清理', cancelButtonText: '取消' });
      } catch (e) { return; }
      resetting.value = true;
      try {
        const r = await api.post('/api/system/reset', { scopes: scopes.value, confirm: confirmText.value });
        resetResult.value = r;
        diffRows.value = Object.keys(NAMES).map((k) => ({
          label: NAMES[k], before: (r.before || {})[k] ?? '-', after: (r.after || {})[k] ?? '-',
        }));
        msg.ok('清理完成');
        confirmText.value = '';
        scopes.value = [];
        load();
      } catch (e) { handleErr(e); } finally { resetting.value = false; }
    }

    onMounted(() => { load(); loadGw(); });

    return { info, scopes, scopes_list, confirmText, resetting, resetResult, diffRows,
             scopeName, fmtSize, load, doReset, gw, gwSaving, saveGw, clearSmsKey, lan,
             mockBusy, mockCount, toggleMock };
  },
};
