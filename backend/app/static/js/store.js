/* 全局状态：当前用户、字典缓存、省市区三级索引 */
import { api, session } from './api.js';

const { reactive, computed } = Vue;

export const store = reactive({
  user: session.user,
  dicts: {
    orgs: [], subjects: [], occupations: [], idTypes: [],
    ethnicities: [], educations: [], colleges: [], departments: [], meta: null,
    // 部门带「所属学院」（平铺单层归属），账号编辑页按学院过滤时用
    departmentItems: [],
  },
  regions: [],       // [{province, cities:[{name, counties:[]}]}]
  loaded: false,
  loading: false,
  asideCollapsed: false,
});

export const roleLabel = {
  candidate: '考生',
  head_teacher: '班主任',
  college_reviewer: '二级学院审核',
  reviewer: '审核员',
  admin: '管理员',
};

export const roleTagType = {
  admin: 'danger',
  reviewer: 'warning',
  college_reviewer: 'info',
  head_teacher: 'success',
  candidate: 'primary',
};

/* 权限组判断：后端在 store.user.perms 下发，前端仅用于渲染，真正校验仍在后端 */
export function can(perm) {
  const u = store.user;
  return !!(u && Array.isArray(u.perms) && u.perms.includes(perm));
}

export function canAny(...perms) {
  return perms.some((p) => can(p));
}

export const myPerms = computed(() => (store.user && store.user.perms) || []);
export const myScope = computed(() => (store.user && store.user.scope) || 'none');
export const myScopeLabel = computed(() => (store.user && store.user.scope_label) || '');
export const isManageLevel = computed(() => can('import'));

export const isAdmin = computed(() => can('user_manage'));
export const isReviewer = computed(() => can('audit'));
export const isCandidate = computed(() => (store.user && store.user.role) === 'candidate');

/* 省市区索引：province -> cities；province|city -> counties */
export const regionIndex = {
  cities(province) {
    const p = store.regions.find((x) => x.province === province);
    return p ? p.cities.map((c) => c.name) : [];
  },
  counties(province, city) {
    const p = store.regions.find((x) => x.province === province);
    if (!p) return [];
    const c = p.cities.find((x) => x.name === city);
    return c ? c.counties : [];
  },
  hasSub(province) {
    return this.cities(province).length > 0;
  },
};

export async function loadDicts(force = false) {
  if (store.loaded && !force) return;
  store.loading = true;
  try {
    const [orgs, subjects, occupations, idTypes, ethnicities, educations, regions, meta,
      colleges, departments, departmentItems] =
      await Promise.all([
        api.get('/api/dicts/org'),
        api.get('/api/dicts/subject'),
        api.get('/api/dicts/occupation'),
        api.get('/api/dicts/id-type'),
        api.get('/api/dicts/ethnicity'),
        api.get('/api/dicts/education'),
        api.get('/api/dicts/region/tree'),
        api.get('/api/dicts/meta'),
        api.get('/api/dicts/college'),
        api.get('/api/dicts/department'),
        api.get('/api/dicts/department/full'),
      ]);
    store.dicts.orgs = orgs;
    store.dicts.subjects = subjects;
    store.dicts.occupations = occupations;
    store.dicts.idTypes = idTypes;
    store.dicts.ethnicities = ethnicities;
    store.dicts.educations = educations;
    store.dicts.meta = meta;
    store.dicts.colleges = colleges || [];
    store.dicts.departments = departments || [];
    store.dicts.departmentItems = departmentItems || [];
    store.regions = regions;
    store.loaded = true;
  } finally {
    store.loading = false;
  }
}

export function setUser(u) {
  store.user = u;
  session.setUser(u);
}

export function logout() {
  session.clear();
  store.user = null;
}
