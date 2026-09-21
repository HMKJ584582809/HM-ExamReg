/* 模块5：汇总导出（严格对齐官方模板表头） */
import { api, handleErr, msg } from '../api.js';
import { can } from '../store.js';

const { ref, reactive, computed, onMounted } = Vue;

export const ExportPage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>汇总导出</h2>
        <div class="sub">按考试批次 + 审核状态导出，表头与 Sheet 结构逐字对齐官方模板</div>
      </div>
    </div>

    <div class="card">
      <div class="filter-bar">
        <el-select v-model="form.exam_id" placeholder="请选择考试批次" filterable style="width:340px"
                   @change="preview">
          <el-option v-for="e in examOptions" :key="e.id" :label="e.label" :value="e.id" />
        </el-select>
        <el-select v-model="form.audit_status" style="width:150px" @change="preview">
          <el-option label="仅已通过（默认）" value="approved" />
          <el-option label="全部状态" value="all" />
          <el-option label="待审核" value="pending" />
          <el-option label="已驳回" value="rejected" />
          <el-option label="已退回" value="returned" />
        </el-select>
        <el-input v-model="form.keyword" placeholder="姓名 / 证件号（可选）" clearable
                  style="width:200px" @keyup.enter="preview" @clear="preview" />
        <el-button type="primary" @click="preview" :loading="loading">预览</el-button>
        <el-button type="success" :disabled="!canExport" @click="download" :loading="exporting">
          导出 xlsx
        </el-button>
      </div>

      <!-- 需求6：表格来源二选一，默认「复制官方模板填充」 -->
      <div class="mode-row">
        <span class="mode-label">表格来源</span>
        <el-radio-group v-model="form.table_mode">
          <el-radio value="template">
            复制原始模板填充
            <span class="muted fs-12">（推荐：沿用官方文件的表头、列顺序、下拉与说明页）</span>
          </el-radio>
          <el-radio value="build">
            系统自建表格
            <span class="muted fs-12">（旧方式：系统生成表头与样式，行数大时更快）</span>
          </el-radio>
        </el-radio-group>
      </div>

      <el-alert class="mt16" type="info" :closable="false" show-icon
                title="导出规则：计算机类考试导出「报名导入模板-计算机」结构（Sheet1 13 列 + 填表说明 + 字典）；普通话水平测试导出「考生报名模板-普通话」结构（Sheet1 20 列 + 职业字典 + 省市区 + 版本）。「复制原始模板填充」会直接以官方模板文件为底稿写入数据，只填值不动表头。" />
    </div>

    <div class="card" v-if="info">
      <div class="card-title">
        <span class="t">{{ info.exam_name }} · {{ info.audit_status_label }}<span
          v-if="info.keyword"> · 关键字「{{ info.keyword }}」</span> · 共 {{ info.total }} 条</span>
        <el-tag size="small">{{ info.headers.length }} 列（与官方模板一致）</el-tag>
      </div>
      <el-table :data="rows" border stripe size="small" max-height="440">
        <el-table-column v-for="(h, i) in info.headers" :key="i" :label="h" min-width="130"
                         show-overflow-tooltip>
          <template #default="{ row }">{{ row[i] }}</template>
        </el-table-column>
      </el-table>
      <div v-if="info.total > rows.length" class="muted mt8">
        仅预览前 {{ rows.length }} 条，导出文件将包含全部 {{ info.total }} 条记录。
      </div>
    </div>

    <!-- 需求5：选一个批次，同时导出表格与照片 -->
    <div class="card">
      <div class="card-title">
        <span class="t">一键导出（汇总表 + 证件照）</span>
        <el-tag size="small" type="success">一次下载，压缩包内含两者</el-tag>
      </div>
      <div class="filter-bar">
        <el-select v-model="bform.exam_id" placeholder="请选择考试批次" filterable
                   style="width:300px">
          <el-option v-for="e in examOptions" :key="e.id" :label="e.label" :value="e.id" />
        </el-select>
        <el-select v-model="bform.audit_status" style="width:150px">
          <el-option label="仅已通过（默认）" value="approved" />
          <el-option label="全部状态" value="all" />
          <el-option label="待审核" value="pending" />
          <el-option label="已驳回" value="rejected" />
          <el-option label="已退回" value="returned" />
        </el-select>
        <el-radio-group v-model="bform.table_mode">
          <el-radio value="template">官方模板</el-radio>
          <el-radio value="build">自建表格</el-radio>
        </el-radio-group>
        <el-radio-group v-model="bform.photo_fmt">
          <el-radio value="jpg">照片 JPG</el-radio>
          <el-radio value="png">照片 PNG</el-radio>
        </el-radio-group>
        <el-checkbox v-model="bWithTable">汇总表</el-checkbox>
        <el-checkbox v-model="bWithPhotos">证件照</el-checkbox>
        <el-button type="primary" :loading="bundleBusy" @click="exportBundle">一键导出</el-button>
      </div>
      <el-alert class="mt16" type="info" :closable="false" show-icon
                title="压缩包结构：考试批次/汇总表/xxx.xlsx + 考试批次/证件照/院系/班级/证件号码.jpg；缺照片的考生名单写在《_导出说明.txt》里，方便补拍。人数多时可按班级或审核状态分批导出。" />
    </div>

    <!-- 需求6：模板来源与覆盖（管理员） -->
    <div class="card" v-if="canManage">
      <div class="card-title">
        <span class="t">官方模板管理</span>
        <el-tag size="small" type="info">{{ tplDir }}</el-tag>
      </div>
      <el-table :data="templates" size="small" border>
        <el-table-column prop="label" label="模板" min-width="180" />
        <el-table-column label="来源" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.source === 'custom' ? 'warning' : 'info'">
              {{ row.source === 'custom' ? '管理员上传' : '内置' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="160" />
        <el-table-column label="表头匹配" min-width="220">
          <template #default="{ row }">
            <span v-if="row.error" class="fs-12">{{ row.error }}</span>
            <template v-else>
              <el-tag size="small" type="success">已识别 {{ (row.matched || []).length }} 列</el-tag>
              <el-tag v-if="(row.missing || []).length" size="small" type="warning" class="ml8">
                缺 {{ row.missing.length }} 列
              </el-tag>
              <div class="muted fs-12 lh-17" v-if="(row.missing || []).length">
                {{ row.missing.join('、') }}
              </div>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="pickTemplate(row.base)">
              上传覆盖
            </el-button>
            <el-button link size="small" v-if="row.source === 'custom'"
                       @click="resetTemplate(row.base)">恢复内置</el-button>
          </template>
        </el-table-column>
      </el-table>
      <input ref="tplInput" type="file" accept=".xlsx" hidden @change="onTplPicked" />
      <div class="hint mt8">
        上传的模板存在数据目录下，升级程序不会被覆盖；上传时会校验 Sheet1 表头能否被识别，
        识别不了的文件会被拒绝，避免导出时列错位。
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        <span class="t">证件照一键导出</span>
        <el-tag size="small" type="info">按 考试 / 院系 / 班级 建目录</el-tag>
      </div>
      <div class="filter-bar">
        <el-select v-model="pform.exam_id" placeholder="请选择考试批次" filterable
                   style="width:280px" @change="pform.class_name = ''; photoStat()">
          <el-option v-for="e in examOptions" :key="e.id" :label="e.label" :value="e.id" />
        </el-select>
        <el-select v-model="pform.audit_status" style="width:150px" @change="photoStat">
          <el-option label="全部状态" value="" />
          <el-option label="仅已通过" value="approved" />
          <el-option label="待审核" value="pending" />
          <el-option label="已驳回" value="rejected" />
          <el-option label="已退回" value="returned" />
        </el-select>
        <el-select v-model="pform.class_name" style="width:180px" clearable
                   placeholder="全部班级" @change="photoStat" v-if="pclasses.length">
          <el-option v-for="c in pclasses" :key="c" :label="c" :value="c" />
        </el-select>
        <el-radio-group v-model="pform.fmt">
          <el-radio value="jpg">JPG</el-radio>
          <el-radio value="png">PNG</el-radio>
        </el-radio-group>
        <el-button type="primary" :loading="photoBusy" @click="exportPhotos">一键导出</el-button>
      </div>
      <el-alert class="mt16" type="info" :closable="false" show-icon
                title="照片按「考试批次 / 院系 / 班级 / 证件号码.格式」打包为 zip；缺照片的考生会列在压缩包内的《_导出说明.txt》里，方便补拍。人数多时可按班级分批导出。" />
      <div class="mt8" v-if="pstat">
        <el-tag size="small">共 {{ pstat.total }} 条报名</el-tag>
        <el-tag size="small" type="success" class="ml8">已上传 {{ pstat.with_photo }} 张</el-tag>
        <el-tag size="small" type="warning" class="ml8">缺 {{ pstat.without_photo }} 张</el-tag>
      </div>
    </div>

    <div class="card" v-if="files.length">
      <div class="card-title"><span class="t">最近导出的文件</span></div>
      <el-table :data="files" size="small" border>
        <el-table-column prop="name" label="文件名" min-width="320" />
        <el-table-column label="大小" width="120">
          <template #default="{ row }">{{ (row.size / 1024).toFixed(1) }} KB</template>
        </el-table-column>
        <el-table-column prop="created_at" label="生成时间" width="180" />
      </el-table>
    </div>
  </div>`,
  setup() {
    const route = VueRouter.useRoute();
    const examOptions = ref([]);
    const files = ref([]);
    const info = ref(null);
    const rows = ref([]);
    const loading = ref(false);
    const exporting = ref(false);
    const form = reactive({
      exam_id: route.query.exam_id ? parseInt(route.query.exam_id, 10) : '',
      audit_status: 'approved',
      keyword: '',
      table_mode: 'template',      // template=复制官方模板 / build=自建
    });
    const canExport = ref(false);
    const canManage = computed(() => can('user_manage'));

    /* 一键导出（汇总表 + 证件照） */
    const bform = reactive({ exam_id: '', audit_status: 'approved', table_mode: 'template',
                             photo_fmt: 'jpg' });
    const bWithTable = ref(true);
    const bWithPhotos = ref(true);
    const bundleBusy = ref(false);

    /* 官方模板管理 */
    const templates = ref([]);
    const tplDir = ref('');
    const tplInput = ref(null);
    const tplBase = ref('');

    async function loadOptions() {
      try { examOptions.value = await api.get('/api/analysis/exam-options'); } catch (e) { handleErr(e); }
      try { files.value = await api.get('/api/export/files'); } catch (e) { /* ignore */ }
    }

    async function preview() {
      if (!form.exam_id) { msg.warn('请先选择考试批次'); return; }
      loading.value = true;
      try {
        // keyword 必须带上：漏传会让预览不过滤，造成「搜索框无效」的错觉
        const data = await api.get(
          `/api/export/preview?exam_id=${form.exam_id}&audit_status=${form.audit_status}`
          + `&keyword=${encodeURIComponent(form.keyword || '')}`);
        info.value = data;
        rows.value = data.rows;
        canExport.value = data.total > 0;
      } catch (e) {
        info.value = null; rows.value = []; canExport.value = false;
        handleErr(e);
      } finally { loading.value = false; }
    }

    async function download() {
      exporting.value = true;
      try {
        const qs = new URLSearchParams({
          exam_id: form.exam_id, audit_status: form.audit_status, keyword: form.keyword || '',
          table_mode: form.table_mode,
        });
        await api.download('/api/export/download?' + qs.toString());
        msg.ok('导出完成，文件已开始下载');
        files.value = await api.get('/api/export/files');
      } catch (e) { handleErr(e); } finally { exporting.value = false; }
    }

    /* 证件照一键导出：按考试 / 院系 / 班级 建目录，文件名用证件号码 */
    const pform = reactive({ exam_id: '', audit_status: '', fmt: 'jpg', class_name: '' });
    const pstat = ref(null);
    const pclasses = ref([]);
    const photoBusy = ref(false);

    async function photoStat() {
      pstat.value = null;
      if (!pform.exam_id) return;
      try {
        pstat.value = await api.get(
          `/api/photos/export/stat?exam_id=${pform.exam_id}&audit_status=${pform.audit_status}`
          + `&class_name=${encodeURIComponent(pform.class_name || '')}`);
        if (pstat.value && pstat.value.classes) pclasses.value = pstat.value.classes;
      } catch (e) { /* 统计失败不阻塞导出 */ }
    }

    async function exportPhotos() {
      if (!pform.exam_id) { msg.warn('请先选择考试批次'); return; }
      photoBusy.value = true;
      try {
        await api.downloadPost('/api/photos/export', { ...pform });
        msg.ok('证件照已导出，文件已开始下载');
      } catch (e) { handleErr(e); } finally { photoBusy.value = false; }
    }

    async function exportBundle() {
      if (!bform.exam_id) { msg.warn('请先选择考试批次'); return; }
      if (!bWithTable.value && !bWithPhotos.value) { msg.warn('请至少勾选一项导出内容'); return; }
      bundleBusy.value = true;
      try {
        await api.downloadPost('/api/export/bundle', {
          exam_id: bform.exam_id, audit_status: bform.audit_status,
          table_mode: bform.table_mode, photo_fmt: bform.photo_fmt,
          with_table: bWithTable.value ? 1 : 0, with_photos: bWithPhotos.value ? 1 : 0,
        });
        msg.ok('已打包，文件开始下载');
        files.value = await api.get('/api/export/files');
      } catch (e) { handleErr(e); } finally { bundleBusy.value = false; }
    }

    async function loadTemplates() {
      if (!canManage.value) return;
      try {
        const d = await api.get('/api/export/templates');
        templates.value = d.items || [];
        tplDir.value = d.dir || '';
      } catch (e) { /* 管理员才看得到，失败不提示 */ }
    }

    function pickTemplate(base) {
      tplBase.value = base;
      if (tplInput.value) { tplInput.value.value = ''; tplInput.value.click(); }
    }

    async function onTplPicked(e) {
      const f = (e.target.files || [])[0];
      e.target.value = '';
      if (!f || !tplBase.value) return;
      const fd = new FormData();
      fd.append('file', f);
      try {
        await api.upload(`/api/export/template/upload?base=${tplBase.value}`, fd);
        msg.ok('模板已更新');
        await loadTemplates();
      } catch (err) { handleErr(err); }
    }

    async function resetTemplate(base) {
      try {
        await ElementPlus.ElMessageBox.confirm(
          '恢复后将改用系统内置的官方模板，上传的覆盖版会被删除，确定继续？', '恢复内置模板',
          { type: 'warning' });
      } catch (err) { return; }
      try {
        await api.post(`/api/export/template/reset?base=${base}`, {});
        msg.ok('已恢复为内置模板');
        await loadTemplates();
      } catch (err) { handleErr(err); }
    }

    onMounted(async () => {
      await loadOptions();
      await loadTemplates();
      if (form.exam_id) preview();
      if (form.exam_id && !pform.exam_id) { pform.exam_id = form.exam_id; photoStat(); }
      if (form.exam_id && !bform.exam_id) bform.exam_id = form.exam_id;
    });

    return { form, examOptions, files, info, rows, loading, exporting, preview, download, canExport,
      pform, pstat, pclasses, photoBusy, photoStat, exportPhotos,
      bform, bWithTable, bWithPhotos, bundleBusy, exportBundle,
      canManage, templates, tplDir, tplInput, pickTemplate, onTplPicked, resetTemplate };
  },
};
