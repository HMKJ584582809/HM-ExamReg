/* 个人中心：资料维护 / 修改密码 */
import { api, handleErr, msg, session } from '../api.js';
import { roleLabel, roleTagType, setUser, store, logout } from '../store.js';

const { ref, reactive, computed, onMounted } = Vue;

export const ProfilePage = {
  template: `
  <div>
    <div class="page-head">
      <div><h2>个人中心</h2>
        <div class="sub">维护个人资料与登录密码</div></div>
    </div>

    <el-row :gutter="16">
      <el-col :span="14">
        <div class="card">
          <div class="card-title"><span class="t">基本信息</span></div>
          <el-form :model="form" :rules="rules" ref="formRef" label-width="100px">
            <el-form-item label="用户名">
              <el-input :model-value="user.username" disabled />
            </el-form-item>
            <el-form-item label="角色">
              <el-tag :type="tagType">{{ roleLabel[user.role] || user.role }}</el-tag>
            </el-form-item>
            <el-form-item label="权限组">
              <el-tag v-for="p in (user.perm_labels || [])" :key="p" size="small" class="perm-chip"
                      :type="p.startsWith('数据范围') ? 'info' : 'primary'">{{ p }}</el-tag>
            </el-form-item>
            <el-form-item label="数据范围">
              <span>{{ user.scope_label || '—' }}</span>
            </el-form-item>
            <el-form-item label="年级 / 院系" v-if="user.college || user.grade">
              <span>{{ [user.grade, user.college].filter(Boolean).join(' · ') }}</span>
            </el-form-item>
            <el-form-item label="所带班级" v-if="user.classes || user.class_name">
              <span>{{ (user.classes || user.class_name || '').split(/[,，]/).filter(Boolean).join('、') || '—' }}</span>
            </el-form-item>
            <el-form-item label="真实姓名" prop="real_name">
              <el-input v-model="form.real_name" maxlength="50" />
            </el-form-item>
            <el-form-item label="手机号" prop="phone">
              <el-input v-model="form.phone" maxlength="11" />
            </el-form-item>
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="form.email" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="saving" @click="save">保存资料</el-button>
            </el-form-item>
          </el-form>
        </div>
      </el-col>

      <el-col :span="10">
        <div class="card">
          <div class="card-title"><span class="t">证件照</span></div>
          <div class="photo-box">
            <img v-if="photoUrl" :src="photoUrl" class="photo-img" alt="证件照" />
            <div v-else class="photo-empty">未上传</div>
          </div>
          <!-- 个人中心只维护「一张」本人证件照；要换底、改尺寸请用「证件照制作」，
               在那里调好后再回来上传/替换，避免同一张照片在两处各有一套处理参数 -->
          <div class="mt8" style="text-align:center">
            <input type="file" accept="image/jpeg,image/png" style="display:none"
                   ref="fileInput" @change="onFileChange" />
            <el-button size="small" type="primary" :loading="uploading"
                       @click="pickFile">{{ photoUrl ? '更换照片' : '上传照片' }}</el-button>
            <el-button size="small" type="danger" plain v-if="photoUrl"
                       :loading="uploading" @click="removePhoto">删除</el-button>
          </div>
          <div class="muted small mt8">
            支持 JPG / PNG，不超过 5 MB。用于报名与审核时的身份核对。<br />
            需要换底色、改尺寸或透明底，请先到左侧菜单「证件照制作」处理（可一键「调用我的照片」
            把这里的照片拷过去编辑），完成后再回来上传替换。
          </div>
        </div>

        <div class="card">
          <div class="card-title"><span class="t">修改密码</span></div>
          <el-form :model="pwd" :rules="pwdRules" ref="pwdRef" label-width="100px">
            <el-form-item label="原密码" prop="old_password">
              <el-input v-model="pwd.old_password" type="password" show-password />
            </el-form-item>
            <el-form-item label="新密码" prop="new_password">
              <el-input v-model="pwd.new_password" type="password" show-password
                        placeholder="不少于 8 位，含两类以上字符" />
            </el-form-item>
            <el-form-item label="确认新密码" prop="confirm_password">
              <el-input v-model="pwd.confirm_password" type="password" show-password />
            </el-form-item>
            <el-form-item>
              <el-button type="warning" :loading="changing" @click="changePwd">修改密码</el-button>
            </el-form-item>
          </el-form>
        </div>

        <div class="card">
          <div class="card-title"><span class="t">账号操作</span></div>
          <el-button type="danger" plain @click="doLogout">退出登录</el-button>
          <div class="muted small mt8">
            系统数据保存在本机 data/app.db，退出登录不会删除数据。
          </div>
        </div>
      </el-col>
    </el-row>
  </div>`,
  setup() {
    const router = VueRouter.useRouter();
    const user = reactive({ ...(store.user || {}) });
    const formRef = ref(null);
    const pwdRef = ref(null);
    const saving = ref(false);
    const changing = ref(false);
    const uploading = ref(false);
    const fileInput = ref(null);
    const photoUrl = ref('');
    const form = reactive({
      real_name: (store.user && store.user.real_name) || '',
      phone: (store.user && store.user.phone) || '',
      email: (store.user && store.user.email) || '',
    });
    const pwd = reactive({ old_password: '', new_password: '', confirm_password: '' });
    const tagType = computed(() => roleTagType[user.role] || 'primary');
    const rules = {
      real_name: [{ required: true, message: '请输入真实姓名', trigger: 'blur' }],
      // 正则里的 \d 只能写一个反斜杠：写成两个就变成「字面反斜杠 + d」，
      // 任何正确手机号都匹配不上（fe_check 有静态扫描守着这个坑）
      phone: [{ pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: 'blur' }],
      email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
    };
    const pwdRules = {
      old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
      new_password: [
        { required: true, message: '请输入新密码', trigger: 'blur' },
        { min: 8, message: '密码不少于 8 位', trigger: 'blur' },
      ],
      confirm_password: [
        { required: true, message: '请再次输入新密码', trigger: 'blur' },
        { validator: (r, v, cb) => (v === pwd.new_password ? cb() : cb(new Error('两次输入的密码不一致'))),
          trigger: 'blur' },
      ],
    };

    async function save() {
      try { await formRef.value.validate(); } catch (e) { return; }
      saving.value = true;
      try {
        const u = await api.put('/api/auth/profile', form);
        setUser(u);
        msg.ok('资料已更新');
      } catch (e) { handleErr(e); } finally { saving.value = false; }
    }

    async function changePwd() {
      try { await pwdRef.value.validate(); } catch (e) { return; }
      changing.value = true;
      try {
        await api.post('/api/auth/change-password', pwd);
        msg.ok('密码修改成功，请重新登录');
        setTimeout(() => { logout(); location.hash = '#/login'; }, 800);
      } catch (e) { handleErr(e); } finally { changing.value = false; }
    }

    async function doLogout() {
      try { await api.post('/api/auth/logout', {}); } catch (e) { /* ignore */ }
      logout();
      router.push('/login');
    }

    /* 证件照：读取接口要带鉴权头，<img src> 带不了，
       所以取回 blob 再转成本地 objectURL（不用 URL 传 token，避免令牌泄漏到地址栏/日志） */
    async function refreshPhoto() {
      const uid = (store.user && store.user.id) || user.id;
      if (!uid) return;
      try {
        const res = await api.raw('GET', `/api/photos/${uid}`);
        const blob = await res.blob();
        if (photoUrl.value) URL.revokeObjectURL(photoUrl.value);
        photoUrl.value = URL.createObjectURL(blob);
      } catch (e) {
        if (photoUrl.value) { URL.revokeObjectURL(photoUrl.value); photoUrl.value = ''; }
      }
    }

    function pickFile() { if (fileInput.value) fileInput.value.click(); }

    async function onFileChange(e) {
      const f = e.target.files && e.target.files[0];
      e.target.value = '';                    // 允许再次选择同一个文件
      if (!f) return;
      if (!/^image\/(jpeg|png)$/.test(f.type)) { msg.err('仅支持 JPG / PNG 图片'); return; }
      if (f.size > 5 * 1024 * 1024) { msg.err('图片不能超过 5 MB'); return; }
      uploading.value = true;
      try {
        // 单张直传：不做任何抠图/换底处理，需要的规格由用户在「证件照制作」里先做好
        const fd = new FormData();
        fd.append('file', f);
        await api.upload('/api/photos/me', fd);
        msg.ok('证件照已上传');
        await refreshPhoto();
        setUser(await api.get('/api/auth/me'));
      } catch (err) { handleErr(err); } finally { uploading.value = false; }
    }

    async function removePhoto() {
      uploading.value = true;
      try {
        await api.del('/api/photos/me');
        msg.ok('证件照已删除');
        if (photoUrl.value) { URL.revokeObjectURL(photoUrl.value); photoUrl.value = ''; }
        setUser(await api.get('/api/auth/me'));
      } catch (err) { handleErr(err); } finally { uploading.value = false; }
    }

    onMounted(async () => {
      try {
        const u = await api.get('/api/auth/me');
        Object.assign(user, u);
        form.real_name = u.real_name; form.phone = u.phone; form.email = u.email;
        setUser(u);
        await refreshPhoto();
      } catch (e) { handleErr(e); }
    });

    return { user, roleLabel, tagType, form, rules, formRef, pwd, pwdRules, pwdRef, saving,
      changing, uploading, fileInput, photoUrl, pickFile, onFileChange, removePhoto,
      save, changePwd, doLogout };
  },
};
