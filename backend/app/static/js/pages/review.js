/* 模块4：信息审核（列表 / 详情 / 通过 / 驳回 / 退回 / 批量） */
import { api, handleErr, msg } from '../api.js';
import { store } from '../store.js';

const { ref, reactive, onMounted, computed } = Vue;

export const ReviewPage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>{{ canAudit ? '报名信息审核' : '报名数据查看' }}</h2>
        <div class="sub">
          <template v-if="canAudit">按批次 / 类型 / 状态筛选待审核记录，支持批量通过、驳回、退回修改（驳回与退回需填写意见）</template>
          <template v-else>当前账号仅可查看数据范围（{{ scopeLabel }}）内的报名信息，审核操作由审核员与管理员完成</template>
          <el-tag v-if="scopeLabel" size="small" type="info" style="margin-left:6px">{{ scopeLabel }}</el-tag>
        </div>
      </div>
      <div class="toolbar">
        <el-button @click="load" :loading="loading">刷新</el-button>
      </div>
    </div>

    <div class="card">
      <div class="filter-bar mb16">
        <el-select v-model="query.exam_id" placeholder="考试批次" clearable filterable style="width:260px">
          <el-option v-for="e in examOptions" :key="e.id" :label="e.label" :value="e.id" />
        </el-select>
        <el-select v-model="query.app_type" placeholder="报名类型" clearable style="width:150px">
          <el-option label="计算机类考试" value="computer" />
          <el-option label="普通话水平测试" value="mandarin" />
        </el-select>
        <el-select v-model="query.audit_status" placeholder="审核状态" clearable style="width:140px">
          <el-option label="待审核" value="pending" />
          <el-option label="已通过" value="approved" />
          <el-option label="已驳回" value="rejected" />
          <el-option label="已退回" value="returned" />
        </el-select>
        <el-input v-model="query.keyword" placeholder="姓名 / 证件号 / 手机号" clearable style="width:210px" />
        <el-button type="primary" @click="() => { query.page = 1; load(); }">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>

      <div class="toolbar mb8">
        <template v-if="canAudit">
          <el-button type="success" :disabled="!selection.length" @click="batch('approved')">
            批量通过（{{ selection.length }}）
          </el-button>
          <el-button type="warning" :disabled="!selection.length" @click="batch('returned')">
            批量退回
          </el-button>
          <el-button type="danger" :disabled="!selection.length" @click="batch('rejected')">
            批量驳回
          </el-button>
        </template>
        <span class="muted">共 {{ total }} 条记录</span>
      </div>

      <el-table :data="list" v-loading="loading" border stripe @selection-change="onSelect">
        <el-table-column type="selection" width="46" v-if="canAudit" />
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="exam_name" label="考试批次" min-width="180" show-overflow-tooltip />
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <span class="type-badge" :class="{ md: row.app_type === 'mandarin' }">{{ row.app_type_label }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="姓名" width="88" />
        <el-table-column prop="id_number" label="证件号码" width="170" />
        <el-table-column label="班级 / 院系" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            {{ [row.class_name, row.department || row.school].filter(Boolean).join(' / ') || '—' }}
          </template>
        </el-table-column>
        <el-table-column prop="phone" label="联系电话" width="120" />
        <el-table-column label="状态" width="95">
          <template #default="{ row }">
            <el-tag :type="tagType(row.audit_status)" size="small">{{ row.audit_status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="提交时间" width="155" />
        <el-table-column label="操作" :width="canAudit ? 180 : 80" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <template v-if="canAudit">
              <el-button link type="success" size="small" @click="audit(row, 'approved')">通过</el-button>
              <el-button link type="warning" size="small" @click="audit(row, 'returned')">退回</el-button>
              <el-button link type="danger" size="small" @click="audit(row, 'rejected')">驳回</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <div class="right mt16">
        <el-pagination background layout="total, sizes, prev, pager, next" :total="total"
                       :page-size="query.page_size" :page-sizes="[10, 20, 50]"
                       :current-page="query.page"
                       @current-change="(p) => { query.page = p; load(); }"
                       @size-change="(s) => { query.page_size = s; query.page = 1; load(); }" />
      </div>
    </div>

    <el-drawer v-model="drawer" title="报名详情与审核" size="660px">
      <div v-if="detail">
        <div class="card-title">
          <span class="t">{{ detail.exam_name }}</span>
          <el-tag :type="tagType(detail.audit_status)" size="small">{{ detail.audit_status_label }}</el-tag>
        </div>
        <div class="photo-row">
          <div class="photo-box">
            <img v-if="photoUrl" :src="photoUrl" class="photo-img" alt="证件照" />
            <div v-else class="photo-empty">无证件照</div>
          </div>
          <div class="muted small">
            考生证件照<br />用于核对本人与证件信息是否一致
          </div>
        </div>

        <div class="detail-grid">
          <template v-for="f in fields" :key="f.k">
            <div class="dl">{{ f.label }}</div>
            <div class="dv">{{ formatValue(f, detail) }}</div>
          </template>
        </div>

        <el-divider content-position="left">审核意见</el-divider>
        <template v-if="canAudit">
          <el-input v-model="comment" type="textarea" :rows="3"
                    placeholder="通过可不填；驳回 / 退回必须填写具体原因" />
          <div class="toolbar mt16">
            <el-button type="success" @click="audit(detail, 'approved')">审核通过</el-button>
            <el-button type="warning" @click="audit(detail, 'returned')">退回修改</el-button>
            <el-button type="danger" @click="audit(detail, 'rejected')">驳回</el-button>
          </div>
          <!-- 代考生撤回：仅报名期内的待审核记录可用（撤回为删除，非审核动作） -->
          <div class="mt16" v-if="detail.audit_status === 'pending'">
            <el-button link type="danger" size="small" @click="withdraw(detail)">
              代考生撤回报名
            </el-button>
            <span class="muted small ml8">仅报名期内可用；撤回会删除该记录，与「驳回」(保留记录)不同</span>
          </div>
        </template>
        <el-alert v-else type="info" :closable="false" show-icon
                  title="当前账号无审核权限，仅可查看该记录及其审核历史。" />

        <el-divider content-position="left">审核记录</el-divider>
        <el-timeline v-if="detail.audits && detail.audits.length">
          <el-timeline-item v-for="a in detail.audits" :key="a.id" :timestamp="a.created_at"
                            placement="top"
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
    const route = VueRouter.useRoute();
    const list = ref([]);
    const total = ref(0);
    const loading = ref(false);
    const selection = ref([]);
    const drawer = ref(false);
    const detail = ref(null);
    const photoUrl = ref('');
    const comment = ref('');
    const examOptions = ref([]);
    const canAudit = ref(true);
    const scopeLabel = ref('');
    const query = reactive({
      exam_id: route.query.exam_id ? parseInt(route.query.exam_id, 10) : '',
      app_type: route.query.app_type || '',
      audit_status: route.query.audit_status || '',
      keyword: '', page: 1, page_size: 10,
    });

    const common = [
      { k: 'name', label: '姓名' }, { k: 'gender', label: '性别' },
      { k: 'id_type', label: '证件类型', map: 'idType' }, { k: 'id_number', label: '证件号码' },
      { k: 'phone', label: '联系电话' },
    ];
    const computerFields = [
      { k: 'org_code', label: '考试机构编码' }, { k: 'org_name', label: '考试机构名称' },
      { k: 'exam_site_code', label: '考点编码' }, { k: 'subject', label: '报考科目' },
      { k: 'school', label: '就读或者毕业院校' }, { k: 'class_name', label: '班级' },
      { k: 'education', label: '学历' }, { k: 'email', label: 'Email' }, { k: 'address', label: '通讯地址' },
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
        const qs = new URLSearchParams();
        Object.keys(query).forEach((k) => { if (query[k] !== '' && query[k] !== null) qs.append(k, query[k]); });
        const data = await api.get('/api/applications?' + qs.toString());
        list.value = data.list;
        total.value = data.total;
        if (data.can_audit !== undefined) canAudit.value = !!data.can_audit;
        scopeLabel.value = data.scope_label || '';
      } catch (e) { handleErr(e); } finally { loading.value = false; }
    }

    function reset() {
      Object.assign(query, { exam_id: '', app_type: '', audit_status: '', keyword: '', page: 1 });
      load();
    }

    function onSelect(rows) { selection.value = rows; }

    async function openDetail(row) { return openDetailById(row.id, row.app_type); }

    /* 证件照：读取需鉴权头，取回 blob 转 objectURL（不把令牌拼进 URL） */
    async function loadPhoto(uid) {
      if (photoUrl.value) { URL.revokeObjectURL(photoUrl.value); photoUrl.value = ''; }
      if (!uid) return;
      try {
        const res = await api.raw('GET', `/api/photos/${uid}`);
        photoUrl.value = URL.createObjectURL(await res.blob());
      } catch (e) { photoUrl.value = ''; }      // 无照片是常态，不提示
    }

    /* 详情展示字段：优先按该考试类型的配置（管理员可改名 / 关闭字段） */
    function buildFields(d) {
      const base = d.app_type === 'computer' ? computerFields
        : (d.app_type === 'generic' ? genericFields : mandarinFields);
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

    /* 深链 /review/:id 与列表点击共用：app_type 可省略，由后端自动判定 */
    async function openDetailById(id, appType) {
      try {
        const url = `/api/applications/${id}` + (appType ? `?app_type=${appType}` : '');
        const d = await api.get(url);
        detail.value = d;
        fields.value = buildFields(d);
        comment.value = '';
        drawer.value = true;
        await loadPhoto(d.user_id);
      } catch (e) { handleErr(e); }
    }

    async function audit(row, action) {
      const label = { approved: '审核通过', rejected: '驳回', returned: '退回修改' }[action];
      let text = comment.value;
      if (action !== 'approved' && !text) {
        try {
          const r = await ElementPlus.ElMessageBox.prompt(
            `请输入${label}的原因（必填）`, label, {
              inputPlaceholder: '例如：证件号码与姓名不匹配',
              inputValidator: (v) => (!!v && v.trim().length > 0) || '审核意见不能为空',
            });
          text = r.value;
        } catch (e) { return; }
      }
      try {
        await api.put(`/api/applications/${row.id}/audit`,
          { app_type: row.app_type, action, comment: text });
        msg.ok(label + '成功');
        drawer.value = false;
        comment.value = '';
        load();
      } catch (e) { handleErr(e); }
    }

    async function batch(action) {
      const label = { approved: '通过', rejected: '驳回', returned: '退回' }[action];
      let commentText = '';
      if (action !== 'approved') {
        try {
          const r = await ElementPlus.ElMessageBox.prompt(
            `请输入批量${label}的审核意见（必填）`, `批量${label}`, {
              inputPlaceholder: '例如：信息核对无误', inputValidator: (v) => (!!v && v.trim().length > 0) || '审核意见不能为空',
            });
          commentText = r.value;
        } catch (e) { return; }
      } else {
        try {
          await ElementPlus.ElMessageBox.confirm(
            `确认将选中的 ${selection.value.length} 条报名记录批量通过？`, '批量通过', { type: 'warning' });
        } catch (e) { return; }
      }
      try {
        const items = selection.value.map((r) => ({ app_type: r.app_type, id: r.id }));
        const res = await api.post('/api/applications/batch-audit',
          { items, action, comment: commentText });
        msg.ok(`批量${label}完成：成功 ${res.success} 条` + (res.errors.length ? `，失败 ${res.errors.length} 条` : ''));
        load();
      } catch (e) { handleErr(e); }
    }

    async function withdraw(row) {
      try {
        await ElementPlus.ElMessageBox.confirm(
          `将删除该考生的报名记录（${row.name || ''} · ${row.exam_name || ''}），不可恢复。`
          + '考生可在报名期内重新报名。确认继续？',
          '代考生撤回', { type: 'warning', confirmButtonText: '确认撤回', cancelButtonText: '取消' });
      } catch (e) { return; }
      try {
        await api.post(`/api/applications/${row.id}/withdraw?app_type=${row.app_type}`);
        msg.ok('已撤回该报名');
        drawer.value = false;
        await load();
      } catch (e) { handleErr(e); }
    }

    onMounted(async () => {
      try { examOptions.value = await api.get('/api/analysis/exam-options'); } catch (e) { /* ignore */ }
      await load();
      // 深链 /review/:id 直接打开对应报名详情
      const deepId = route.params && route.params.id;
      if (deepId) {
        const hit = list.value.find((r) => String(r.id) === String(deepId));
        openDetailById(parseInt(deepId, 10), hit ? hit.app_type : '');
      }
    });

    return {
      list, total, loading, query, load, reset, selection, onSelect, drawer, detail, fields,
      photoUrl,
      comment, audit, batch, openDetail, openDetailById, tagType, actionLabel, formatValue,
      examOptions, canAudit, scopeLabel, withdraw,
    };
  },
};
