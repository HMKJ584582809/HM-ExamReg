/* 模块11：实名信息（本人自填待审 + 第三方认证通道 + 管理员审核） */
import { api, handleErr, msg } from '../api.js';
import { can } from '../store.js';

const { ref, reactive, computed, onMounted } = Vue;

export const RealNamePage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>实名认证</h2>
        <div class="sub">
          用于确认考生身份真实性。默认由本人填写姓名与证件号后交管理员审核；
          管理员也可在下方启用支付宝 / 微信的第三方实名通道。
          实名通过后，找回密码可用「图形验证码 + 证件号」自助完成。
        </div>
      </div>
      <el-button @click="loadAll" :loading="loading">刷新</el-button>
    </div>

    <el-alert v-if="!enabled" type="info" :closable="false" show-icon
              title="管理员已关闭实名信息功能" class="mb16" />

    <!-- ───────────── 我的实名 ───────────── -->
    <div class="card">
      <h3 class="card-title">我的实名状态</h3>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="认证状态">
          <el-tag size="small" :type="statusType">{{ statusText }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="姓名">{{ profile.real_name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="证件类型">{{ profile.id_type_label || '—' }}</el-descriptions-item>
        <el-descriptions-item label="证件号码">{{ profile.id_number || '—' }}</el-descriptions-item>
        <el-descriptions-item label="认证方式">{{ profile.channel_label || '—' }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ profile.submitted_at || '—' }}</el-descriptions-item>
        <el-descriptions-item label="审核意见" :span="2">
          {{ profile.review_note || '（无）' }}
        </el-descriptions-item>
      </el-descriptions>
      <el-alert v-if="status === 'rejected'" type="error" :closable="false" class="mt12"
                title="实名信息被驳回，请根据审核意见修改后重新提交。" />
      <div class="hint mt8" v-if="requireApproved">
        管理员已开启「实名认证通过后才能报名」，未完成认证将无法提交报名表。
      </div>
    </div>

    <!-- ───────────── 自填提交 ───────────── -->
    <div class="card" v-if="enabled && selfFill">
      <h3 class="card-title">填写实名信息</h3>
      <el-alert v-if="status === 'approved'" type="success" :closable="false" class="mb12"
                title="实名信息已通过审核。如需变更请联系管理员撤销后重新提交。" />
      <el-form :model="form" label-width="100px" style="max-width:560px">
        <el-form-item label="真实姓名">
          <el-input v-model="form.real_name" placeholder="与证件完全一致"
                    :disabled="status === 'approved'" />
        </el-form-item>
        <el-form-item label="证件类型">
          <el-select v-model="form.id_type" style="width:100%"
                     :disabled="status === 'approved'">
            <el-option v-for="t in idTypes" :key="t.key" :label="t.label" :value="t.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="证件号码">
          <el-input v-model="form.id_number" maxlength="20" show-password
                    placeholder="身份证 18 位，末位可为 X"
                    :disabled="status === 'approved'" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="submitSelf" :loading="saving"
                     :disabled="status === 'approved'">提交审核</el-button>
          <span class="muted fs-12 ml8">提交后进入管理员审核队列，审核期间可修改重交。</span>
        </el-form-item>
      </el-form>
    </div>

    <!-- ───────────── 第三方认证 ───────────── -->
    <div class="card" v-if="enabled">
      <h3 class="card-title">第三方实名认证</h3>
      <div class="hint lh-17 mb12">
        管理员启用并配置好密钥后，可直接使用支付宝 / 微信的实名能力：
        点击按钮 → 在弹出窗口完成授权 → 认证结果自动回填。
        未配置齐全时自动降级为<b>内置模拟通道</b>（仅用于联调，不会真的核验证件真伪）。
      </div>
      <div class="rn-channels">
        <div v-for="c in channels" :key="c.key" class="rn-channel">
          <div class="rn-ch-head">
            <b>{{ c.label }}</b>
            <el-tag size="small" :type="c.ready ? 'success' : (c.enable ? 'warning' : 'info')">
              {{ c.ready ? '已启用（真实认证）' : (c.enable ? '未配齐（模拟通道）' : '未启用') }}
            </el-tag>
          </div>
          <div class="muted fs-12 lh-17">{{ c.reason || (c.ready ? '配置完整，可发起真实认证。' : '') }}</div>
          <div class="mt8">
            <el-button size="small" type="primary" plain :disabled="!c.enable"
                       :loading="starting === c.key" @click="startThird(c.key)">
              发起{{ c.label }}认证
            </el-button>
          </div>
        </div>
      </div>

      <el-alert v-if="thirdUrl" type="info" :closable="false" class="mt12"
                title="请在弹出窗口完成授权">
        <div class="fs-13 lh-17">若窗口被拦截，可手动复制下面的地址打开：</div>
        <div class="rn-url">{{ thirdUrl }}</div>
      </el-alert>
      <el-alert v-if="ticket" type="warning" :closable="false" class="mt12"
                :title="mockTip">
        <div class="mt8">
          <el-button size="small" type="success" :loading="finishing"
                     @click="finishMock(1)">模拟认证通过</el-button>
          <el-button size="small" @click="finishMock(0)">模拟认证失败</el-button>
        </div>
      </el-alert>
    </div>

    <!-- ───────────── 管理员：审核 ───────────── -->
    <template v-if="canManage">
      <div class="card">
        <h3 class="card-title">
          实名审核
          <el-tag size="small" type="warning" v-if="counts.pending">
            待审 {{ counts.pending }}
          </el-tag>
        </h3>
        <div class="filter-bar mb16">
          <el-select v-model="query.status" placeholder="状态（全部）" clearable
                     style="width:150px" @change="loadList">
            <el-option label="待审核" value="pending" />
            <el-option label="已通过" value="approved" />
            <el-option label="已驳回" value="rejected" />
          </el-select>
          <el-input v-model="query.keyword" placeholder="姓名 / 证件号 / 用户名 / 手机号"
                    clearable style="width:280px" @keyup.enter="loadList" />
          <el-button type="primary" @click="loadList">查询</el-button>
        </div>
        <el-table :data="list" v-loading="loading" border stripe size="small">
          <el-table-column prop="username" label="账号" width="120" />
          <el-table-column prop="real_name" label="姓名" width="110" />
          <el-table-column label="证件" min-width="200">
            <template #default="{ row }">
              {{ row.id_type_label }} · {{ row.id_number }}
            </template>
          </el-table-column>
          <el-table-column prop="channel_label" label="认证方式" width="130" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="tagOf(row.status)">{{ labelOf(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="submitted_at" label="提交时间" width="160" />
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="{ row }">
              <el-button link type="success" size="small" v-if="row.status === 'pending'"
                         @click="review(row, 1)">通过</el-button>
              <el-button link type="danger" size="small" v-if="row.status === 'pending'"
                         @click="review(row, 0)">驳回</el-button>
              <el-button link size="small" v-if="row.status !== 'pending'"
                         @click="revoke(row)">撤销</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="right mt16">
          <el-pagination background layout="total, prev, pager, next" :total="total"
                         :page-size="query.page_size" :current-page="query.page"
                         @current-change="(p) => { query.page = p; loadList(); }" />
        </div>
      </div>

      <div class="card">
        <h3 class="card-title">实名设置</h3>
        <el-form :model="cfgForm" label-width="150px">
          <el-form-item label="启用实名信息">
            <el-switch v-model="cfgForm.enable" :active-value="1" :inactive-value="0" />
            <span class="muted fs-12 ml8">关闭后考生看不到本页的提交与认证入口</span>
          </el-form-item>
          <el-form-item label="允许本人自填">
            <el-switch v-model="cfgForm.self_fill" :active-value="1" :inactive-value="0" />
            <span class="muted fs-12 ml8">关闭后只能用第三方通道认证</span>
          </el-form-item>
          <el-form-item label="实名通过才可报名">
            <el-switch v-model="cfgForm.require_approved" :active-value="1" :inactive-value="0" />
            <span class="muted fs-12 ml8">开启后未实名通过的账号提交报名表会被拒绝</span>
          </el-form-item>
        </el-form>

        <div class="section-title">第三方通道</div>
        <el-alert type="info" :closable="false" class="mb12"
                  title="真实接入需要商户资质、AppID 与密钥，并且回调地址要能被公网访问。
                        单机内网部署收不到回调，因此未配置齐全时会自动降级为内置模拟通道。" />
        <el-form :model="cfgForm" label-width="150px">
          <template v-for="ch in cfgForm.channels" :key="ch.channel">
            <div class="section-title">{{ ch.label }}实名认证</div>
            <el-form-item :label="ch.label + '通道'">
              <el-switch :model-value="ch.enable" :active-value="1" :inactive-value="0"
                         @change="(v) => ch.enable = v" />
              <el-tag size="small" class="ml8" :type="ch.ready ? 'success' : 'info'">
                {{ ch.ready ? '配置完整' : ('未配齐：' + (ch.reason || '—')) }}
              </el-tag>
            </el-form-item>
            <el-form-item label="AppID">
              <el-input v-model="ch.app_id" style="max-width:420px" placeholder="应用唯一标识" />
            </el-form-item>
            <el-form-item v-if="ch.channel === 'wechat'" label="商户号">
              <el-input v-model="ch.mch_id" style="max-width:420px" placeholder="微信支付商户号" />
            </el-form-item>
            <el-form-item v-if="ch.channel === 'alipay'" label="应用私钥">
              <el-input v-model="ch.private_key" type="password" show-password
                        style="max-width:420px"
                        :placeholder="ch.private_key_set ? '已配置，留空表示不修改' : 'RSA2 应用私钥'" />
            </el-form-item>
            <el-form-item v-if="ch.channel === 'alipay'" label="支付宝公钥">
              <el-input v-model="ch.alipay_public_key" type="password" show-password
                        style="max-width:420px"
                        :placeholder="ch.alipay_public_key_set ? '已配置，留空表示不修改' : ''" />
            </el-form-item>
            <el-form-item v-if="ch.channel === 'wechat'" label="AppSecret">
              <el-input v-model="ch.app_secret" type="password" show-password
                        style="max-width:420px"
                        :placeholder="ch.app_secret_set ? '已配置，留空表示不修改' : ''" />
            </el-form-item>
            <el-form-item v-if="ch.channel === 'wechat'" label="APIv3 密钥">
              <el-input v-model="ch.api_v3_key" type="password" show-password
                        style="max-width:420px"
                        :placeholder="ch.api_v3_key_set ? '已配置，留空表示不修改' : ''" />
            </el-form-item>
          </template>
          <el-form-item>
            <el-button type="primary" @click="saveCfg" :loading="savingCfg">保存实名设置</el-button>
          </el-form-item>
        </el-form>
      </div>
    </template>
  </div>`,
  setup() {
    const loading = ref(false);
    const saving = ref(false);
    const savingCfg = ref(false);
    const starting = ref('');
    const finishing = ref(false);
    const enabled = ref(true);
    const selfFill = ref(true);
    const requireApproved = ref(false);
    const status = ref('');
    const profile = ref({});
    const idTypes = ref([]);
    const channels = ref([]);
    const thirdUrl = ref('');
    const ticket = ref('');
    const mockTip = ref('');
    const list = ref([]);
    const total = ref(0);
    const counts = reactive({ pending: 0, approved: 0, rejected: 0 });
    const query = reactive({ status: '', keyword: '', page: 1, page_size: 10 });
    const form = reactive({ real_name: '', id_type: '1', id_number: '' });
    const cfgForm = reactive({ enable: 1, self_fill: 1, require_approved: 0, channels: [] });
    const canManage = computed(() => can('user_manage'));

    const statusText = computed(() => (
      { pending: '待管理员审核', approved: '已通过', rejected: '已驳回' }[status.value]
      || '未提交'));
    const statusType = computed(() => (
      { pending: 'warning', approved: 'success', rejected: 'danger' }[status.value] || 'info'));
    function tagOf(s) { return { pending: 'warning', approved: 'success', rejected: 'danger' }[s] || 'info'; }
    function labelOf(s) { return { pending: '待审核', approved: '已通过', rejected: '已驳回' }[s] || s; }

    async function loadMe() {
      const d = await api.get('/api/realname/me');
      enabled.value = !!d.enabled;
      selfFill.value = !!d.self_fill;
      requireApproved.value = !!d.require_approved;
      status.value = d.status || '';
      profile.value = d.profile || {};
      idTypes.value = d.id_types || [];
      channels.value = d.channels || [];
      const p = d.profile || {};
      form.real_name = status.value === 'approved' ? '' : (p.real_name || '');
      form.id_type = p.id_type || '1';
      form.id_number = status.value === 'approved' ? '' : '';
    }

    async function loadList() {
      const qs = new URLSearchParams();
      if (query.status) qs.append('status', query.status);
      if (query.keyword) qs.append('keyword', query.keyword);
      qs.append('page', String(query.page));
      qs.append('page_size', String(query.page_size));
      const d = await api.get('/api/realname/list?' + qs.toString());
      list.value = d.list || [];
      total.value = d.total || 0;
      Object.assign(counts, d.counts || {});
    }

    async function loadCfg() {
      const d = await api.get('/api/realname/config');
      cfgForm.enable = d.enable ? 1 : 0;
      cfgForm.self_fill = d.self_fill ? 1 : 0;
      cfgForm.require_approved = d.require_approved ? 1 : 0;
      cfgForm.channels = (d.channels || []).map((c) => ({ ...c, enable: c.enable ? 1 : 0 }));
    }

    async function loadAll() {
      loading.value = true;
      try {
        await loadMe();
        if (canManage.value) { await loadList(); await loadCfg(); }
      } catch (e) { handleErr(e); } finally { loading.value = false; }
    }

    async function submitSelf() {
      if (!form.real_name || !form.id_number) { msg.warn('请填写姓名与证件号码'); return; }
      saving.value = true;
      try {
        await api.post('/api/realname/submit', { ...form });
        msg.ok('已提交，等待管理员审核');
        await loadMe();
      } catch (e) { handleErr(e); } finally { saving.value = false; }
    }

    async function startThird(ch) {
      starting.value = ch;
      thirdUrl.value = ''; ticket.value = ''; mockTip.value = '';
      try {
        const d = await api.post('/api/realname/thirdparty/start', {
          channel: ch,
          real_name: form.real_name || (profile.value.real_name || ''),
          id_type: form.id_type || '1',
          id_number: form.id_number || '',
          callback: location.origin + '/api/realname/thirdparty/callback?channel=' + ch,
        });
        if (d.mode === 'official' && d.authorize_url) {
          thirdUrl.value = d.authorize_url;
          window.open(d.authorize_url, '_blank');
        } else if (d.ticket) {
          ticket.value = d.ticket;
          mockTip.value = d.message || '当前为模拟通道';
        }
        msg.ok(d.message || '已发起认证');
      } catch (e) { handleErr(e); } finally { starting.value = ''; }
    }

    async function finishMock(okv) {
      finishing.value = true;
      try {
        if (okv === 1) {
          await api.post('/api/realname/thirdparty/finish', { ticket: ticket.value, result: 1 });
          msg.ok('实名认证已通过');
          ticket.value = '';
          await loadMe();
        } else {
          msg.warn('已按「认证未通过」处理，未写入实名信息');
          ticket.value = '';
        }
      } catch (e) { handleErr(e); } finally { finishing.value = false; }
    }

    async function review(row, approve) {
      if (approve !== 1) {
        try {
          const r = await ElementPlus.ElMessageBox.prompt('填写驳回原因（会展示给本人）',
            '驳回实名申请', { inputPlaceholder: '如：证件号与姓名不一致', inputType: 'textarea' });
          await api.post(`/api/realname/${row.id}/review`,
                         { approve: 0, note: (r && r.value) || '' });
        } catch (e) {
          if (e && e.message) handleErr(e);
          return;
        }
      } else {
        try {
          await ElementPlus.ElMessageBox.confirm(
            `确认通过「${row.real_name}」的实名信息？通过后该账号可用证件号自助找回密码。`,
            '审核通过', { type: 'warning' });
        } catch (e) { return; }
        await api.post(`/api/realname/${row.id}/review`, { approve: 1 });
      }
      msg.ok(approve === 1 ? '已通过' : '已驳回');
      await loadList();
      await loadMe();
    }

    async function revoke(row) {
      try {
        await ElementPlus.ElMessageBox.confirm(
          `撤销后「${row.real_name}」需重新提交实名信息，确定继续？`, '撤销实名',
          { type: 'warning' });
      } catch (e) { return; }
      await api.post(`/api/realname/${row.id}/revoke`, {});
      msg.ok('已撤销');
      await loadList();
      await loadMe();
    }

    async function saveCfg() {
      savingCfg.value = true;
      try {
        const patch = {
          enable: cfgForm.enable, self_fill: cfgForm.self_fill,
          require_approved: cfgForm.require_approved, alipay: {}, wechat: {},
        };
        for (const c of cfgForm.channels) {
          patch[c.channel] = {
            enable: c.enable ? 1 : 0, app_id: c.app_id || '',
            private_key: c.private_key || '', alipay_public_key: c.alipay_public_key || '',
            app_secret: c.app_secret || '', api_v3_key: c.api_v3_key || '',
            mch_id: c.mch_id || '',
          };
        }
        const d = await api.put('/api/realname/config', patch);
        cfgForm.channels = (d.channels || []).map((c) => ({ ...c, enable: c.enable ? 1 : 0 }));
        msg.ok('实名设置已保存');
        await loadMe();
      } catch (e) { handleErr(e); } finally { savingCfg.value = false; }
    }

    onMounted(loadAll);

    return {
      loading, saving, savingCfg, starting, finishing, enabled, selfFill, requireApproved,
      status, profile, idTypes, channels, thirdUrl, ticket, mockTip,
      list, total, counts, query, form, cfgForm, canManage,
      statusText, statusType, tagOf, labelOf,
      loadAll, loadList, submitSelf, startThird, finishMock, review, revoke, saveCfg,
    };
  },
};
