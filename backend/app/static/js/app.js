/* 应用入口：路由、布局、鉴权守卫（按权限组渲染，后端二次核验） */
import { api, session } from './api.js';
import { canAny, loadDicts, logout, roleLabel, roleTagType, store } from './store.js';
import { LoginPage, RegisterPage } from './pages/auth.js';
import { UsersPage } from './pages/users.js';
import { HomePage } from './pages/home.js';
import { ExamsPage } from './pages/exams.js';
import { ExamTypesPage } from './pages/examtypes.js';
import { DictsPage } from './pages/dicts.js';
import { ApplyPage } from './pages/apply.js';
import { MinePage } from './pages/mine.js';
import { ReviewPage } from './pages/review.js';
import { ImportPage } from './pages/import.js';
import { ExportPage } from './pages/export.js';
import { AnalysisPage } from './pages/analysis.js';
import { DashboardPage } from './pages/dashboard.js';
import { SystemPage } from './pages/system.js';
import { ProfilePage } from './pages/profile.js';
import { AiPage } from './pages/ai.js';
import { DocsPage } from './pages/docs.js';
import { IdPhotoPage } from './pages/idphoto.js';
import { RealNamePage } from './pages/realname.js';

const { createApp, ref, computed, onMounted } = Vue;
const { createRouter, createWebHashHistory } = VueRouter;

const routes = [
  { path: '/login', component: LoginPage, meta: { public: true, title: '登录' } },
  { path: '/register', component: RegisterPage, meta: { public: true, title: '注册' } },
  { path: '/', component: HomePage, meta: { title: '开放报名批次' } },
  { path: '/exams/new', component: ExamsPage, meta: { title: '新建考试批次', perm: ['exam_manage'] } },
  { path: '/exams', component: ExamsPage, meta: { title: '考试批次管理', perm: ['exam_manage'] } },
  { path: '/exam-types', component: ExamTypesPage, meta: { title: '考试类型管理', perm: ['dict_manage'] } },
  { path: '/dicts', component: DictsPage, meta: { title: '字典维护', perm: ['dict_manage'] } },
  { path: '/users', component: UsersPage,
    meta: { title: '用户与权限管理', perm: ['user_manage'], adminOnly: true } },
  { path: '/apply/:examId', component: ApplyPage, meta: { title: '考试报名', perm: ['apply'] } },
  { path: '/my-applications', component: MinePage, meta: { title: '我的报名', perm: ['apply'] } },
  { path: '/import', component: ImportPage, meta: { title: '批量导入报名数据', perm: ['import'] } },
  { path: '/review/:id', component: ReviewPage, meta: { title: '报名信息审核', perm: ['audit', 'import'] } },
  { path: '/review', component: ReviewPage, meta: { title: '报名信息审核', perm: ['audit', 'import'] } },
  { path: '/export', component: ExportPage, meta: { title: '汇总导出', perm: ['export'] } },
  { path: '/analysis', component: AnalysisPage, meta: { title: '数据分析', perm: ['analysis'] } },
  { path: '/dashboard', component: DashboardPage, meta: { title: '数据看板', perm: ['dashboard'], fullscreen: true } },
  { path: '/system', component: SystemPage, meta: { title: '系统维护', perm: ['system_manage'] } },
  { path: '/idphoto', component: IdPhotoPage, meta: { title: '证件照制作', perm: ['idphoto'] } },
  { path: '/realname', component: RealNamePage, meta: { title: '实名认证', perm: ['realname', 'realname_audit'] } },
  { path: '/ai', component: AiPage, meta: { title: 'AI 智能助手', perm: ['ai'] } },
  { path: '/docs/:key', component: DocsPage, meta: { title: '内置文档' } },
  { path: '/docs', redirect: '/docs/usage' },
  { path: '/profile', component: ProfilePage, meta: { title: '个人中心' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
];

const router = createRouter({ history: createWebHashHistory(), routes });

const MENUS = [
  { path: '/', icon: 'HomeFilled', label: '首页 · 开放批次' },
  { path: '/exams', icon: 'Files', label: '考试批次管理', perm: ['exam_manage'] },
  { path: '/exam-types', icon: 'Collection', label: '考试类型管理', perm: ['dict_manage'] },
  { path: '/dicts', icon: 'Menu', label: '字典维护', perm: ['dict_manage'] },
  { path: '/users', icon: 'UserFilled', label: '用户与权限管理', perm: ['user_manage'],
    adminOnly: true },
  { path: '/my-applications', icon: 'Tickets', label: '我的报名', perm: ['apply'] },
  { path: '/import', icon: 'Upload', label: '批量导入', perm: ['import'] },
  { path: '/review', icon: 'DocumentChecked', label: '报名信息审核', perm: ['audit', 'import'] },
  { path: '/export', icon: 'Download', label: '汇总导出', perm: ['export'] },
  { path: '/analysis', icon: 'DataAnalysis', label: '数据分析', perm: ['analysis'] },
  { path: '/dashboard', icon: 'Monitor', label: '数据看板大屏', perm: ['dashboard'] },
  { path: '/system', icon: 'SetUp', label: '系统维护', perm: ['system_manage'] },
  { path: '/idphoto', icon: 'Camera', label: '证件照制作', perm: ['idphoto'] },
  { path: '/realname', icon: 'Postcard', label: '实名认证', perm: ['realname', 'realname_audit'] },
  { path: '/ai', icon: 'ChatDotRound', label: 'AI 智能助手', perm: ['ai'] },
  { path: '/docs/usage', icon: 'QuestionFilled', label: '使用说明' },
  { path: '/docs/dev', icon: 'Notebook', label: '开发文档', perm: ['system_manage'] },
  { path: '/profile', icon: 'User', label: '个人中心' },
];

function allowed(meta) {
  // 管理员专属页面（用户管理）：不看权限组，只认角色。
  // 单独授权 user_manage 只能分配功能，不能让人进来改别人的账号（后端同样按角色拦）。
  if (meta && meta.adminOnly && (store.user || {}).role !== 'admin') return false;
  if (!meta || !meta.perm) return true;
  return canAny(...meta.perm);
}

/* 管理员可能在后台调整过本账号的权限（单独授权），而本地会话缓存的是登录时的快照。
   后端每个请求都读库、改动是即时的，前端若只认快照，菜单/按钮会停留在旧权限，
   必须退出重登才更新。这里按 token 缓存地拉一次最新权限即可对齐。 */
const meCache = { token: '', promise: null };
function refreshMe() {
  const tk = session.token;
  if (!tk) return Promise.resolve(null);
  if (meCache.token !== tk) {
    meCache.token = tk;
    meCache.promise = api.get('/api/auth/me').then((u) => {
      if (u && u.id) {
        store.user = u;
        session.setUser(u);
      }
      return u;
    }).catch(() => null);
  }
  return meCache.promise;
}

router.beforeEach(async (to) => {
  const logged = !!session.token;
  if (to.meta.public) {
    return logged ? '/' : true;
  }
  if (!logged) return { path: '/login' };
  if (!store.user) store.user = session.user;
  await refreshMe();
  if (!allowed(to.meta)) {
    ElementPlus.ElMessage.error('无权访问该页面');
    return '/';
  }
  if (!store.loaded) {
    try { await loadDicts(); } catch (e) { /* 由页面自行提示 */ }
  }
  document.title = (to.meta.title ? to.meta.title + ' · ' : '') + '考试报名信息采集与审核管理系统';
  return true;
});

const App = {
  template: `
  <div v-if="isPublic" class="public-wrap"><router-view /></div>
  <el-container v-else class="layout">
    <el-aside :width="store.asideCollapsed ? '64px' : '216px'" class="layout-aside">
      <div class="brand">
        <div class="logo">考</div>
        <div class="brand-text" v-show="!store.asideCollapsed">考试报名审核系统</div>
      </div>
      <el-menu :default-active="activeMenu" :collapse="store.asideCollapsed"
               :collapse-transition="false" router>
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          <el-icon><component :is="m.icon" /></el-icon>
          <template #title>{{ m.label }}</template>
        </el-menu-item>
      </el-menu>
      <div class="aside-foot" v-show="!store.asideCollapsed">
        <div class="scope-line" v-if="scopeLabel">数据范围：{{ scopeLabel }}</div>
        v1.1.0 · 本地运行<br>数据存储于 data/app.db
      </div>
    </el-aside>

    <el-container>
      <el-header class="layout-header">
        <div class="header-title">
          <el-button link @click="store.asideCollapsed = !store.asideCollapsed"
                     :icon="store.asideCollapsed ? 'Expand' : 'Fold'" />
          <span>{{ title }}</span>
        </div>
        <div class="header-right">
          <el-tag size="small" v-if="scopeLabel" type="info" class="scope-tag">{{ scopeLabel }}</el-tag>
          <el-tag size="small" class="role-tag" :type="tagType">
            {{ roleLabel[role] || '访客' }}
          </el-tag>
          <el-dropdown @command="onCommand">
            <span class="user-chip">
              <el-avatar :size="30" style="background:#1f6feb">
                {{ (store.user && (store.user.real_name || store.user.username) || 'U').slice(0, 1) }}
              </el-avatar>
              <span>{{ store.user ? (store.user.real_name || store.user.username) : '' }}</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">个人中心</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="layout-main">
        <router-view v-slot="{ Component }">
          <!-- key 绑定完整路径：确保切换批次 / 报名记录时页面组件重新创建 -->
          <component :is="Component" :key="route.fullPath" />
        </router-view>
      </el-main>
    </el-container>
  </el-container>`,
  setup() {
    const route = VueRouter.useRoute();
    const r = VueRouter.useRouter();
    const isPublic = computed(() => route.path === '/login' || route.path === '/register');
    const role = computed(() => (store.user && store.user.role) || '');
    const tagType = computed(() => roleTagType[role.value] || 'primary');
    const menus = computed(() => MENUS.filter(
      (m) => (!m.adminOnly || (store.user || {}).role === 'admin')
        && (!m.perm || canAny(...m.perm))));
    const scopeLabel = computed(() => {
      const s = store.user && store.user.scope;
      if (!s || s === 'scope_all' || s === 'none') return '';
      return (store.user.scope_label || '').replace('数据范围：', '');
    });
    const activeMenu = computed(() => {
      if (route.path.startsWith('/apply')) return '/';
      if (route.path.startsWith('/exams')) return '/exams';
      if (route.path.startsWith('/review')) return '/review';
      return route.path;
    });
    const title = computed(() => route.meta.title || '考试报名信息采集与审核管理系统');

    function onCommand(cmd) {
      if (cmd === 'profile') r.push('/profile');
      if (cmd === 'logout') {
        logout();
        ElementPlus.ElMessage.success('已退出登录');
        r.push('/login');
      }
    }

    onMounted(() => {
      if (session.token && !store.loaded) loadDicts().catch(() => {});
    });

    return { store, route, isPublic, role, roleLabel, tagType, menus, activeMenu, title,
             scopeLabel, onCommand };
  },
};

const app = createApp(App);
app.use(router);
app.use(ElementPlus, { locale: window.ElementPlusLocaleZhCN || undefined });
if (window.ElementPlusIconsVue) {
  Object.keys(window.ElementPlusIconsVue).forEach((key) => {
    app.component(key, window.ElementPlusIconsVue[key]);
  });
}
app.config.errorHandler = (err) => {
  console.error(err);
  if (window.ElementPlus) ElementPlus.ElMessage.error('页面出现异常：' + (err.message || err));
};
app.mount('#app');
