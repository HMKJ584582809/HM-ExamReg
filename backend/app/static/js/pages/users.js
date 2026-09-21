/* 模块1（补充）：用户与权限管理
   管理员开通 / 维护 考生、班主任、辅导员、审核员、管理员账号，并核验权限组与数据范围。 */
import { api, handleErr, msg } from '../api.js';
import { roleTagType, store } from '../store.js';

const { ref, reactive, computed, onMounted, onBeforeUnmount } = Vue;

export const UsersPage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>用户与权限管理</h2>
        <div class="sub">开通各类账号并绑定管理范围；数据范围分为 本班级 → 本年级 → 本院系 → 全校
          四档，角色决定默认档位与上限，也可对单个账号单独收窄（不得放宽）。
          考生可在注册页自助注册。</div>
      </div>
      <div>
        <el-button @click="verifyDrawer = true">权限组核验</el-button>
        <el-button @click="photoDlg = true">批量导入证件照</el-button>
        <!-- 找回密码走「图形验证码 + 身份证号」核验不过时产生的申请，在这里审核 -->
        <el-button @click="openResets">
          密码重置申请
          <el-badge v-if="resetPending > 0" :value="resetPending" class="ml8" />
        </el-button>
        <!-- 实名认证：审核集中在 /realname 页，这里给入口 + 待审数字 -->
        <el-button @click="goRealName">
          实名审核
          <el-badge v-if="rnPending > 0" :value="rnPending" class="ml8" />
        </el-button>
        <el-button type="primary" @click="openNew">开通账号</el-button>
      </div>
    </div>

    <div class="card">
      <el-tabs v-model="tab" @tab-change="onTabChange" class="role-tabs">
        <el-tab-pane v-for="t in tabs" :key="t.key" :name="t.key">
          <template #label>
            <span>{{ t.label }}<span class="tab-num">{{ countOf(t.key) }}</span></span>
          </template>
        </el-tab-pane>
      </el-tabs>
      <div class="muted small mb8">
        当前显示：<b>{{ currentTabLabel }}</b> ·
        共 {{ total }} 个账号（启用 {{ enabledInTab }} / 停用 {{ total - enabledInTab }}）
      </div>

      <div class="filter-bar mb16">
        <el-select v-model="query.status" placeholder="状态（全部）" clearable style="width:130px">
          <el-option label="已启用" :value="1" />
          <el-option label="已禁用" :value="0" />
        </el-select>
        <el-input v-model="query.keyword" placeholder="用户名 / 姓名 / 手机号 / 院系 / 班级"
                  clearable style="width:270px" @keyup.enter="doSearch" />
        <el-button type="primary" @click="doSearch">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>

      <!-- 内容超出时在表格内滚动（表头吸顶），页面本身不再整体拉长 -->
      <el-table :data="list" v-loading="loading" border stripe :max-height="tableMax">
        <el-table-column prop="id" label="ID" width="64" />
        <el-table-column prop="username" label="用户名" width="120" />
        <el-table-column prop="real_name" label="姓名" width="105" />
        <el-table-column label="角色" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="tagOf(row.role)">{{ row.role_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="数据范围" min-width="180">
          <template #default="{ row }">
            <span v-if="row.scope === 'scope_all'" class="muted">全部数据</span>
            <span v-else-if="row.scope === 'none'" class="muted">仅本人</span>
            <span v-else>{{ row.scope_label }}</span>
            <el-tag v-if="row.custom_scope" size="small" type="warning" style="margin-left:6px">单独设定</el-tag>
            <el-tooltip v-if="row.scope_issues && row.scope_issues.length" :content="row.scope_issues.join('；')">
              <el-tag size="small" type="danger" style="margin-left:6px">待完善</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="phone" label="手机号" width="125" />
        <el-table-column label="实名" width="96">
          <template #default="{ row }">
            <el-tag size="small" :type="rnTagType(row.realname_status)">{{ rnLabel(row.realname_status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="权限组" min-width="200">
          <template #default="{ row }">
            <el-tag v-if="row.custom_perms" size="small" type="warning" class="perm-chip">单独授权</el-tag>
            <el-tag v-for="p in row.perm_labels" :key="p" size="small" class="perm-chip"
                    :type="p.startsWith('数据范围') ? 'info' : 'primary'">{{ p }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="报名 / 审核" width="105">
          <template #default="{ row }"><span>{{ row.app_count }} / {{ row.review_count }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-switch v-model="row.__on" :loading="row.__saving" @change="(v) => toggle(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="warning" size="small" @click="resetPwd(row)">重置密码</el-button>
            <el-button link size="small" @click="viewApps(row)">报名记录</el-button>
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

    <el-dialog v-model="dialog" :title="form.id ? '编辑账号' : '开通账号'" width="600px">
      <el-form :model="form" :rules="rules" ref="formRef" label-width="100px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" :disabled="!!form.id" placeholder="3-20 位字母/数字/下划线" />
        </el-form-item>
        <el-form-item label="手机号" prop="phone">
          <el-input v-model="form.phone" maxlength="11" />
        </el-form-item>
        <el-form-item v-if="!form.id" label="初始密码" prop="password">
          <el-input v-model="form.password" placeholder="留空则自动生成（创建后展示）" show-password />
        </el-form-item>
        <!-- 姓名/邮箱等非必要项收进「补充信息」，建号时只看用户名+手机号+密码 -->
        <el-collapse v-model="moreOpen" class="mb16">
          <el-collapse-item name="more" title="补充信息（可选）">
            <el-form-item label="真实姓名" prop="real_name">
              <el-input v-model="form.real_name" maxlength="50" placeholder="留空则默认与用户名相同" />
            </el-form-item>
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="form.email" />
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
        <el-form-item label="角色" prop="role">
          <el-radio-group v-model="form.role" @change="onRoleChange">
            <el-radio v-for="r in roleOptions" :key="r.key" :value="r.key">{{ r.label }}</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-alert type="info" :closable="false" show-icon class="mb16"
                  :title="'该角色权限组：' + (currentRole.perm_labels || []).join('、')" />

        <el-divider content-position="left">数据范围（可见哪些数据）</el-divider>
        <el-alert v-if="scopeCustom" type="warning" :closable="false" show-icon class="mb16"
                  title="该账号已单独设定数据范围；选「跟随角色」可恢复为角色默认值。" />
        <el-radio-group v-model="form.scope" class="mb8" @change="scopeDirty = true">
          <el-radio value="">跟随角色（{{ currentRole.scope_label || '—' }}）</el-radio>
          <el-radio v-for="s in allowedScopes" :key="s.key" :value="s.key">{{ s.label }}</el-radio>
        </el-radio-group>
        <div class="muted" style="margin-bottom:6px">
          {{ scopeHint }}
        </div>

        <template v-if="needScopeFields">
          <el-divider content-position="left">范围依据字段</el-divider>
          <el-form-item label="年级">
            <el-input v-model="form.grade" placeholder="如 2022级（用于「本年级」范围）" />
          </el-form-item>
          <el-form-item label="院系" :required="currentRole.scoped">
            <el-select v-model="form.college" filterable allow-create default-first-option
                       placeholder="选择或输入二级学院（用于「本院系」范围）" style="width:100%">
              <el-option v-for="c in dicts.colleges" :key="c" :label="c" :value="c" />
            </el-select>
            <div class="muted small" v-if="!dicts.colleges.length">
              字典「二级学院」还没维护，可先直接输入；维护后这里会出现下拉候选。
            </div>
          </el-form-item>
          <el-form-item label="部门">
            <el-select v-model="form.department" filterable allow-create clearable
                       default-first-option placeholder="选择或输入所属部门（可选）" style="width:100%">
              <el-option v-for="d in deptOptions" :key="d.value" :label="d.label" :value="d.value" />
            </el-select>
            <div class="muted small" v-if="form.college">
              已按「{{ form.college }}」筛选：列出该学院的部门与校级部门；
              也仍可直接输入其它部门。
            </div>
          </el-form-item>
          <el-form-item v-if="form.role === 'head_teacher'" label="管理班级" :required="true">
            <el-input v-model="form.classes" type="textarea" :rows="2"
                      placeholder="可填写多个班级，用逗号分隔，如：计算机2101,软件工程2202（决定本班数据范围）" />
            <div class="muted" v-if="form.classes">已配置 {{ form.classes.split(/[,，]/).filter(Boolean).length }} 个班级</div>
          </el-form-item>
          <el-form-item v-else-if="form.scope === 'scope_class'" label="管理班级">
            <el-input v-model="form.classes" type="textarea" :rows="2"
                      placeholder="逗号分隔，如：计算机2101,软件工程2202" />
          </el-form-item>
        </template>

        <el-divider content-position="left">功能权限（可逐个开关）</el-divider>
        <el-alert v-if="permCustom" type="warning" :closable="false" show-icon class="mb16"
                  title="该账号已被单独授权，当前开关与角色默认值不同；点「恢复角色默认权限」可改为跟随角色自动调整。" />
        <div style="display:flex;flex-wrap:wrap;gap:10px 18px;margin-bottom:8px">
          <div v-for="g in permGroups" :key="g.key"
               style="display:flex;align-items:center;gap:8px;min-width:170px">
            <span style="min-width:124px">
              {{ g.label }}
              <span v-if="g.role_default === false" class="muted small">（角色默认关）</span>
            </span>
            <el-switch v-model="g.on" size="small" @change="permDirty = true" />
          </div>
        </div>
        <div class="muted small mb16" style="line-height:1.9">
          <div v-for="g in permGroups" :key="'d' + g.key">
            <b>{{ g.label }}</b>：{{ g.desc }}
          </div>
        </div>
        <div class="muted" style="margin-bottom:6px">
          未单独设置时沿用角色默认权限；关闭某项后该账号将无法使用对应功能（数据范围仍随角色判定）。
        </div>
        <el-button link type="primary" size="small" style="margin-bottom:12px"
                   @click="resetPermsToRole">恢复角色默认权限</el-button>

        <el-form-item v-if="form.id" label="账号状态">
          <el-radio-group v-model="form.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">禁用</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resetDlg" title="密码重置申请" width="860px">
      <el-alert type="info" :closable="false" show-icon class="mb16"
                title="这些申请来自「图形验证码 + 身份证号」核验未通过的找回密码请求。"
                description="核对本人身份后点「通过并重置」，系统会生成临时密码并只在这里显示一次，请线下告知本人并提醒其尽快修改。" />
      <el-radio-group v-model="resetStatus" size="small" class="mb16" @change="loadResets">
        <el-radio-button value="pending">待审核</el-radio-button>
        <el-radio-button value="approved">已通过</el-radio-button>
        <el-radio-button value="rejected">已驳回</el-radio-button>
      </el-radio-group>
      <el-table :data="resetList" v-loading="resetLoading" size="small" max-height="420">
        <el-table-column prop="username" label="账号" width="120" />
        <el-table-column prop="real_name" label="姓名" width="100" />
        <el-table-column prop="id_number_masked" label="提交证件号" width="160" />
        <el-table-column prop="contact" label="联系方式" width="130" />
        <el-table-column prop="created_at" label="提交时间" width="160" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="resetTag(row.status)">{{ resetText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <template v-if="row.status === 'pending'">
              <el-button link type="success" @click="approveReset(row)">通过并重置</el-button>
              <el-button link type="danger" @click="rejectReset(row)">驳回</el-button>
            </template>
            <span v-else class="muted small">{{ row.admin_note || '—' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="photoDlg" title="批量导入证件照" width="780px">
      <el-alert type="info" :closable="false" show-icon class="mb16"
                title="按文件名匹配账号"
                description="文件名（不含扩展名）依次按 证件号码 → 用户名/学号 → 手机号 → 姓名 匹配；姓名重名的不会自动覆盖，请改名后重传。" />
      <!-- 需求1：批量导入时也给出统一规格与底色，管理员不必事先在外面处理好 -->
      <div class="idp-line">
        <span class="idp-lab">尺寸</span>
        <el-radio-group v-model="photoSize" size="small">
          <el-radio-button value="original">保持原图</el-radio-button>
          <el-radio-button value="one_inch">一寸 295×413</el-radio-button>
          <el-radio-button value="two_inch">二寸 413×579</el-radio-button>
        </el-radio-group>
      </div>
      <div class="idp-line mb16">
        <span class="idp-lab">底色</span>
        <el-radio-group v-model="photoColor" size="small">
          <el-radio-button value="keep">不换底</el-radio-button>
          <el-radio-button value="white">白底</el-radio-button>
          <el-radio-button value="blue">蓝底</el-radio-button>
          <el-radio-button value="red">红底</el-radio-button>
        </el-radio-group>
      </div>
      <input type="file" accept="image/jpeg,image/png" multiple style="display:none"
             ref="photoFiles" @change="onPickPhotos" />
      <el-button type="primary" :loading="photoBusy" @click="pickPhotos">选择照片（可多选）</el-button>
      <span class="muted small ml8" v-if="photoResult">已选 {{ photoResult.total }} 个文件</span>

      <div v-if="photoResult" class="mt16">
        <div class="mb8">
          将覆盖 <b>{{ photoResult.counts.matched }}</b> 张 ·
          重名跳过 <b>{{ photoResult.counts.ambiguous }}</b> ·
          未匹配 <b>{{ photoResult.counts.unmatched }}</b> ·
          重复 <b>{{ photoResult.counts.duplicate }}</b> ·
          无效 <b>{{ photoResult.counts.invalid }}</b>
        </div>
        <el-table :data="photoResult.items" size="small" border max-height="330">
          <el-table-column prop="filename" label="文件名" min-width="170" show-overflow-tooltip />
          <el-table-column label="状态" width="94">
            <template #default="{ row }">
              <el-tag size="small" :type="photoTag(row.status)">{{ photoText(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="by_label" label="匹配方式" width="104" />
          <el-table-column label="匹配到" min-width="150">
            <template #default="{ row }">
              <span v-if="row.real_name">{{ row.real_name }}（{{ row.username }}）</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="note" label="说明" min-width="140" show-overflow-tooltip />
        </el-table>
        <div v-if="pendingNames.length" class="mt8">
          <div class="muted small">未采用（修正文件名后可重新上传）：</div>
          <div class="muted small">{{ pendingNames.join('、') }}</div>
        </div>
      </div>
      <template #footer>
        <el-button @click="photoDlg = false">关闭</el-button>
        <el-button type="primary" :loading="photoBusy"
                   :disabled="!photoResult || !photoResult.counts.matched"
                   @click="confirmPhotoImport">
          确认覆盖 {{ photoResult ? photoResult.counts.matched : 0 }} 张
        </el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="verifyDrawer" title="权限组核验" size="900px">
      <el-alert type="info" :closable="false" show-icon class="mb16"
                title="逐账号核对「角色 → 权限组 → 数据范围」是否自洽；标记「待完善」的账号将看不到任何数据，请补全管理范围。" />
      <div class="filter-bar mb16">
        <el-switch v-model="onlyIssues" active-text="只看异常账号" @change="loadVerify" />
        <el-tag type="danger" v-if="verifyData.issue_count">异常 {{ verifyData.issue_count }} / 共 {{ verifyData.checked }}</el-tag>
        <el-tag type="success" v-else>全部账号权限自洽（共 {{ verifyData.checked }}）</el-tag>
      </div>
      <el-table :data="verifyData.list" size="small" border v-loading="verifyLoading" max-height="620">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="username" label="用户名" width="110" />
        <el-table-column prop="real_name" label="姓名" width="100" />
        <el-table-column prop="role_label" label="角色" width="90" />
        <el-table-column label="生效权限组" min-width="220">
          <template #default="{ row }">
            <el-tag v-for="p in row.perm_labels" :key="p" size="small" class="perm-chip"
                    :type="p.startsWith('数据范围') ? 'info' : 'primary'">{{ p }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="scope_label" label="数据范围" width="170" />
        <el-table-column label="核验结果" min-width="200">
          <template #default="{ row }">
            <el-tag v-if="!row.issues.length" type="success" size="small">通过</el-tag>
            <div v-else>
              <el-tag v-for="i in row.issues" :key="i" type="danger" size="small" class="perm-chip">{{ i }}</el-tag>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <el-drawer v-model="appsDrawer" :title="'报名记录 · ' + (current.username || '')" size="720px">
      <el-table :data="apps" size="small" border v-loading="appsLoading">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="exam_name" label="考试批次" min-width="190" show-overflow-tooltip />
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <span class="type-badge" :class="{ md: row.app_type === 'mandarin' }">
              {{ row.exam_type_label || (row.app_type === 'mandarin' ? '普通话水平测试' : '计算机类考试') }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="姓名" width="90" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="tagType(row.audit_status)">{{ row.audit_status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="提交时间" width="160" />
      </el-table>
    </el-drawer>
  </div>`,
  setup() {
    const list = ref([]);
    const total = ref(0);
    const loading = ref(false);
    const dialog = ref(false);
    const saving = ref(false);
    const formRef = ref(null);
    const stats = ref({});
    const apps = ref([]);
    const appsDrawer = ref(false);
    const appsLoading = ref(false);
    const current = ref({});
    const roleOptions = ref([]);
    const verifyDrawer = ref(false);
    const verifyLoading = ref(false);
    const verifyData = ref({ list: [], checked: 0, issue_count: 0 });
    const onlyIssues = ref(false);
    const enabledCount = ref(0);
    const query = reactive({ role: '', status: '', keyword: '', page: 1, page_size: 10 });
    // 角色标签页：点哪个页签就只看哪类账号（空串 = 全部）
    const TABS = [
      { key: 'admin', label: '管理员' }, { key: 'reviewer', label: '审核员' },
      { key: 'head_teacher', label: '班主任' }, { key: 'college_reviewer', label: '二级学院' },
      { key: 'candidate', label: '学生' },
    ];
    const tab = ref('');
    const tabs = [{ key: '', label: '全部' }, ...TABS];
    const countOf = (k) => (k ? ((stats.value[k] || {}).total || 0)
      : (stats.value._total || 0));
    const currentTabLabel = computed(() =>
      (tabs.find((t) => t.key === tab.value) || {}).label || '全部');
    // 表格最大高度：随窗口高度走，超出即在表格内滚动（表头吸顶、分页始终可见）
    const winH = ref(window.innerHeight || 768);
    const tableMax = computed(() => Math.max(240, winH.value - 250));
    const moreOpen = ref([]);   // 「补充信息」折叠面板，默认收起

    // ------------------------------------------------ 密码重置申请（找回密码人工审核）
    const resetDlg = ref(false);
    const resetList = ref([]);
    const resetLoading = ref(false);
    const resetStatus = ref('pending');
    const resetPending = ref(0);

    async function loadResets() {
      resetLoading.value = true;
      try {
        const r = await api.get('/api/users/password-resets?status=' + resetStatus.value +
                                '&page_size=100');
        resetList.value = (r && r.list) || [];
        resetPending.value = ((r && r.counts) || {}).pending || 0;
      } catch (e) { handleErr(e); } finally { resetLoading.value = false; }
    }

    async function openResets() {
      resetDlg.value = true;
      await loadResets();
    }

    function resetTag(s) {
      return { pending: 'warning', approved: 'success', rejected: 'danger' }[s] || 'info';
    }
    function resetText(s) {
      return { pending: '待审核', approved: '已通过', rejected: '已驳回' }[s] || s;
    }

    // ------------------------------------------------ 实名认证（入口 + 列表状态列）
    const rnPending = ref(0);
    const RN_TEXT = { pending: '待审核', approved: '已通过', rejected: '已驳回' };

    function rnLabel(s) {
      return RN_TEXT[s] || (s ? s : '未认证');
    }
    function rnTagType(s) {
      return { pending: 'warning', approved: 'success', rejected: 'danger' }[s] || 'info';
    }
    function goRealName() {
      window.location.hash = '#/realname';
    }
    async function loadRnPending() {
      try {
        const r = await api.get('/api/realname/list?status=pending&page_size=1');
        rnPending.value = (r && r.total) || 0;
      } catch (e) { /* 无权限或未启用实名时不显示角标，不影响主流程 */ }
    }

    async function approveReset(row) {
      try {
        const r = await ElMessageBox.confirm(
          '通过后系统将为「' + row.username + '」生成临时密码并立即生效，确定继续？',
          '审核通过', { type: 'warning' });
        if (!r) return;
      } catch (e) { return; }
      try {
        const d = await api.post(`/api/users/password-resets/${row.id}/approve`);
        ElMessageBox.alert(
          '账号：' + (d && d.username) + '<br/>临时密码：<b>' + (d && d.new_password) +
          '</b><br/><br/>请线下告知本人，并提醒其登录后尽快修改密码。',
          '重置成功', { dangerouslyUseHTMLString: true });
        await loadResets();
      } catch (e) { handleErr(e); }
    }

    async function rejectReset(row) {
      try {
        const r = await ElMessageBox.prompt('填写驳回原因（可选）', '驳回申请',
          { inputPlaceholder: '如：身份信息不一致', inputType: 'textarea' });
        await api.post(`/api/users/password-resets/${row.id}/reject`,
                       { note: (r && r.value) || '' });
        msg.ok('已驳回');
        await loadResets();
      } catch (e) {
        if (e && e.message) handleErr(e);   // 取消输入时不应报错
      }
    }
    const onWinResize = () => { winH.value = window.innerHeight || 768; };
    const enabledInTab = computed(() => (tab.value
      ? ((stats.value[tab.value] || {}).enabled || 0) : enabledCount.value));
    function onTabChange(k) {
      tab.value = k || '';
      query.role = tab.value;
      query.page = 1;
      load();
    }
    const form = reactive({
      id: null, username: '', real_name: '', phone: '', email: '', scope: '',
      role: 'reviewer', password: '', status: 1, grade: '', college: '', department: '',
      class_name: '', classes: '',
    });
    const currentRole = computed(() =>
      roleOptions.value.find((r) => r.key === form.role)
      || { perm_labels: [], scoped: false, scope_label: '', allowed_scopes: [] });
    // 功能权限开关（可单独授权；未改动时跟随角色默认）
    const permGroups = ref([]);
    const permDirty = ref(false);
    const permCustom = ref(false);
    // 数据范围（可逐账号收窄，但不得超过角色上限；空串=跟随角色）
    const allowedScopes = computed(() => currentRole.value.allowed_scopes || []);
    const scopeCustom = computed(() => !!form.scope);
    const scopeDirty = ref(false);
    // 需要填写年级/院系/班级等「范围依据字段」的场景：
    // 范围角色必填；或范围被单独收窄到本年级 / 本班级时也要填
    const needScopeFields = computed(() =>
      !!currentRole.value.scoped
      || form.scope === 'scope_grade' || form.scope === 'scope_class' || form.scope === 'scope_college');
    // 部门候选：按所选学院过滤。层级是平铺单层的 —— 部门只是「可归属到某个学院」，
    // college 为空的是校级处室（教务处之类），任何学院都能选，所以永远保留。
    const deptOptions = computed(() => {
      const items = (store.dicts.departmentItems || []);
      const pool = items.length
        ? items
        : (store.dicts.departments || []).map((d) => ({ name: d, college: '' }));
      const c = (form.college || '').trim();
      const hit = c ? pool.filter((d) => !d.college || d.college === c) : pool;
      return hit.map((d) => ({
        value: d.name,
        label: d.college ? `${d.name}（${d.college}）` : d.name,
      }));
    });
    const scopeHint = computed(() => {
      const s = form.scope || currentRole.value.scope;
      if (s === 'scope_all') return '可见全校全部数据。';
      if (s === 'scope_college') return '只能看本院系（依据「院系」字段）的数据。';
      if (s === 'scope_grade') return '只能看本年级的数据（报名表无年级字段，按同年级班级匹配，批量导入且未建账号的考生会漏掉）。';
      if (s === 'scope_class') return '只能看所带班级的数据（依据「管理班级」字段）。';
      return '只能看本人的数据。';
    });

    const rules = {
      username: [
        { required: true, message: '请输入用户名', trigger: 'blur' },
        { pattern: /^[A-Za-z0-9_]{3,20}$/, message: '3-20 位字母、数字或下划线', trigger: 'blur' },
      ],
      phone: [
        { required: true, message: '请输入手机号', trigger: 'blur' },
        { pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: 'blur' },
      ],
      email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
      role: [{ required: true, message: '请选择角色', trigger: 'change' }],
      password: [{ min: 8, message: '密码不少于 8 位', trigger: 'blur' }],
    };

    function tagOf(r) { return roleTagType[r] || 'primary'; }
    function tagType(s) {
      return { pending: 'warning', approved: 'success', rejected: 'danger', returned: 'info' }[s] || '';
    }

    function onRoleChange() {
      if (!currentRole.value.scoped) {
        form.grade = ''; form.college = ''; form.class_name = ''; form.classes = '';
      } else if (form.role !== 'head_teacher') {
        form.class_name = ''; form.classes = '';
      }
      // 角色变了，原来的单独范围可能已超出新角色上限 —— 直接回到「跟随角色」
      form.scope = '';
      scopeDirty.value = false;
      // 未手动改过开关时，跟随新角色重置为默认值
      if (!permDirty.value) syncPermsWithRole();
    }

    // 用角色默认权限重置开关
    function syncPermsWithRole() {
      const r = roleOptions.value.find((x) => x.key === form.role);
      const def = (r && r.perms) || [];
      permGroups.value.forEach((g) => { g.on = def.includes(g.key); });
      permCustom.value = false;
    }

    function resetPermsToRole() {
      syncPermsWithRole();
      permDirty.value = false;
    }

    async function loadPermGroups(row) {
      try {
        const d = await api.get(`/api/users/permission-groups`);
        // groups 里含 scope_*，这里只保留可开关的功能项（desc 用于逐项说明）
        const all = d.groups.filter((g) => g.kind === 'action');
        if (row && row.id) {
          const p = await api.get(`/api/users/${row.id}/perms`);
          const owned = p.perms || [];
          permGroups.value = (p.groups || all).map((g) => ({
            key: g.key, label: g.label, desc: g.desc || '',
            on: owned.includes(g.key), role_default: g.role_default,
          }));
          permCustom.value = !!p.custom_perms;
          // 用「单独设定的原始值」回显，空串即跟随角色
          form.scope = p.stored_scope || '';
        } else {
          const r = roleOptions.value.find((x) => x.key === form.role);
          const def = (r && r.perms) || [];
          permGroups.value = all.map((g) => ({
            key: g.key, label: g.label, desc: g.desc || '', on: def.includes(g.key),
            role_default: def.includes(g.key) }));
          permCustom.value = false;
          form.scope = '';
        }
      } catch (e) { /* 忽略：权限组加载失败不阻塞编辑 */ }
      permDirty.value = false;
      scopeDirty.value = false;
    }

    // 保存功能权限开关 + 数据范围；与角色默认一致时后端会自动清除单独配置
    async function savePerms(userId) {
      if (!userId) return;
      const picked = permGroups.value.filter((g) => g.on).map((g) => g.key);
      try {
        await api.put(`/api/users/${userId}/perms`, { perms: picked, scope: form.scope || '' });
      } catch (e) { handleErr(e); }
    }

    async function loadRoleOptions() {
      try { roleOptions.value = await api.get('/api/users/options'); } catch (e) { /* ignore */ }
    }

    async function load() {
      loading.value = true;
      try {
        const qs = new URLSearchParams();
        Object.keys(query).forEach((k) => { if (query[k] !== '' && query[k] !== null) qs.append(k, query[k]); });
        const data = await api.get('/api/users?' + qs.toString());
        list.value = data.list.map((r) => ({ ...r, __on: r.status === 1, __saving: false }));
        total.value = data.total;
      } catch (e) { handleErr(e); } finally { loading.value = false; }
    }

    async function loadStats() {
      try {
        const s = await api.get('/api/users/stats');
        stats.value = s;
        enabledCount.value = Object.keys(s).filter((k) => k !== '_total')
          .reduce((a, k) => a + ((s[k] || {}).enabled || 0), 0);
      } catch (e) { /* ignore */ }
    }

    async function loadVerify() {
      verifyLoading.value = true;
      try {
        verifyData.value = await api.get('/api/users/verify?only_issues=' + (onlyIssues.value ? 1 : 0));
      } catch (e) { handleErr(e); } finally { verifyLoading.value = false; }
    }

    function doSearch() { query.page = 1; load(); }

    function reset() {
      Object.assign(query, { role: '', status: '', keyword: '', page: 1 });
      tab.value = '';                 // 重置时回到「全部」页签，避免筛选与页签不一致
      load();
    }

    function openNew() {
      Object.assign(form, {
        id: null, username: '', real_name: '', phone: '', email: '', scope: '',
        role: 'reviewer', password: '', status: 1, grade: '', college: '', department: '',
      class_name: '', classes: '',
      });
      loadPermGroups(null);
      dialog.value = true;
    }

    function openEdit(row) {
      Object.assign(form, {
        id: row.id, username: row.username, real_name: row.real_name, phone: row.phone,
        email: row.email, role: row.role, password: '', status: row.status, scope: '',
        grade: row.grade || '', college: row.college || '',
        department: row.department || '',
        class_name: row.class_name || '', classes: row.classes || '',
      });
      loadPermGroups(row);
      dialog.value = true;
    }

    // 范围依据字段：非范围角色也可留着（可能用于逐账号收窄），后端会按角色决定
    // 它们是否参与过滤，不会因此越权
    function scopePayload() {
      return {
        grade: form.grade,
        college: form.college,
        class_name: form.role === 'head_teacher' ? form.class_name : '',
        classes: (form.role === 'head_teacher' || form.scope === 'scope_class')
          ? form.classes : '',
      };
    }

    async function save() {
      try { await formRef.value.validate(); } catch (e) { return; }
      if (currentRole.value.scoped && !form.college) { msg.warn('该角色必须填写所属院系'); return; }
      if (form.role === 'head_teacher' && !form.classes) { msg.warn('请填写至少一个所带班级'); return; }
      if (form.scope === 'scope_grade' && !form.grade) { msg.warn('数据范围为「本年级」时请填写年级'); return; }
      if (form.scope === 'scope_class' && !form.classes) { msg.warn('数据范围为「本班级」时请填写班级'); return; }
      saving.value = true;
      try {
        if (form.id) {
          await api.put('/api/users/' + form.id, {
            real_name: form.real_name, phone: form.phone, email: form.email,
            role: form.role, status: form.status, ...scopePayload(),
          });
          await savePerms(form.id);
          msg.ok('账号已更新');
        } else {
          const r = await api.post('/api/users', {
            username: form.username, real_name: form.real_name, phone: form.phone,
            email: form.email, role: form.role, password: form.password, ...scopePayload(),
          });
          await savePerms(r.id);
          ElementPlus.ElMessageBox.alert(
            `账号：${r.username}\n角色：${r.role_label}\n数据范围：${r.scope_label}\n`
            + `初始密码：${r.initial_password}\n\n`
            + '请及时将账号与初始密码告知本人，并提醒其登录后修改密码。',
            '账号开通成功', { confirmButtonText: '我已记录', type: 'success' });
        }
        dialog.value = false;
        load(); loadStats();
      } catch (e) { handleErr(e); } finally { saving.value = false; }
    }

    async function toggle(row, v) {
      const prev = row.status === 1;
      row.__saving = true;
      try {
        await api.put('/api/users/' + row.id, { status: v ? 1 : 0 });
        row.status = v ? 1 : 0;
        msg.ok(v ? '账号已启用' : '账号已禁用');
        loadStats();
      } catch (e) {
        row.__on = prev;
        handleErr(e);
      } finally { row.__saving = false; }
    }

    async function resetPwd(row) {
      let pwd = '';
      try {
        const r = await ElementPlus.ElMessageBox.prompt(
          `为「${row.real_name || row.username}」设置新密码（留空则自动生成）`,
          '重置密码', { inputPlaceholder: '不少于 8 位，含两类以上字符', inputType: 'password' });
        pwd = r.value || '';
      } catch (e) { return; }
      try {
        const r = await api.post(`/api/users/${row.id}/reset-password`, { new_password: pwd });
        ElementPlus.ElMessageBox.alert(`新密码：${r.new_password}\n请及时告知本人。`,
          '密码已重置', { confirmButtonText: '我已记录', type: 'success' });
      } catch (e) { handleErr(e); }
    }

    async function viewApps(row) {
      current.value = row;
      appsDrawer.value = true;
      appsLoading.value = true;
      try {
        const d = await api.get(`/api/users/${row.id}/applications?page_size=50`);
        apps.value = d.list.map((x) => ({
          ...x,
          audit_status_label: { pending: '待审核', approved: '已通过', rejected: '已驳回',
            returned: '已退回' }[x.audit_status] || x.audit_status,
        }));
      } catch (e) { handleErr(e); } finally { appsLoading.value = false; }
    }

    /* 批量导入证件照：先预览匹配结果，确认后才覆盖，避免重名误配 */
    const photoDlg = ref(false);
    const photoSize = ref('original');     // 批量导入时的统一规格（需求1）
    const photoColor = ref('keep');
    const photoFiles = ref(null);
    const photoResult = ref(null);
    const photoBusy = ref(false);

    const pendingNames = computed(() => {
      const items = (photoResult.value && photoResult.value.items) || [];
      return items.filter((i) => i.status !== 'matched').map((i) => i.filename);
    });

    function photoTag(s) {
      return { matched: 'success', ambiguous: 'warning', unmatched: 'info',
        duplicate: 'warning', invalid: 'danger' }[s] || 'info';
    }

    function photoText(s) {
      return { matched: '将覆盖', ambiguous: '重名跳过', unmatched: '未匹配',
        duplicate: '重复', invalid: '无效' }[s] || s;
    }

    function pickPhotos() { if (photoFiles.value) photoFiles.value.click(); }

    async function onPickPhotos(e) {
      const fs = Array.from(e.target.files || []);
      e.target.value = '';
      if (!fs.length) return;
      if (fs.some((f) => !/^image\/(jpeg|png)$/.test(f.type))) {
        msg.err('仅支持 JPG / PNG 图片');
        return;
      }
      if (fs.some((f) => f.size > 5 * 1024 * 1024)) {
        msg.err('有图片超过 5 MB');
        return;
      }
      photoResult.value = null;
      photoBusy.value = true;
      try {
        const fd = new FormData();
        fs.forEach((f) => fd.append('files', f));
        fd.append('size', photoSize.value);
        fd.append('color', photoColor.value);
        const { data } = await api.upload('/api/photos/import/preview', fd);
        photoResult.value = data;
      } catch (err) { handleErr(err); } finally { photoBusy.value = false; }
    }

    async function confirmPhotoImport() {
      if (!photoResult.value) return;
      photoBusy.value = true;
      try {
        await api.post('/api/photos/import', { token: photoResult.value.token });
        msg.ok('证件照已覆盖导入');
        photoResult.value = null;
        photoDlg.value = false;
        load();
      } catch (e) { handleErr(e); } finally { photoBusy.value = false; }
    }

    onMounted(async () => {
      await loadRoleOptions(); load(); loadStats();
      loadResets();   // 先把待审数量取回来，好让入口按钮显示角标
      loadRnPending();
      window.addEventListener('resize', onWinResize);
    });

    onBeforeUnmount(() => window.removeEventListener('resize', onWinResize));

    return {
      list, total, loading, query, load, reset, doSearch, dialog, form, rules, formRef, saving,
      openNew, openEdit, save, toggle, resetPwd, viewApps, stats, enabledCount,
      apps, appsDrawer, appsLoading, current, tagType, tagOf,
      roleOptions, currentRole, onRoleChange,
      tab, tabs, onTabChange, countOf, currentTabLabel, enabledInTab, tableMax, dicts: store.dicts,
      moreOpen,
      resetDlg, resetList, resetLoading, resetStatus, resetPending,
      openResets, loadResets, resetTag, resetText, approveReset, rejectReset,
      permGroups, permDirty, permCustom, resetPermsToRole,
      allowedScopes, scopeCustom, scopeDirty, scopeHint, needScopeFields, deptOptions,
      verifyDrawer, verifyLoading, verifyData, onlyIssues, loadVerify,
      photoDlg, photoFiles, photoResult, photoBusy, pendingNames, pickPhotos, onPickPhotos,
      photoSize, photoColor,
      confirmPhotoImport, photoTag, photoText,
      rnPending, rnLabel, rnTagType, goRealName,
    };
  },
};
