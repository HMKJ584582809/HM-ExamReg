/* 模块3（补充）：批量导入报名数据 —— 管理权限级别（管理员 / 班主任）可用
   支持任意自定义表头：上传后先 /import/preview 自动识别列并转换数据，
   用户可在界面上逐列校正，再把 mapping / defaults 随 /import 提交。 */
import { api, handleErr, msg } from '../api.js';
import { store } from '../store.js';

const { ref, reactive, computed, onMounted, watch } = Vue;

export const ImportPage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>批量导入报名数据</h2>
        <div class="sub">
          下载官方模板 → 填写 → 上传导入。系统逐行校验并自动为考生建立账号；
          导入范围：<b>{{ scopeLabel }}</b>
        </div>
      </div>
      <el-button @click="loadBatches">刷新导入记录</el-button>
    </div>

    <el-alert v-if="!scopedReady" type="warning" show-icon :closable="false" class="mb16"
              :title="'当前账号未绑定管理范围（' + scopeLabel + '），导入会被拒绝，请联系管理员补全。'" />

    <el-row :gutter="16">
      <el-col :span="14">
        <div class="card">
          <h3 class="card-title">1 · 选择批次并下载模板</h3>
          <div class="filter-bar">
            <el-select v-model="examId" placeholder="请选择考试批次" style="width:340px" filterable>
              <el-option v-for="e in exams" :key="e.id" :label="e.label" :value="e.id">
                <span>{{ e.label }}</span>
                <span class="muted" style="float:right">已通过 {{ e.exportable }}</span>
              </el-option>
            </el-select>
            <el-button :disabled="!examId" @click="downloadTemplate">
              <el-icon><Download /></el-icon>&nbsp;下载导入模板
            </el-button>
          </div>
          <div class="muted small mt8" v-if="currentExam">
            批次类型：{{ currentExam.exam_type_label }}
            （{{ baseLabel }}）。
            <b>也可以直接上传自己手里的表格</b>（花名册、教务导出等）：系统会自动识别每一列
            对应的字段（顺序可不同、允许有多余列），并自动转换数据格式。
          </div>
        </div>

        <div class="card mt16">
          <h3 class="card-title">2 · 上传填写好的文件</h3>
          <div class="filter-bar mb16">
            <span class="muted small">导入后的审核状态</span>
            <el-radio-group v-model="auditStatus">
              <el-radio-button value="approved">直接标记为已通过</el-radio-button>
              <el-radio-button value="pending">标记为待审核</el-radio-button>
            </el-radio-group>
            <el-checkbox v-model="createAccounts">同时创建考生账号</el-checkbox>
          </div>

          <input ref="fileInput" type="file" accept=".xlsx" style="display:none" @change="onFilePicked" />
          <el-upload drag :auto-upload="false" :show-file-list="false" accept=".xlsx"
                     :on-change="onUploadChange" :disabled="!examId">
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">
              将 .xlsx 文件拖到此处，或<em>点击选择文件</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">
                仅支持 .xlsx（不超过 20MB）。{{ file ? '已选择：' + file.name : '' }}
              </div>
            </template>
          </el-upload>

          <div class="right mt16">
            <el-button @click="clearFile" :disabled="!file">清空</el-button>
            <el-button type="primary" :disabled="!examId || !file" :loading="importing || previewing"
                       @click="doImport">
              {{ preview ? '按当前匹配导入' : '开始导入' }}
            </el-button>
          </div>
        </div>

        <!-- 智能列识别：上传后自动解析，可手工校正后再导入 -->
        <div class="card mt16" v-if="preview">
          <h3 class="card-title">
            3 · 确认列匹配
            <span class="muted small">
              共 {{ preview.total_rows }} 行数据，表头在第 {{ preview.header_row + 1 }} 行；
              识别结果可逐列修改，确认无误后再导入
            </span>
          </h3>

          <el-alert v-if="preview.unmapped_headers && preview.unmapped_headers.length"
                    type="info" :closable="false" class="mb16"
                    :title="'以下列没能自动识别（不会导入），如需要请手动指定：'
                            + preview.unmapped_headers.join('、')" />
          <el-alert v-if="mappingMissingRequired.length" type="warning" :closable="false" class="mb16"
                    title="有必填字段没有对应列，请在下方为其填写统一默认值（应用到所有行）" />

          <el-table :data="preview.mapping" size="small" border max-height="380">
            <el-table-column prop="header" label="文件中的列" min-width="110" />
            <el-table-column label="识别为" min-width="190">
              <template #default="{ row }">
                <el-select v-model="mappingSel[row.col]" size="small" placeholder="不导入" clearable>
                  <el-option v-for="f in preview.fields" :key="f.key"
                             :label="f.label + (f.required ? '（必填）' : '')" :value="f.key" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="可信度" width="96" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.field && row.confidence >= 1" type="success" size="small">精确</el-tag>
                <el-tag v-else-if="row.field && row.confidence >= 0.7" type="warning" size="small">较可能</el-tag>
                <el-tag v-else-if="row.field" type="info" size="small">推测</el-tag>
                <el-tag v-else type="danger" size="small">未识别</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="样例数据" min-width="170">
              <template #default="{ row }">
                <span class="muted small">{{ (row.samples || []).join(' / ') || '—' }}</span>
              </template>
            </el-table-column>
          </el-table>

          <template v-if="mappingMissingRequired.length">
            <h4 class="card-title mt16">为缺失的必填字段指定统一默认值</h4>
            <el-form label-width="150px" size="small">
              <el-form-item v-for="f in mappingMissingRequired" :key="f.key" :label="f.label">
                <el-input v-model="defaultVals[f.key]" placeholder="该字段所有行都使用此值" />
              </el-form-item>
            </el-form>
          </template>

          <div v-if="preview.preview_rows && preview.preview_rows.length" class="mt16">
            <h4 class="card-title">转换效果预览（前 {{ preview.preview_rows.length }} 行）</h4>
            <el-table :data="preview.preview_rows" size="small" border max-height="200">
              <el-table-column v-for="f in previewFieldsUsed" :key="f.key"
                               :label="f.label" :prop="f.key" min-width="120" show-overflow-tooltip />
            </el-table>
          </div>
        </div>

        <div class="card mt16" v-if="result">
          <h3 class="card-title">导入结果</h3>
          <div class="kpi-grid mb16">
            <div class="kpi c-blue"><div class="k-label">总行数</div><div class="k-value">{{ result.total }}</div></div>
            <div class="kpi c-green"><div class="k-label">成功</div><div class="k-value">{{ result.success }}</div></div>
            <div class="kpi c-red"><div class="k-label">失败</div><div class="k-value">{{ result.failed }}</div></div>
            <div class="kpi c-orange">
              <div class="k-label">新建账号</div><div class="k-value">{{ result.created_account_total }}</div>
            </div>
          </div>
          <div class="right mb16">
            <el-button v-if="result.failed" type="warning" @click="downloadErrors(result.batch_id)">
              下载失败明细
            </el-button>
            <el-button v-if="result.created_account_total" @click="accountsDrawer = true">
              查看新建账号（{{ result.created_account_total }}）
            </el-button>
          </div>
          <el-table v-if="result.errors.length" :data="result.errors" size="small" border max-height="360">
            <el-table-column prop="row_no" label="行号" width="70" />
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column prop="id_number" label="证件号码" width="180" />
            <el-table-column prop="reason" label="失败原因" min-width="260" />
          </el-table>
          <div v-else class="muted">全部导入成功，无失败明细。</div>
        </div>
      </el-col>

      <el-col :span="10">
        <div class="card">
          <h3 class="card-title">导入须知</h3>
          <ul class="tips">
            <li>表头必须包含：<b>{{ requiredHint }}</b></li>
            <li>班主任导入的数据按文件中的「班级」归入其管理班级（班级须在您的管理范围内）</li>
            <li>同一批次内证件号码重复的行会被拒绝</li>
            <li>开启「同时创建考生账号」后，学生可用证件号后 8 位作为用户名登录</li>
            <li>导入结果与失败明细会保留在下方记录中，可随时重新下载</li>
          </ul>
        </div>

        <div class="card mt16">
          <h3 class="card-title">导入记录</h3>
          <el-table :data="batches" size="small" border v-loading="batchLoading" max-height="460">
            <el-table-column prop="created_at" label="时间" width="150" />
            <el-table-column prop="exam_name" label="批次" min-width="150" show-overflow-tooltip />
            <el-table-column label="结果" width="110">
              <template #default="{ row }">
                <span class="ok-text">{{ row.success }}</span> /
                <span :class="{ 'err-text': row.failed }">{{ row.failed }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90">
              <template #default="{ row }">
                <el-button link size="small" :disabled="!row.failed"
                           @click="downloadErrors(row.id)">明细</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-col>
    </el-row>

    <el-drawer v-model="accountsDrawer" title="本次新建的考生账号" size="760px">
      <el-alert type="warning" show-icon :closable="false" class="mb16"
                title="请及时将账号与初始密码转告学生，并提醒首次登录后修改密码。" />
      <el-table :data="result ? result.created_accounts : []" size="small" border>
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="username" label="登录用户名" width="150" />
        <el-table-column prop="initial_password" label="初始密码" width="130" />
        <el-table-column prop="class_name" label="班级" width="130" />
        <el-table-column prop="college" label="院系" min-width="140" />
      </el-table>
    </el-drawer>
  </div>`,
  setup() {
    const exams = ref([]);
    const examId = ref(null);
    const file = ref(null);
    const importing = ref(false);
    const result = ref(null);
    const auditStatus = ref('approved');
    const createAccounts = ref(true);
    const batches = ref([]);
    const batchLoading = ref(false);
    const accountsDrawer = ref(false);
    const fileInput = ref(null);
    // 智能列识别
    const preview = ref(null);
    const previewing = ref(false);
    const mappingSel = reactive({});   // { 列下标: 字段名 }
    const defaultVals = reactive({});  // { 字段名: 统一默认值 }

    const scopeLabel = computed(() => (store.user && store.user.scope_label) || '');
    const scopedReady = computed(() => {
      const s = store.user && store.user.scope;
      if (s === 'scope_class') return !!((store.user.classes || store.user.class_name || '').trim());
      return true;
    });
    const currentExam = computed(() => exams.value.find((e) => e.id === examId.value) || null);
    /** 基础模板的中文名（通用模板不能再说成「普通话模板」） */
    const baseLabel = computed(() => ({
      computer: '计算机类模板', mandarin: '普通话模板', generic: '通用模板',
    }[(currentExam.value || {}).exam_type_base] || '模板'));
    const requiredHint = computed(() => {
      const t = currentExam.value && currentExam.value.exam_type_base;
      return t === 'mandarin'
        ? '姓名、证件号码、联系电话、从事职业、出生地省、现居住地省'
        : '姓名、证件号码、手机号码、报考科目';
    });
    // 必填字段里还没有任何列映射到的（需用户在界面上填统一默认值）
    const mappingMissingRequired = computed(() => {
      if (!preview.value) return [];
      const used = new Set(Object.values(mappingSel).filter(Boolean));
      return (preview.value.fields || []).filter((f) => f.required && !used.has(f.key));
    });
    // 转换效果预览只展示实际用到的字段
    const previewFieldsUsed = computed(() => {
      if (!preview.value) return [];
      const used = new Set(Object.values(mappingSel).filter(Boolean));
      return (preview.value.fields || []).filter((f) => used.has(f.key));
    });

    async function loadExams() {
      try {
        exams.value = await api.get('/api/export/exam-options');
        if (!examId.value && exams.value.length) examId.value = exams.value[0].id;
      } catch (e) { handleErr(e); }
    }

    async function loadBatches() {
      batchLoading.value = true;
      try {
        const d = await api.get('/api/applications/import-batches?page_size=20');
        batches.value = d.list;
      } catch (e) { handleErr(e); } finally { batchLoading.value = false; }
    }

    async function downloadTemplate() {
      if (!examId.value) return;
      try {
        const e = currentExam.value;
        await api.download('/api/applications/import-template?exam_id=' + examId.value,
          '批量导入模板-' + (e && e.exam_type_base === 'mandarin' ? '普通话' : '计算机') + '.xlsx');
      } catch (err) { handleErr(err); }
    }

    function resetPreview() {
      preview.value = null;
      Object.keys(mappingSel).forEach((k) => delete mappingSel[k]);
      Object.keys(defaultVals).forEach((k) => delete defaultVals[k]);
    }

    function pickFile(f) {
      if (!f) return;
      if (!/\.xlsx$/i.test(f.name)) { msg.warn('请选择 .xlsx 格式文件'); return; }
      if (f.size > 20 * 1024 * 1024) { msg.warn('文件不能超过 20MB'); return; }
      file.value = f;
      resetPreview();
      doPreview();
    }

    function onUploadChange(uf) { pickFile(uf.raw); }
    function onFilePicked(ev) { pickFile(ev.target.files[0]); }
    function clearFile() {
      file.value = null;
      resetPreview();
      if (fileInput.value) fileInput.value.value = '';
    }

    /** 上传后先解析：自动识别每一列对应字段，并返回转换后的样例，供用户确认。 */
    async function doPreview() {
      if (!examId.value || !file.value) return;
      previewing.value = true;
      try {
        const fd = new FormData();
        fd.append('exam_id', examId.value);
        fd.append('file', file.value);
        const r = await api.upload('/api/applications/import/preview', fd);
        preview.value = r.data;
        Object.keys(mappingSel).forEach((k) => delete mappingSel[k]);
        (r.data.mapping || []).forEach((m) => { if (m.field) mappingSel[m.col] = m.field; });
        const n = Object.keys(mappingSel).length;
        const un = (r.data.unmapped_headers || []).length;
        msg.ok(un ? `已识别 ${n} 列，${un} 列未能自动识别（可在下方手动指定）`
                  : `已识别 ${n} 列，请核对后导入`);
      } catch (e) {
        handleErr(e);
        preview.value = null;
      } finally { previewing.value = false; }
    }

    async function doImport() {
      if (!examId.value) { msg.warn('请选择考试批次'); return; }
      if (!file.value) { msg.warn('请选择要导入的文件'); return; }
      importing.value = true;
      try {
        const fd = new FormData();
        fd.append('exam_id', examId.value);
        fd.append('audit_status', auditStatus.value);
        fd.append('create_accounts', createAccounts.value ? '1' : '0');
        // 有预览则提交用户确认/修正后的列映射与统一默认值
        if (preview.value) {
          const m = {};
          Object.keys(mappingSel).forEach((k) => { if (mappingSel[k]) m[k] = mappingSel[k]; });
          fd.append('mapping', JSON.stringify(m));
          const dv = {};
          Object.keys(defaultVals).forEach((k) => {
            if ((defaultVals[k] || '').trim()) dv[k] = defaultVals[k].trim();
          });
          if (Object.keys(dv).length) fd.append('defaults', JSON.stringify(dv));
        }
        fd.append('file', file.value);
        const r = await api.upload('/api/applications/import', fd);
        result.value = r.data;
        msg.ok(r.message);
        clearFile();
        loadBatches();
        loadExams();
      } catch (e) { handleErr(e); } finally { importing.value = false; }
    }

    async function downloadErrors(batchId) {
      try {
        await api.download(`/api/applications/import-batches/${batchId}/errors`,
          `导入失败明细-批次${batchId}.xlsx`);
      } catch (e) { handleErr(e); }
    }

    // 切换批次后字段集可能不同，已识别的列映射不再有效
    watch(examId, () => { if (preview.value) resetPreview(); });

    onMounted(() => { loadExams(); loadBatches(); });

    return {
      store, exams, examId, file, importing, result, auditStatus, createAccounts,
      batches, batchLoading, accountsDrawer, fileInput, scopeLabel, scopedReady,
      currentExam, requiredHint, loadExams, loadBatches, downloadTemplate,
      onUploadChange, onFilePicked, clearFile, doImport, downloadErrors,
      preview, previewing, mappingSel, defaultVals, doPreview,
      mappingMissingRequired, previewFieldsUsed, baseLabel,
    };
  },
};
