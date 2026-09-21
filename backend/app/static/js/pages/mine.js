/* 我的报名：列表 / 详情 / 修改 / 撤回 */
import { api, handleErr, msg } from '../api.js';
import { store } from '../store.js';

const { ref, onMounted } = Vue;

export const MinePage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>我的报名</h2>
        <div class="sub">查看报名记录与审核状态；待审核或已退回的记录可在报名期内修改或撤回</div>
      </div>
      <el-button @click="load" :loading="loading">刷新</el-button>
    </div>

    <div class="card">
      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="exam_name" label="考试批次" min-width="200" />
        <el-table-column label="类型" width="130">
          <template #default="{ row }">
            <span class="type-badge" :class="{ md: row.app_type === 'mandarin' }">{{ row.exam_type_label }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="id_number" label="证件号码" width="180" />
        <el-table-column prop="phone" label="联系电话" width="130" />
        <el-table-column label="审核状态" width="110">
          <template #default="{ row }">
            <el-tag :type="tagType(row.audit_status)" size="small">{{ row.audit_status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="audit_comment" label="审核意见" min-width="180" show-overflow-tooltip />
        <el-table-column prop="created_at" label="提交时间" width="165" />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button v-if="row.can_edit" link type="warning" size="small" @click="edit(row)">修改</el-button>
            <el-button v-if="row.can_edit && row.audit_status === 'pending'" link type="danger"
                       size="small" @click="withdraw(row)">撤回</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!list.length && !loading" class="empty-box">
        <el-empty description="暂无报名记录">
          <el-button type="primary" @click="$router.push('/')">去报名</el-button>
        </el-empty>
      </div>
    </div>

    <el-drawer v-model="drawer" title="报名详情" size="620px">
      <div v-if="detail">
        <el-alert v-if="detail.audit_comment" class="mb16" :closable="false" show-icon
                  :type="detail.audit_status === 'approved' ? 'success'
                    : (detail.audit_status === 'rejected' ? 'error' : 'warning')"
                  :title="'审核意见：' + detail.audit_comment" />
        <div class="card-title"><span class="t">{{ detail.exam_name }}</span>
          <el-tag :type="tagType(detail.audit_status)" size="small">{{ detail.audit_status_label }}</el-tag>
        </div>
        <div class="detail-grid">
          <template v-for="f in fields" :key="f.k">
            <div class="dl">{{ f.label }}</div>
            <div class="dv">{{ formatValue(f, detail) }}</div>
          </template>
        </div>
        <el-divider content-position="left">审核记录</el-divider>
        <el-timeline v-if="detail.audits && detail.audits.length">
          <el-timeline-item v-for="a in detail.audits" :key="a.id"
                            :timestamp="a.created_at" placement="top"
                            :type="a.action === 'approved' ? 'success' : (a.action === 'rejected' ? 'danger' : 'warning')">
            <b>{{ actionLabel(a.action) }}</b> · {{ a.reviewer_name || ('审核员#' + a.reviewer_id) }}
            <div class="muted">{{ a.comment || '—' }}</div>
          </el-timeline-item>
        </el-timeline>
        <div v-else class="muted">暂无审核记录</div>
      </div>
    </el-drawer>
  </div>`,
  setup() {
    const list = ref([]);
    const loading = ref(false);
    const drawer = ref(false);
    const detail = ref(null);

    const common = [
      { k: 'name', label: '姓名' }, { k: 'gender', label: '性别' },
      { k: 'id_type', label: '证件类型', map: 'idType' }, { k: 'id_number', label: '证件号码' },
      { k: 'phone', label: '联系电话' }, { k: 'created_at', label: '提交时间' },
    ];
    const computerFields = [
      { k: 'org_code', label: '考试机构编码' }, { k: 'org_name', label: '考试机构名称' },
      { k: 'exam_site_code', label: '考点编码' }, { k: 'subject', label: '报考科目' },
      { k: 'school', label: '就读或者毕业院校' }, { k: 'class_name', label: '班级' },
      { k: 'education', label: '学历' }, { k: 'email', label: 'Email' },
      { k: 'address', label: '通讯地址' },
    ];
    // 通用模板：核心字段之外的自定义字段在 buildFields 里按类型配置动态追加
    const genericFields = [
      { k: 'email', label: '电子邮箱' }, { k: 'class_name', label: '班级' },
      { k: 'college', label: '院系' },
    ];
    const mandarinFields = [
      { k: 'ethnicity', label: '考生民族' }, { k: 'occupation', label: '从事职业' },
      { k: 'employer', label: '所在单位' }, { k: 'student_no', label: '考生学号' },
      { k: 'class_name', label: '考生班级' }, { k: 'department', label: '考生院系' },
      { k: 'contact_address', label: '联系地址' }, { k: 'mail_address', label: '邮寄地址' },
      { k: 'postcode', label: '邮政编码' },
      { k: 'birth_province', label: '出生所在省' }, { k: 'birth_city', label: '出生所在城市' },
      { k: 'birth_county', label: '出生所在县(区)' },
      { k: 'live_province', label: '现居住省' }, { k: 'live_city', label: '现居住城市' },
      { k: 'live_county', label: '现居住县(区)' },
    ];
    const fields = ref([]);

    function tagType(s) {
      return { pending: 'warning', approved: 'success', rejected: 'danger', returned: 'info' }[s] || '';
    }
    function actionLabel(a) {
      return { approved: '审核通过', rejected: '审核驳回', returned: '退回修改' }[a] || a;
    }
    function formatValue(f, row) {
      // 通用模板的自定义字段值存在 extra 里，不在记录顶层
      const v = f.custom ? (row.extra || {})[f.k] : row[f.k];
      if (f.map === 'idType') {
        const t = store.dicts.idTypes.find((x) => x.code === v);
        return t ? `${t.code} - ${t.name}` : (v || '—');
      }
      if (Array.isArray(v)) return v.length ? v.join('、') : '—';
      return (v === null || v === undefined || v === '') ? '—' : v;
    }

    async function load() {
      loading.value = true;
      try {
        list.value = await api.get('/api/applications/mine');
      } catch (e) { handleErr(e); } finally { loading.value = false; }
    }

    /* 详情展示字段：优先按该考试类型的配置（管理员可改名 / 关闭字段），
       老数据没有配置时退回按基础模板取，保证行为不变。 */
    function buildFields(d, appType) {
      const base = appType === 'computer' ? computerFields
        : (appType === 'generic' ? genericFields : mandarinFields);
      const meta = d.fields || [];
      if (!meta.length) return [...base, ...common];
      const off = new Set(meta.filter((f) => f.enabled === false).map((f) => f.key));
      const label = {};
      meta.forEach((f) => { label[f.key] = f.label; });
      // 通用模板的自定义字段由管理员配置，值保存在 extra 里
      const custom = meta.filter((f) => f.custom)
        .map((f) => ({ k: f.key, label: f.label, custom: true }));
      return [...base, ...common]
        .map((f) => (label[f.k] ? { ...f, label: label[f.k] } : f))
        .filter((f) => !off.has(f.k))
        .concat(custom);
    }

    async function openDetail(row) {
      try {
        const d = await api.get(`/api/applications/${row.id}?app_type=${row.app_type}`);
        detail.value = d;
        fields.value = buildFields(d, row.app_type);
        drawer.value = true;
      } catch (e) { handleErr(e); }
    }

    function edit(row) {
      location.hash = `#/apply/${row.exam_id}?appId=${row.id}`;
    }

    async function withdraw(row) {
      try {
        await ElementPlus.ElMessageBox.confirm(
          `确认撤回对「${row.exam_name}」的报名？撤回后需重新报名。`, '撤回确认', { type: 'warning' });
      } catch (e) { return; }
      try {
        await api.post(`/api/applications/${row.id}/withdraw?app_type=${row.app_type}`);
        msg.ok('已撤回报名');
        load();
      } catch (e) { handleErr(e); }
    }

    onMounted(load);
    return { list, loading, drawer, detail, fields, load, openDetail, edit, withdraw,
      tagType, actionLabel, formatValue };
  },
};
