/* 考试类型管理（管理员）：基于基础模板自定义考试类型
 * 通用模板（generic）额外支持「自定义字段」——字段由管理员自由增删 */
import { api, handleErr, msg } from '../api.js';

const { ref, reactive, onMounted, computed } = Vue;

const BASE_TEMPLATES = [
  { value: 'computer', label: '计算机类考试（13 个采集字段）' },
  { value: 'mandarin', label: '普通话水平测试（20 个采集字段）' },
  { value: 'generic', label: '通用考试报名（8 个核心字段 + 自定义字段）' },
];

export const ExamTypesPage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>考试类型管理</h2>
        <div class="sub">内置「计算机类考试」「普通话水平测试」「通用考试报名」三套模板；新增类型选一个模板作为底座，即可复用报名、导入、审核、导出全部链路</div>
      </div>
      <el-button type="primary" @click="openNew">新建考试类型</el-button>
    </div>

    <el-alert class="mb16" type="info" :closable="false" show-icon
              title="「基础模板」决定该类型采集哪些字段、用哪张数据表、导出哪套官方模板，创建后不可更改。自定义类型可自由调整字段的启用、必填与显示名；选「通用考试报名」还能自由增删自定义字段。" />

    <div class="card">
      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="name" label="类型名称" min-width="160" />
        <el-table-column label="编码" width="130">
          <template #default="{ row }"><span class="mono">{{ row.code }}</span></template>
        </el-table-column>
        <el-table-column label="基础模板" width="180">
          <template #default="{ row }">
            <span class="type-badge" :class="{ md: row.base_type === 'mandarin', gn: row.base_type === 'generic' }">{{ row.base_type_label }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="说明" min-width="170" show-overflow-tooltip />
        <el-table-column label="采集字段" width="130">
          <template #default="{ row }">
            {{ enabledCount(row) }} / {{ row.fields.length }}
            <span class="hint" v-if="(row.custom_fields || []).length">+{{ row.custom_fields.length }} 自定义</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
              {{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link :type="row.enabled ? 'warning' : 'success'" @click="toggle(row)">
              {{ row.enabled ? '停用' : '启用' }}</el-button>
            <el-button link type="danger" :disabled="row.is_builtin" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="hint mt8">内置类型不可删除，停用后新建批次时不会再出现在类型下拉中。</div>
    </div>

    <el-dialog :title="form.id ? '编辑考试类型' : '新建考试类型'" v-model="dialog" width="780px">
      <el-form :model="form" :rules="rules" ref="formRef" label-width="100px">
        <el-form-item label="类型名称" prop="name">
          <el-input v-model="form.name" placeholder="如：英语四级考试" maxlength="30" />
        </el-form-item>
        <el-form-item label="编码" prop="code">
          <el-input v-model="form.code" :disabled="!!form.id" placeholder="小写字母开头，如 english" />
          <div class="hint" v-if="!form.id">系统内部标识，创建后不可修改</div>
        </el-form-item>
        <el-form-item label="基础模板" prop="base_type">
          <el-select v-model="form.base_type" :disabled="!!form.id" style="width:100%">
            <el-option v-for="t in bases" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="100" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort" :min="0" :max="999" />
          <span class="hint ml8">数字越小越靠前</span>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="采集字段">
          <el-table :data="form.fields" size="small" border max-height="300">
            <el-table-column label="显示名" min-width="150">
              <template #default="{ row }">
                <el-input v-model="row.label" size="small" :disabled="!row.enabled" />
              </template>
            </el-table-column>
            <el-table-column prop="key" label="字段标识" width="160">
              <template #default="{ row }"><span class="mono small">{{ row.key }}</span></template>
            </el-table-column>
            <el-table-column label="采集" width="78" align="center">
              <template #default="{ row }">
                <el-checkbox v-model="row.enabled" :disabled="row.system_required" />
              </template>
            </el-table-column>
            <el-table-column label="必填" width="78" align="center">
              <template #default="{ row }">
                <el-checkbox v-model="row.required" :disabled="row.system_required || !row.enabled" />
              </template>
            </el-table-column>
          </el-table>
          <div class="hint">系统必填字段不可关闭；关闭采集的字段不会出现在报名表、导入模板与导出文件中。</div>
        </el-form-item>

        <el-form-item label="自定义字段" v-if="form.base_type === 'generic'">
          <div v-if="!form.id" class="hint">保存该类型后即可添加自定义字段。</div>
          <template v-else>
            <div class="mb8">
              <el-button size="small" type="primary" @click="openField()">添加字段</el-button>
              <span class="hint ml8">最多 {{ fieldMax }} 个；自定义字段会同步出现在报名表、导入模板与导出文件中</span>
            </div>
            <el-table :data="customFields" size="small" border max-height="240" v-if="customFields.length">
              <el-table-column prop="label" label="字段名称" min-width="110" />
              <el-table-column label="类型" width="90">
                <template #default="{ row }">{{ (fieldTypes[row.type] || row.type) }}</template>
              </el-table-column>
              <el-table-column label="必填" width="72" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.required ? 'danger' : 'info'">{{ row.required ? '必填' : '选填' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="选项" min-width="140">
                <template #default="{ row }">
                  <span class="small muted" v-if="(row.options || []).length">{{ (row.options || []).join('、') }}</span>
                  <span class="small muted" v-else-if="row.type === 'select' || row.type === 'multiselect'">未配置</span>
                  <span class="small muted" v-else>不适用</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="116" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" @click="openField(row)">编辑</el-button>
                  <el-button link type="danger" @click="removeField(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div class="hint" v-else>尚未添加自定义字段，当前只有 8 个核心字段。</div>
          </template>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog :title="fieldForm.id ? '编辑自定义字段' : '添加自定义字段'"
               v-model="fieldDialog" width="560px" append-to-body>
      <el-form :model="fieldForm" :rules="fieldRules" ref="fieldFormRef" label-width="100px">
        <el-form-item label="字段名称" prop="label">
          <el-input v-model="fieldForm.label" maxlength="30" placeholder="如：报考等级" />
        </el-form-item>
        <el-form-item label="字段标识" prop="field_key">
          <el-input v-model="fieldForm.field_key" :disabled="!!fieldForm.id"
                    placeholder="小写字母开头，如 exam_level" />
          <div class="hint" v-if="!fieldForm.id">创建后不可修改；不能与固定字段（name / phone 等）重名</div>
        </el-form-item>
        <el-form-item label="类型" prop="field_type">
          <el-select v-model="fieldForm.field_type" style="width:100%">
            <el-option v-for="(lb, k) in fieldTypes" :key="k" :label="lb" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="选项" prop="optionsText"
                      v-if="fieldForm.field_type === 'select' || fieldForm.field_type === 'multiselect'">
          <el-input v-model="fieldForm.optionsText" type="textarea" :rows="3"
                    placeholder="每行一个选项" />
          <div class="hint">每行一个；多选在批量导入时用「、」分隔</div>
        </el-form-item>
        <el-form-item label="提示语">
          <el-input v-model="fieldForm.placeholder" maxlength="50" />
        </el-form-item>
        <el-form-item label="必填">
          <el-switch v-model="fieldForm.required" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="fieldForm.sort" :min="0" :max="999" />
          <span class="hint ml8">数字越小越靠前</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="fieldDialog = false">取消</el-button>
        <el-button type="primary" :loading="fieldSaving" @click="saveField">保存</el-button>
      </template>
    </el-dialog>
  </div>
  `,
  setup() {
    const list = ref([]);
    const loading = ref(false);
    const dialog = ref(false);
    const saving = ref(false);
    const formRef = ref(null);
    const bases = BASE_TEMPLATES;
    const form = reactive({
      id: null, code: '', name: '', base_type: 'computer', description: '',
      sort: 30, enabled: true, fields: [],
    });
    const rules = {
      name: [{ required: true, message: '请填写类型名称', trigger: 'blur' }],
      code: [
        { required: true, message: '请填写类型编码', trigger: 'blur' },
        { pattern: /^[a-z][a-z0-9_]{1,29}$/, message: '小写字母开头，仅含小写字母/数字/下划线，长度 2-30', trigger: 'blur' },
      ],
      base_type: [{ required: true, message: '请选择基础模板', trigger: 'change' }],
    };

    // 自定义字段（仅通用模板）
    const customFields = ref([]);
    const fieldDialog = ref(false);
    const fieldSaving = ref(false);
    const fieldFormRef = ref(null);
    const fieldTypes = ref({});
    const fieldMax = ref(30);
    const fieldForm = reactive({
      id: null, field_key: '', label: '', field_type: 'text', required: false,
      optionsText: '', placeholder: '', sort: 10,
    });
    const fieldRules = {
      label: [{ required: true, message: '请填写字段名称', trigger: 'blur' }],
      field_key: [
        { required: true, message: '请填写字段标识', trigger: 'blur' },
        { pattern: /^[a-z][a-z0-9_]{1,29}$/, message: '小写字母开头，仅含小写字母/数字/下划线，长度 2-30', trigger: 'blur' },
      ],
      field_type: [{ required: true, message: '请选择字段类型', trigger: 'change' }],
    };
    const needsOptions = computed(() => fieldForm.field_type === 'select'
      || fieldForm.field_type === 'multiselect');

    const enabledCount = (row) => (row.fields || []).filter((f) => f.enabled).length;

    /** 加载某类型的自定义字段定义（同时拿到类型字典与上限） */
    async function loadFields(tid) {
      if (!tid) { customFields.value = []; return; }
      try {
        const d = await api.get('/api/exam-types/' + tid + '/fields');
        customFields.value = d.list || [];
        if (d.types) fieldTypes.value = d.types;
        if (d.max) fieldMax.value = d.max;
      } catch (e) { handleErr(e); }
    }

    function openField(row) {
      Object.assign(fieldForm, {
        id: row ? row.id : null,
        field_key: row ? row.key : '',
        label: row ? row.label : '',
        field_type: row ? row.type : 'text',
        required: row ? !!row.required : false,
        optionsText: row ? (row.options || []).join('\n') : '',
        placeholder: row ? (row.placeholder || '') : '',
        sort: row ? row.sort : (customFields.value.length + 1) * 10,
      });
      fieldDialog.value = true;
    }

    async function saveField() {
      try { await fieldFormRef.value.validate(); } catch (e) { return; }
      const opts = fieldForm.optionsText.split('\n')
        .map((s) => s.trim()).filter(Boolean);
      if (needsOptions.value && !opts.length) {
        msg.err('请至少填写一个选项'); return;
      }
      const payload = {
        field_key: fieldForm.field_key.trim(), label: fieldForm.label.trim(),
        field_type: fieldForm.field_type, required: fieldForm.required,
        options: opts, placeholder: fieldForm.placeholder.trim(), sort: fieldForm.sort,
      };
      fieldSaving.value = true;
      try {
        if (fieldForm.id) {
          await api.put(`/api/exam-types/${form.id}/fields/${fieldForm.id}`, payload);
        } else {
          await api.post('/api/exam-types/' + form.id + '/fields', payload);
        }
        msg.ok('已保存');
        fieldDialog.value = false;
        await loadFields(form.id);
        await load();
      } catch (e) { handleErr(e); } finally { fieldSaving.value = false; }
    }

    async function removeField(row) {
      try {
        await ElementPlus.ElMessageBox.confirm(
          `确认删除自定义字段「${row.label}」？已有报名记录中该字段的值将不再显示。`,
          '删除确认', { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' });
      } catch (e) { return; }
      try {
        const r = await api.del(`/api/exam-types/${form.id}/fields/${row.id}`);
        msg.ok(r && r.message ? r.message : '已删除');
        await loadFields(form.id);
        await load();
      } catch (e) { handleErr(e); }
    }

    /** 取某基础模板的默认字段清单（用内置类型作为样板） */
    function templateFields(base) {
      const t = list.value.find((x) => x.base_type === base && x.is_builtin);
      return (t ? t.fields : []).map((f) => ({ ...f }));
    }

    async function load() {
      loading.value = true;
      try {
        const d = await api.get('/api/exam-types');
        list.value = d.list || [];
      } catch (e) { handleErr(e); } finally { loading.value = false; }
    }

    function openNew() {
      Object.assign(form, {
        id: null, code: '', name: '', base_type: 'computer', description: '',
        sort: (list.value.length + 1) * 10, enabled: true,
        fields: templateFields('computer'),
      });
      customFields.value = [];
      dialog.value = true;
    }

    function openEdit(row) {
      Object.assign(form, {
        id: row.id, code: row.code, name: row.name, base_type: row.base_type,
        description: row.description || '', sort: row.sort, enabled: row.enabled,
        fields: (row.fields || []).map((f) => ({ ...f })),
      });
      customFields.value = (row.custom_fields || []).map((f) => ({ ...f }));
      if (row.base_type === 'generic') loadFields(row.id);
      dialog.value = true;
    }

    // 新建时切换基础模板：整份字段清单跟随模板变化
    Vue.watch(() => form.base_type, (v, old) => {
      if (!form.id && v && v !== old) form.fields = templateFields(v);
    });

    function configPayload() {
      const cfg = {};
      form.fields.forEach((f) => {
        cfg[f.key] = {
          enabled: !!f.enabled,
          required: !!f.required,
          label: (f.label || '').trim(),
        };
      });
      return cfg;
    }

    async function save() {
      try { await formRef.value.validate(); } catch (e) { return; }
      const payload = {
        name: form.name.trim(), description: form.description.trim(),
        sort: form.sort, enabled: form.enabled, fields_config: configPayload(),
      };
      saving.value = true;
      try {
        if (form.id) {
          await api.put('/api/exam-types/' + form.id, payload);
        } else {
          await api.post('/api/exam-types',
            Object.assign({ code: form.code.trim(), base_type: form.base_type }, payload));
        }
        msg.ok('已保存');
        dialog.value = false;
        await load();
      } catch (e) { handleErr(e); } finally { saving.value = false; }
    }

    async function toggle(row) {
      try {
        await api.put('/api/exam-types/' + row.id, { enabled: !row.enabled });
        msg.ok(row.enabled ? '已停用' : '已启用');
        await load();
      } catch (e) { handleErr(e); }
    }

    async function remove(row) {
      if (row.is_builtin) return;
      try {
        await ElementPlus.ElMessageBox.confirm(
          `确认删除考试类型「${row.name}」？该操作不可恢复。`, '删除确认',
          { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' });
      } catch (e) { return; }
      try {
        await api.del('/api/exam-types/' + row.id);
        msg.ok('已删除');
        await load();
      } catch (e) { handleErr(e); }
    }

    onMounted(load);

    return {
      list, loading, dialog, saving, formRef, form, rules, bases,
      enabledCount, openNew, openEdit, save, toggle, remove,
      customFields, fieldDialog, fieldSaving, fieldFormRef, fieldForm, fieldRules,
      fieldTypes, fieldMax, needsOptions, openField, saveField, removeField,
    };
  },
};
