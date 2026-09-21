/* 模块2：考试批次管理（管理员） */
import { api, handleErr, msg } from '../api.js';

const { ref, reactive, onMounted, computed } = Vue;

function fmt(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:00`;
}

export const ExamsPage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>考试批次管理</h2>
        <div class="sub">按「年份 + 月份 + 考试类型」创建批次，状态流转：草稿 → 报名中 → 已截止 → 已归档</div>
      </div>
      <el-button type="primary" @click="openNew">新建考试批次</el-button>
    </div>

    <div class="card">
      <div class="filter-bar mb16">
        <el-select v-model="query.exam_type" placeholder="考试类型" clearable style="width:170px">
          <el-option v-for="t in types" :key="t.value" :label="t.label" :value="t.value" />
        </el-select>
        <el-select v-model="query.year" placeholder="年份" clearable style="width:120px">
          <el-option v-for="y in years" :key="y" :label="y + ' 年'" :value="y" />
        </el-select>
        <el-select v-model="query.month" placeholder="月份" clearable style="width:110px">
          <el-option v-for="m in 12" :key="m" :label="m + ' 月'" :value="m" />
        </el-select>
        <el-select v-model="query.status" placeholder="状态" clearable style="width:130px">
          <el-option v-for="s in statuses" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
        <el-input v-model="query.keyword" placeholder="批次名称关键字" clearable style="width:200px" />
        <el-button type="primary" @click="load">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>

      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="id" label="编号" width="70" />
        <el-table-column prop="name" label="考试批次名称" min-width="220" />
        <el-table-column label="类型" width="130">
          <template #default="{ row }">
            <span class="type-badge" :class="{ md: row.exam_type_base === 'mandarin' }">
              {{ row.exam_type_label }}</span>
          </template>
        </el-table-column>
        <el-table-column label="年/月" width="100">
          <template #default="{ row }">{{ row.exam_year }}年{{ row.exam_month }}月</template>
        </el-table-column>
        <el-table-column label="报名窗口" min-width="290">
          <template #default="{ row }">
            <span class="mono small">{{ row.signup_start_at }} ~ {{ row.signup_end_at }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="照片要求" width="150">
          <template #default="{ row }">
            <el-tag v-if="row.photo_requirement && row.photo_requirement.limited" size="small" type="warning">
              {{ row.photo_requirement.text }}
            </el-tag>
            <span v-else class="muted small">不限制</span>
          </template>
        </el-table-column>
        <el-table-column label="报名情况" width="180">
          <template #default="{ row }">
            <span>共 {{ row.stats.total }} 人</span>
            <div class="muted small">
              待审 {{ row.stats.pending }} / 通过 {{ row.stats.approved }} / 驳回 {{ row.stats.rejected }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="270" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="row.status === 'draft'" link type="success" size="small"
                       @click="changeStatus(row, 'open')">发布</el-button>
            <el-button v-if="row.status === 'open'" link type="warning" size="small"
                       @click="changeStatus(row, 'closed')">截止</el-button>
            <el-button v-if="row.status === 'closed'" link type="success" size="small"
                       @click="changeStatus(row, 'open')">重新开放</el-button>
            <el-button v-if="row.status !== 'archived'" link size="small"
                       @click="changeStatus(row, 'archived')">归档</el-button>
            <el-button v-if="row.status === 'draft'" link type="danger" size="small"
                       @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="right mt16">
        <el-pagination background layout="total, prev, pager, next" :total="total"
                       :page-size="query.page_size" :current-page="query.page"
                       @current-change="(p) => { query.page = p; load(); }" />
      </div>
    </div>

    <el-dialog v-model="dialog" :title="form.id ? '编辑考试批次' : '新建考试批次'" width="640px">
      <el-form :model="form" :rules="rules" ref="formRef" label-width="100px">
        <el-form-item label="考试类型" prop="exam_type">
          <el-radio-group v-model="form.exam_type" :disabled="!!form.id">
            <el-radio-button v-for="t in types" :key="t.value" :value="t.value">{{ t.label }}</el-radio-button>
          </el-radio-group>
          <div class="muted small lh-17">
            {{ baseHint }}
          </div>
        </el-form-item>
        <el-form-item label="年 / 月" prop="exam_year">
          <el-row :gutter="10" style="width:100%">
            <el-col :span="12">
              <el-input-number v-model="form.exam_year" :min="2020" :max="2100" style="width:100%" />
            </el-col>
            <el-col :span="12">
              <el-select v-model="form.exam_month" style="width:100%">
                <el-option v-for="m in 12" :key="m" :label="m + ' 月'" :value="m" />
              </el-select>
            </el-col>
          </el-row>
        </el-form-item>
        <el-form-item label="批次名称" prop="name">
          <el-input v-model="form.name" :placeholder="autoName" />
          <div class="muted small">留空则自动生成：{{ autoName }}</div>
        </el-form-item>
        <el-form-item label="报名时间" prop="signup_start_at">
          <el-date-picker v-model="range" type="datetimerange" style="width:100%"
                          range-separator="至" start-placeholder="报名开始" end-placeholder="报名截止"
                          value-format="YYYY-MM-DD HH:mm:ss" />
        </el-form-item>
        <el-form-item label="考试说明">
          <el-input v-model="form.description" type="textarea" :rows="3"
                    placeholder="考试说明 / 注意事项" />
        </el-form-item>
        <el-form-item label="照片要求">
          <el-row :gutter="10" style="width:100%">
            <el-col :span="12">
              <el-select v-model="form.photo_size" placeholder="尺寸：不限制" clearable style="width:100%">
                <el-option v-for="s in photoSpecs.sizes" :key="s.key" :label="s.label" :value="s.key" />
              </el-select>
            </el-col>
            <el-col :span="12">
              <el-select v-model="form.photo_color" placeholder="底色：不限制" clearable style="width:100%">
                <el-option v-for="c in photoSpecs.colors" :key="c.key" :label="c.label" :value="c.key" />
              </el-select>
            </el-col>
          </el-row>
          <div class="muted small lh-17">
            留空表示本场考试不限制；设置后考生证件照须符合要求才能提交报名（也可在「证件照制作」中直接生成）。
          </div>
        </el-form-item>
        <el-form-item label="保存状态">
          <el-radio-group v-model="form.status">
            <el-radio value="draft">存为草稿</el-radio>
            <el-radio value="open">立即发布（报名中）</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const route = VueRouter.useRoute();
    const router = VueRouter.useRouter();
    const list = ref([]);
    const total = ref(0);
    const loading = ref(false);
    const dialog = ref(false);
    const saving = ref(false);
    const formRef = ref(null);
    const range = ref([]);
    // 考试类型由管理员在「考试类型管理」中维护，这里动态拉取（含自定义类型）
    const types = ref([]);
    const typeLabel = (code) =>
      (types.value.find((t) => t.value === code) || {}).label || code || '考试';
    const statuses = [
      { value: 'draft', label: '草稿' }, { value: 'open', label: '报名中' },
      { value: 'closed', label: '已截止' }, { value: 'archived', label: '已归档' },
    ];
    const years = Array.from({ length: 5 }, (_, i) => new Date().getFullYear() - 2 + i);
    const query = reactive({ exam_type: '', year: '', month: '', status: '', keyword: '', page: 1, page_size: 10 });
    // 照片要求的尺寸 / 底色选项：取自证件照规格接口（唯一事实源），
    // 不在前端再抄一份枚举，避免两边口径不一致
    const photoSpecs = ref({ sizes: [], colors: [] });
    const form = reactive({
      id: null, exam_type: 'computer', exam_year: new Date().getFullYear(),
      exam_month: new Date().getMonth() + 1, name: '', description: '', status: 'draft',
      signup_start_at: '', signup_end_at: '',
      photo_size: '', photo_color: '',   // 空串 = 本场不限制照片要求
    });
    const rules = {
      exam_type: [{ required: true, message: '请选择考试类型', trigger: 'change' }],
      exam_year: [{ required: true, message: '请选择年份', trigger: 'change' }],
      name: [{ max: 100, message: '名称不超过 100 字', trigger: 'blur' }],
    };
    const autoName = computed(
      () => `${form.exam_year}年${form.exam_month}月${typeLabel(form.exam_type)}`);
    /* 类型可自定义，但字段集与校验规则由「基础模板」决定，这里提示当前类型落在哪套模板上 */
    const BASE_LABEL = { computer: '计算机类考试', mandarin: '普通话水平测试' };
    const BASE_HINT = {
      computer: '计算机类模板：13 个报名字段（考试机构 / 报考科目字典）',
      mandarin: '普通话模板：20 个报名字段（职业字典 + 省市区三级联动）',
    };
    const baseHint = computed(() => {
      const t = types.value.find((x) => x.value === form.exam_type);
      const b = (t && t.base_type) || 'computer';
      const tail = t && b !== t.value ? `（自定义类型，基于「${BASE_LABEL[b]}」模板，字段可在「考试类型管理」中增删）` : '';
      return BASE_HINT[b] + tail;
    });

    async function loadTypes() {
      try {
        const d = await api.get('/api/exam-types/options');
        types.value = d.list || [];
        // 当前选中类型若已停用/被删除，回落到首个可用类型，避免提交后后端报「类型不存在」
        if (types.value.length && !types.value.some((t) => t.value === form.exam_type)) {
          form.exam_type = types.value[0].value;
        }
      } catch (e) { /* 类型下拉加载失败不阻塞批次列表 */ }
    }

    async function loadPhotoSpecs() {
      try {
        // ⚠ api.get 返回的已是解包后的 data，不能再取一层 .data
        const d = (await api.get('/api/idphoto/specs')) || {};
        photoSpecs.value = { sizes: d.sizes || [], colors: d.colors || [] };
      } catch (e) { /* 规格加载失败不阻塞批次表单，仅下拉为空 */ }
    }

    async function load() {
      loading.value = true;
      try {
        const qs = new URLSearchParams();
        Object.keys(query).forEach((k) => { if (query[k] !== '' && query[k] !== null) qs.append(k, query[k]); });
        const data = await api.get('/api/exams?' + qs.toString());
        list.value = data.list;
        total.value = data.total;
      } catch (e) { handleErr(e); } finally { loading.value = false; }
    }

    function reset() {
      Object.assign(query, { exam_type: '', year: '', month: '', status: '', keyword: '', page: 1 });
      load();
    }

    function openNew() {
      Object.assign(form, {
        id: null, exam_type: (types.value[0] || {}).value || 'computer', exam_year: new Date().getFullYear(),
        exam_month: new Date().getMonth() + 1,         name: '', description: '', status: 'draft',
        signup_start_at: '', signup_end_at: '', photo_size: '', photo_color: '',
      });
      const now = new Date();
      const end = new Date(now.getTime() + 20 * 86400000);
      range.value = [fmt(now), fmt(end)];
      dialog.value = true;
    }

    function openEdit(row) {
      Object.assign(form, {
        id: row.id, exam_type: row.exam_type, exam_year: row.exam_year, exam_month: row.exam_month,
        name: row.name, description: row.description, status: row.status,
        signup_start_at: row.signup_start_at, signup_end_at: row.signup_end_at,
        // 老批次没有这两个字段时回落到空串（不限制）
        photo_size: row.photo_size || '', photo_color: row.photo_color || '',
      });
      range.value = [row.signup_start_at, row.signup_end_at];
      dialog.value = true;
    }

    async function save() {
      try { await formRef.value.validate(); } catch (e) { return; }
      if (!range.value || range.value.length !== 2) { msg.err('请选择报名开始与截止时间'); return; }
      form.signup_start_at = range.value[0];
      form.signup_end_at = range.value[1];
      saving.value = true;
      try {
        if (form.id) {
          await api.put('/api/exams/' + form.id, {
            name: form.name, description: form.description, status: form.status,
            exam_year: form.exam_year, exam_month: form.exam_month,
            signup_start_at: form.signup_start_at, signup_end_at: form.signup_end_at,
            photo_size: form.photo_size || '', photo_color: form.photo_color || '',
          });
        } else {
          await api.post('/api/exams', form);
        }
        msg.ok('保存成功');
        dialog.value = false;
        load();
      } catch (e) { handleErr(e); } finally { saving.value = false; }
    }

    async function changeStatus(row, status) {
      const label = { open: '发布（报名中）', closed: '截止', archived: '归档' }[status];
      try {
        await ElementPlus.ElMessageBox.confirm(
          `确认将批次「${row.name}」变更为「${label}」状态？`, '状态变更', { type: 'warning' });
      } catch (e) { return; }
      try {
        await api.put('/api/exams/' + row.id, { status });
        msg.ok('状态已更新');
        load();
      } catch (e) { handleErr(e); }
    }

    async function remove(row) {
      try {
        await ElementPlus.ElMessageBox.confirm(`确认删除草稿批次「${row.name}」？`, '删除确认', { type: 'warning' });
      } catch (e) { return; }
      try {
        await api.del('/api/exams/' + row.id);
        msg.ok('已删除');
        load();
      } catch (e) { handleErr(e); }
    }

    function statusTag(s) {
      return { draft: 'info', open: 'success', closed: 'warning', archived: 'danger' }[s] || '';
    }

    onMounted(async () => {
      await loadTypes();  // 先拿到类型，再由深链打开新建弹窗，否则默认类型可能落后
      await loadPhotoSpecs();  // 照片要求下拉（尺寸 / 底色）
      load();
      // 深链 /exams/new：直接打开新建批次弹窗
      if (route.path === '/exams/new') openNew();
    });
    // 由深链打开的弹窗关闭后回到列表地址，避免刷新又弹出
    Vue.watch(dialog, (v) => {
      if (!v && route.path === '/exams/new') router.replace('/exams');
    });
    return {
      list, total, loading, query, types, statuses, years, load, reset, dialog, form, rules,
      formRef, saving, openNew, openEdit, save, changeStatus, remove, statusTag, range, autoName,
      baseHint, photoSpecs,
    };
  },
};
