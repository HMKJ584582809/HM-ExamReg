/* 证件照制作：上传 → 智能抠图 → 换底色 → 一寸/二寸排版 */
import { api } from '../api.js';
import { handleErr, msg } from '../api.js';
import { store } from '../store.js';

const { defineComponent, ref, computed, onUnmounted, watch } = Vue;

// 用户可调参数的本地记忆键：用户在制作页改过的值记下来，下次打开继续用；
// 管理员在「系统维护」里改的是初始化值（默认），点「恢复默认」即可回退到它。
const LS_KEY = 'idphoto.params';

export const IdPhotoPage = defineComponent({
  name: 'IdPhotoPage',
  setup() {
    const specs = ref({ sizes: [], colors: [], default_size: 'one_inch', default_color: 'white' });
    const engines = ref(null);
    const sizeKey = ref('one_inch');
    const colorKey = ref('white');
    const src = ref('');          // 原图预览（objectURL）
    const srcFile = ref(null);
    const outUrl = ref('');       // 成品预览
    const outInfo = ref('');
    const busy = ref(false);
    const dragOver = ref(false);
    const inputEl = ref(null);
    const mineBusy = ref(false);
    const paramOpen = ref(false);
    // 可调参数：抠图阈值 / 边缘羽化 / 底部补底 / 留白填充
    const params = ref({ alpha_threshold: 55, edge_feather: 10, bottom_fill: 12, blank_fill: 'edge' });
    const defParams = ref({ alpha_threshold: 55, edge_feather: 10, bottom_fill: 12, blank_fill: 'edge' });

    const sizeLabel = computed(() => {
      const s = specs.value.sizes.find((x) => x.key === sizeKey.value);
      return s ? `${s.label}${s.mm ? '（' + s.mm + '）' : ''}` : '';
    });
    const localOk = computed(() => !!(engines.value && engines.value.local &&
                                      engines.value.local.enabled));
    const paramDirty = computed(() =>
      ['alpha_threshold', 'edge_feather', 'bottom_fill', 'blank_fill']
        .some((k) => params.value[k] !== defParams.value[k]));

    function saveLocal() {
      try { localStorage.setItem(LS_KEY, JSON.stringify(params.value)); } catch (e) { /* 隐私模式下忽略 */ }
    }
    function restoreDefault() {
      params.value = { ...defParams.value };
      saveLocal();
      msg.ok('已恢复为系统初始化参数');
    }

    async function load() {
      try {
        // ⚠ api.get 返回的已是解包后的 data，不能再多取一层
        //   （曾多取一层 → 恒为 undefined：尺寸/底色选项一个都不显示，
        //    还会因 engines 为 null 误报「本地抠图模型不可用」）
        const d = (await api.get('/api/idphoto/specs')) || {};
        specs.value = d;
        engines.value = d.engines || null;
        if (d.default_size) sizeKey.value = d.default_size;
        if (d.default_color) colorKey.value = d.default_color;
        if (d.params) {
          defParams.value = { ...defParams.value, ...d.params };
          let saved = null;
          try { saved = JSON.parse(localStorage.getItem(LS_KEY) || 'null'); } catch (e) { saved = null; }
          params.value = { ...defParams.value, ...(saved || {}) };
        }
      } catch (e) { handleErr(e); }
    }

    function reset() {
      if (src.value) URL.revokeObjectURL(src.value);
      if (outUrl.value) URL.revokeObjectURL(outUrl.value);
      src.value = ''; outUrl.value = ''; outInfo.value = ''; srcFile.value = null;
    }

    function pickFile(file) {
      if (!file) return;
      if (!/^image\//.test(file.type)) { msg.warn('请选择图片文件'); return; }
      if (file.size > 20 * 1024 * 1024) { msg.warn('照片不能超过 20MB'); return; }
      reset();
      srcFile.value = file;
      src.value = URL.createObjectURL(file);
    }

    function onPick(e) {
      const f = (e.target.files || [])[0];
      pickFile(f);
      e.target.value = '';
    }
    function onDrop(e) {
      dragOver.value = false;
      const f = (e.dataTransfer.files || [])[0];
      pickFile(f);
    }

    /* 「调用我的照片」：把个人中心已上传的证件照直接拷进编辑器继续处理。
       读取接口要带鉴权头，<img src> 带不了，所以取回 blob 再转成 File。 */
    async function useMyPhoto() {
      const uid = (store.user && store.user.id);
      if (!uid) { msg.warn('尚未登录'); return; }
      mineBusy.value = true;
      try {
        const res = await api.raw('GET', `/api/photos/${uid}`);
        if (!res.ok) { msg.warn('个人中心还没有证件照，请先上传'); return; }
        const blob = await res.blob();
        if (!blob || !blob.size) { msg.warn('个人中心还没有证件照，请先上传'); return; }
        const ext = (blob.type || '').indexOf('png') >= 0 ? 'png' : 'jpg';
        const f = new File([blob], `my-photo.${ext}`, { type: blob.type || 'image/jpeg' });
        pickFile(f);
        msg.ok('已载入个人中心的照片');
      } catch (e) {
        msg.warn('个人中心还没有证件照，可在「个人中心」先上传');
      } finally { mineBusy.value = false; }
    }

    async function make() {
      if (!srcFile.value) { msg.warn('请先选择一张照片'); return; }
      busy.value = true;
      try {
        const fd = new FormData();
        fd.append('file', srcFile.value);
        fd.append('size', sizeKey.value);
        fd.append('color', colorKey.value);
        fd.append('params', JSON.stringify(params.value));
        const r = await api.uploadBlob('/api/idphoto/make', fd);
        if (outUrl.value) URL.revokeObjectURL(outUrl.value);
        outUrl.value = URL.createObjectURL(r.blob);
        const eng = r.headers.get('X-IdPhoto-Engine') || '';
        const size = r.headers.get('X-IdPhoto-Size') || '';
        outInfo.value = `${sizeLabel.value} · ${size}${eng ? ' · ' + (eng === 'local' ? '本地抠图' : '远程接口') : ''}`;
        msg.ok('已生成，可点击下方下载');
      } catch (e) { handleErr(e); }
      finally { busy.value = false; }
    }

    function download() {
      if (!outUrl.value) return;
      const c = specs.value.colors.find((x) => x.key === colorKey.value) || {};
      const ext = c.hex === null ? 'png' : 'jpg';
      const s = specs.value.sizes.find((x) => x.key === sizeKey.value) || {};
      const a = document.createElement('a');
      a.href = outUrl.value;
      a.download = `证件照-${s.label || '一寸'}.${ext}`;
      a.click();
    }

    /* 上传成品到个人中心：省去「下载 → 回个人中心 → 上传」三步 */
    async function saveToProfile() {
      if (!outUrl.value) return;
      busy.value = true;
      try {
        const blob = await (await fetch(outUrl.value)).blob();
        const c = specs.value.colors.find((x) => x.key === colorKey.value) || {};
        const ext = c.hex === null ? 'png' : 'jpg';
        const fd = new FormData();
        fd.append('file', new File([blob], `idphoto.${ext}`, { type: blob.type || 'image/jpeg' }));
        await api.upload('/api/photos/me', fd);
        msg.ok('已更新到个人中心证件照');
      } catch (e) { handleErr(e); } finally { busy.value = false; }
    }

    // 规格或参数变化后成品即失效，提示重新生成
    watch([sizeKey, colorKey], () => {
      if (outUrl.value) { URL.revokeObjectURL(outUrl.value); outUrl.value = ''; outInfo.value = ''; }
    });
    watch(params, () => {
      saveLocal();
      if (outUrl.value) { URL.revokeObjectURL(outUrl.value); outUrl.value = ''; outInfo.value = ''; }
    }, { deep: true });

    onUnmounted(reset);
    load();

    return { specs, engines, sizeKey, colorKey, src, outUrl, outInfo, busy, dragOver,
             inputEl, sizeLabel, localOk, onPick, onDrop, make, download, reset,
             params, defParams, paramDirty, paramOpen, restoreDefault,
             mineBusy, useMyPhoto, saveToProfile };
  },
  template: `
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">证件照制作</h2>
        <p class="hint">上传一张生活照/自拍照，自动抠图换底色并排版为一寸或二寸标准证件照。</p>
      </div>
    </div>

    <el-alert v-if="!localOk" type="warning" :closable="false" show-icon class="mb-12"
      title="本地抠图模型不可用"
      description="未检测到内置模型，将尝试远程接口（需管理员在系统维护中配置）。" />

    <el-row :gutter="16">
      <el-col :xs="24" :md="10">
        <el-card shadow="never">
          <template #header><span class="card-title">1 · 选择照片</span></template>
          <div class="idp-drop" :class="{ 'is-over': dragOver }"
               @click="inputEl && inputEl.click()"
               @dragover.prevent="dragOver = true"
               @dragleave.prevent="dragOver = false"
               @drop.prevent="onDrop">
            <img v-if="src" :src="src" class="idp-preview" alt="原图" />
            <div v-else class="idp-empty">
              <el-icon :size="34"><Picture /></el-icon>
              <p>点击选择，或将照片拖到这里</p>
              <p class="hint">建议正面免冠、背景干净；支持 JPG / PNG，≤20MB</p>
            </div>
          </div>
          <input ref="inputEl" type="file" accept="image/*" hidden @change="onPick" />
          <div class="mt-12" v-if="src">
            <el-button size="small" plain @click="reset">重新选择</el-button>
          </div>
          <div class="mt-12">
            <el-button size="small" type="primary" plain :loading="mineBusy" @click="useMyPhoto">
              调用我的照片
            </el-button>
            <span class="hint" style="margin-left:8px">直接拷贝个人中心已上传的证件照来编辑</span>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="14">
        <el-card shadow="never">
          <template #header><span class="card-title">2 · 规格与底色</span></template>
          <el-form label-width="88px">
            <el-form-item label="尺寸">
              <el-radio-group v-model="sizeKey">
                <el-radio-button v-for="s in specs.sizes" :key="s.key" :value="s.key">
                  {{ s.label }}<span v-if="s.mm" class="hint"> · {{ s.mm }}</span>
                </el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="底色">
              <el-radio-group v-model="colorKey">
                <el-radio-button v-for="c in specs.colors" :key="c.key" :value="c.key">
                  <span class="idp-swatch" :style="{ background: c.hex || 'transparent' }"></span>
                  {{ c.label }}
                </el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-form>
          <div class="mt-12">
            <el-button type="primary" :loading="busy" :disabled="!src" @click="make">
              生成证件照
            </el-button>
            <span class="hint" style="margin-left:8px">{{ sizeLabel }}</span>
          </div>
        </el-card>

        <el-card shadow="never" class="mt-16">
          <template #header>
            <div class="idp-phead" @click="paramOpen = !paramOpen">
              <span class="card-title">抠图参数（可调）</span>
              <span class="hint">
                {{ paramOpen ? '收起' : '展开' }}
                <el-tag v-if="paramDirty" size="small" type="warning" style="margin-left:6px">已调整</el-tag>
              </span>
            </div>
          </template>
          <div v-show="paramOpen">
            <el-form label-width="96px">
              <el-form-item label="抠图阈值">
                <el-slider v-model="params.alpha_threshold" :min="0" :max="255" :step="1" show-input
                           :show-input-controls="false" style="width:100%" />
                <div class="hint">alpha 低于此值一律当背景。调高可消除轮廓发灰、原背景透出；过高会啃掉头发丝。</div>
              </el-form-item>
              <el-form-item label="边缘羽化">
                <el-slider v-model="params.edge_feather" :min="0" :max="60" :step="1" show-input
                           :show-input-controls="false" style="width:100%" />
                <div class="hint">阈值之上保留一段过渡带，避免硬边锯齿。0 = 完全硬边。</div>
              </el-form-item>
              <el-form-item label="底部补底">
                <el-slider v-model="params.bottom_fill" :min="0" :max="60" :step="1" show-input
                           :show-input-controls="false" style="width:100%" />
                <div class="hint">底部百分之多少的行内，非实心像素强制透明（用底色填满）。专治「底部漏色」，0 = 关闭。</div>
              </el-form-item>
              <el-form-item label="留白填充">
                <el-radio-group v-model="params.blank_fill">
                  <el-radio-button value="edge">取照片边缘色</el-radio-button>
                  <el-radio-button value="white">纯白</el-radio-button>
                </el-radio-group>
                <div class="hint">仅「不换底」时生效：比例不一致留出的空白边填什么。换底色时留白一律用所选底色（整幅才均匀）。</div>
              </el-form-item>
            </el-form>
            <el-button size="small" plain :disabled="!paramDirty" @click="restoreDefault">
              恢复初始化参数
            </el-button>
            <span class="hint" style="margin-left:8px">
              默认值由管理员在「系统维护 → 证件照初始化参数」设定，这里的调整只记在本机浏览器上。
            </span>
          </div>
        </el-card>

        <el-card shadow="never" class="mt-16">
          <template #header><span class="card-title">3 · 成品</span></template>
          <div v-if="outUrl" class="idp-result">
            <img :src="outUrl" alt="证件照" />
            <div class="mt-12">
              <p class="hint">{{ outInfo }}</p>
              <el-button type="success" @click="download">下载证件照</el-button>
              <el-button type="primary" plain :loading="busy" @click="saveToProfile">
                保存到个人中心
              </el-button>
            </div>
          </div>
          <el-empty v-else description="左侧选择照片后点击「生成证件照」" :image-size="70" />
        </el-card>
      </el-col>
    </el-row>
  </div>
  `,
});
