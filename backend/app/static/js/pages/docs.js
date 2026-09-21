/* 内置文档：使用说明 / 开发文档（随程序打包，Markdown 本地渲染） */
import { api, handleErr } from '../api.js';

const { ref, computed, onMounted, watch, nextTick } = Vue;

export const DocsPage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>{{ title }}</h2>
        <div class="sub">{{ desc }}</div>
      </div>
      <div class="filter-bar">
        <el-radio-group v-model="key" size="small" @change="switchDoc">
          <el-radio-button v-for="d in list" :key="d.key" :value="d.key">{{ d.title }}</el-radio-button>
        </el-radio-group>
        <el-input v-model="kw" placeholder="在文档中查找" clearable
                  style="width:200px" @keyup.enter="locate" @clear="clearKw" />
        <el-button @click="locate" :disabled="!kw">查找</el-button>
      </div>
    </div>

    <div class="card">
      <el-skeleton :rows="6" animated v-if="loading" />
      <el-alert v-else-if="error" type="error" show-icon :closable="false" :title="error" />
      <div v-else class="md-body" v-html="html"></div>
    </div>

    <div class="card mt16" v-if="!loading && !error">
      <div class="muted small">
        本文档随程序内置，与 exe 版本同步更新 · 共 {{ charCount }} 字
        <span v-if="hitCount"> · 查找到 {{ hitCount }} 处</span>
      </div>
    </div>
  </div>`,
  setup() {
    const route = VueRouter.useRoute();
    const router = VueRouter.useRouter();
    const key = ref(route.params.key || 'usage');
    const list = ref([]);
    const title = ref('');
    const desc = ref('');
    const html = ref('');
    const loading = ref(true);
    const error = ref('');
    const kw = ref('');
    const hitCount = ref(0);
    const raw = ref('');

    const charCount = computed(() => raw.value.length);

    async function loadList() {
      try {
        const d = await api.get('/api/manual');
        list.value = d.list || [];
        // 当前文档无权查看时，退回第一个可读的
        if (list.value.length && !list.value.some((x) => x.key === key.value)) {
          key.value = list.value[0].key;
        }
      } catch (e) { handleErr(e); }
    }

    async function load() {
      loading.value = true;
      error.value = '';
      hitCount.value = 0;
      try {
        const d = await api.get('/api/manual/' + key.value);
        raw.value = d.content || '';
        title.value = d.title;
        desc.value = d.desc || '';
        // marked 由 vendor 提供（本地化，无外网依赖）
        html.value = (window.marked ? window.marked.parse(d.content || '')
                                    : escapeHtml(d.content || ''));
      } catch (e) {
        raw.value = ''; html.value = '';
        error.value = (e && e.message) || '文档加载失败';
      } finally { loading.value = false; }
    }

    function switchDoc() {
      router.push('/docs/' + key.value);
      load();
    }

    function escapeHtml(s) {
      return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
    }

    /* 页内查找：用浏览器原生高亮，不改动渲染结果 */
    function locate() {
      const word = (kw.value || '').trim();
      hitCount.value = 0;
      if (!word) return;
      const root = document.querySelector('.md-body');
      if (!root) return;
      // 先清掉上一次的高亮
      root.querySelectorAll('mark.md-hit').forEach((m) => {
        m.replaceWith(document.createTextNode(m.textContent));
      });
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      let hits = 0;
      for (const n of nodes) {
        const idx = n.nodeValue.toLowerCase().indexOf(word.toLowerCase());
        if (idx < 0) continue;
        const frag = document.createDocumentFragment();
        let rest = n.nodeValue, base = 0;
        while (true) {
          const i = rest.toLowerCase().indexOf(word.toLowerCase());
          if (i < 0) break;
          frag.appendChild(document.createTextNode(rest.slice(0, i)));
          const mk = document.createElement('mark');
          mk.className = 'md-hit';
          mk.style.background = '#ffe58f';
          mk.textContent = rest.slice(i, i + word.length);
          frag.appendChild(mk);
          rest = rest.slice(i + word.length);
          hits += 1;
          base += 1;
        }
        frag.appendChild(document.createTextNode(rest));
        n.parentNode.replaceChild(frag, n);
      }
      hitCount.value = hits;
      if (hits) {
        const first = root.querySelector('mark.md-hit');
        if (first) first.scrollIntoView({ block: 'center' });
      }
    }

    function clearKw() {
      kw.value = '';
      hitCount.value = 0;
      const root = document.querySelector('.md-body');
      if (root) root.querySelectorAll('mark.md-hit').forEach(
        (m) => m.replaceWith(document.createTextNode(m.textContent)));
    }

    onMounted(async () => {
      await loadList();
      await load();
    });

    watch(() => route.params.key, async (v) => {
      if (v && v !== key.value) { key.value = v; await load(); }
    });

    return { key, list, title, desc, html, loading, error, kw, hitCount,
             charCount, switchDoc, locate, clearKw };
  },
};
