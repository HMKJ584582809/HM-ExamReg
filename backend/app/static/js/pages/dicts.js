/* 字典维护（管理员）：考试机构 / 报考科目 / 从事职业 的自助增删改 */
import { api, handleErr, msg } from '../api.js';
import { loadDicts } from '../store.js';

const { ref, reactive, computed, onMounted, onUnmounted } = Vue;

// key 必须与后端 EDITABLE 的键一致（后端也接受中文名，但前端统一用英文键）
const KINDS = [
  { key: 'org', label: '考试机构', hint: '计算机类考试的「考试机构」下拉与机构编码校验' },
  { key: 'subject', label: '报考科目', hint: '计算机类考试的「报考科目」下拉与导入时的模糊匹配' },
  { key: 'occupation', label: '从事职业', hint: '普通话水平测试的「从事职业」下拉与导入匹配' },
  { key: 'college', label: '二级学院', hint: '报名「院系」与账号「所属院系」的下拉候选（三套模板的院系列都取这里）' },
  // 部门可「归属到某个学院」（平铺单层，不做树）：学工办（辅导员）/ 专职教师
  // 这类院内岗位挂在具体学院下，校级处室（教务处等）留空表示不属于任何学院
  { key: 'department', label: '部门', hasCollege: true,
    hint: '账号「所属部门」的下拉候选。可指定归属哪个二级学院（院内岗位如学工办、专职教师）；留空表示校级部门，不属于任何学院' },
];

export const DictsPage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>字典维护</h2>
        <div class="sub">维护报名表单的下拉选项；改动立即生效，无需重启。省市区与证件类型来自官方模板，保持只读。</div>
      </div>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-alert class="mb16" type="info" :closable="false" show-icon
              title="字典是报名校验、批量导入与表单下拉的共同数据源。被引用的条目不能删除（可改名为「xxx（停用）」）；改名不会改写历史报名记录，导出仍以原值为准。" />

    <div class="card">
      <el-tabs v-model="kind" @tab-change="onKindChange">
        <el-tab-pane v-for="k in kinds" :key="k.key" :label="k.label" :name="k.key" />
      </el-tabs>

      <div class="hint mb8">{{ currentHint }}</div>

      <div class="filter-bar mb16">
        <el-input v-model="keyword" placeholder="按名称搜索" clearable style="width:220px"
                  @keyup.enter="load" @clear="load" />
        <el-button type="primary" @click="openNew">新增</el-button>
      </div>

      <el-table :data="list" v-loading="loading" size="small" border>
        <el-table-column v-if="isOrg" prop="code" label="编码" width="140" />
        <el-table-column prop="name" :label="isOrg ? '机构名称' : '名称'" min-width="220" />
        <el-table-column v-if="isDept" label="所属学院" min-width="180">
          <template #default="{ row }">
            <span v-if="row.college">{{ row.college }}</span>
            <span v-else class="muted">校级（不限学院）</span>
          </template>
        </el-table-column>
        <el-table-column label="被引用" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.used ? 'warning' : 'info'">
              {{ row.used ? row.used + ' 条' : '未使用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty><span class="muted small">暂无数据</span></template>
      </el-table>

      <div class="center mt16">
        <el-pagination v-model:current-page="page" :page-size="pageSize" :total="total"
                       layout="prev, pager, next, total" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="dialog" :title="(editing ? '编辑' : '新增') + currentLabel" width="440px">
      <el-form :model="form" label-width="90px">
        <el-form-item v-if="isOrg" label="机构编码" required>
          <el-input v-model="form.code" :disabled="!!editing" placeholder="如 001" />
        </el-form-item>
        <el-form-item :label="isOrg ? '机构名称' : '名称'" required>
          <el-input v-model="form.name" maxlength="60" show-word-limit
                    placeholder="不超过 60 个字符" />
        </el-form-item>
        <el-form-item v-if="isDept" label="所属学院">
          <el-select v-model="form.college" clearable filterable allow-create
                     default-first-option placeholder="留空 = 校级部门（不属于任何学院）"
                     style="width:100%">
            <el-option v-for="c in colleges" :key="c" :label="c" :value="c" />
          </el-select>
          <div class="hint">院内岗位（学工办、专职教师）请选所属学院；校级处室留空</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>`,
  setup() {
    const kinds = KINDS;
    const kind = ref('org');
    const list = ref([]);
    const total = ref(0);
    const page = ref(1);
    const pageSize = 20;
    const keyword = ref('');
    const loading = ref(false);
    const dialog = ref(false);
    const saving = ref(false);
    const editing = ref(null);
    const form = reactive({ code: '', name: '', college: '' });
    const colleges = ref([]);

    const isOrg = computed(() => kind.value === 'org');
    // 只有部门有「所属学院」这一列
    const isDept = computed(
      () => !!(kinds.find((k) => k.key === kind.value) || {}).hasCollege);
    const currentLabel = computed(
      () => (kinds.find((k) => k.key === kind.value) || {}).label || '');
    const currentHint = computed(
      () => (kinds.find((k) => k.key === kind.value) || {}).hint || '');

    async function load() {
      loading.value = true;
      try {
        // api.get 不带参数序列化，这里手工拼查询串
        const q = `?keyword=${encodeURIComponent(keyword.value)}`
          + `&page=${page.value}&page_size=${pageSize}`;
        const d = await api.get(`/api/dicts/manage/${kind.value}${q}`);
        list.value = d.list || [];
        total.value = d.total || 0;
      } catch (e) { handleErr(e); } finally { loading.value = false; }
    }

    async function loadColleges() {
      try {
        // 学院下拉直接读字典接口（api.get 已解包，返回的就是名称数组）
        colleges.value = (await api.get('/api/dicts/college')) || [];
      } catch (e) { colleges.value = []; }
    }

    function onKindChange() {
      page.value = 1;
      keyword.value = '';
      if (isDept.value && !colleges.value.length) loadColleges();
      load();
    }

    function openNew() {
      editing.value = null;
      form.code = '';
      form.name = '';
      form.college = '';
      dialog.value = true;
    }

    function openEdit(row) {
      editing.value = row;
      form.code = row.code || '';
      form.name = row.name || '';
      form.college = row.college || '';
      dialog.value = true;
    }

    async function save() {
      if (!form.name.trim()) { msg.warn('请填写名称'); return; }
      if (isOrg.value && !form.code.trim()) { msg.warn('请填写机构编码'); return; }
      saving.value = true;
      try {
        const body = isOrg.value ? { code: form.code.trim(), name: form.name.trim() }
                                 : { name: form.name.trim() };
        // 部门额外带「所属学院」（留空 = 校级部门）
        if (isDept.value) body.college = (form.college || '').trim();
        if (editing.value) {
          // 接口只回 data（message 被 request 丢弃），影响面提示在这里自行组装
          const r = await api.put(`/api/dicts/manage/${kind.value}/${editing.value.id}`, body);
          const r2 = r || {};
          if (r2.used) {
            msg.warn(`已保存为「${r2.name}」；已有 ${r2.used} 条报名记录仍保留旧值`
              + `「${r2.old_name}」，导出时以原值为准`);
          } else {
            msg.ok('已保存');
          }
        } else {
          await api.post(`/api/dicts/manage/${kind.value}`, body);
          msg.ok(`已新增${currentLabel.value}`);
        }
        dialog.value = false;
        await load();
        // 下拉选项可能已变化，强制刷新全局字典缓存
        await loadDicts(true);
      } catch (e) { handleErr(e); } finally { saving.value = false; }
    }

    async function remove(row) {
      try {
        await ElementPlus.ElMessageBox.confirm(
          `确定删除「${row.name}」？`, '删除确认',
          { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' });
      } catch (e) { return; }
      try {
        const r = await api.del(`/api/dicts/manage/${kind.value}/${row.id}`);
        msg.ok(r && r.message ? r.message : '已删除');
        await load();
        await loadDicts(true);
      } catch (e) { handleErr(e); }
    }

    onMounted(async () => { await loadColleges(); await load(); });

    return { kinds, kind, list, total, page, pageSize, keyword, loading, dialog, saving,
             editing, form, isOrg, isDept, colleges, currentLabel, currentHint,
             load, loadColleges, onKindChange, openNew, openEdit, save, remove };
  },
};
