/* 模块10：AI 智能分析 + AI 客服 + 大模型配置（可选） */
import { api, handleErr, msg } from '../api.js';
import { can } from '../store.js';

const { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick, watch } = Vue;

export const AiPage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>AI 智能助手</h2>
        <div class="sub">
          智能分析报名数据、给出处置建议；随时问答系统用法与本人数据
          <el-tag size="small" :type="engineType" style="margin-left:6px">{{ engineLabel }}</el-tag>
          <el-tag v-if="scopeLabel" size="small" type="info" style="margin-left:6px">{{ scopeLabel }}</el-tag>
        </div>
      </div>
      <el-button @click="reload" :loading="loading">刷新</el-button>
    </div>

    <el-tabs v-model="tab" class="ai-tabs">
      <el-tab-pane label="智能分析" name="analyze">
        <template v-if="!canAnalysis">
          <el-alert type="info" :closable="false" show-icon title="智能数据分析需要「数据分析」权限">
            <div class="lh-17">当前账号未开通该权限，可切换到「AI 客服」页提问，或联系管理员开通。</div>
          </el-alert>
        </template>
        <template v-else>
          <div class="card">
            <div class="filter-bar">
              <el-select v-model="query.exam_id" placeholder="考试批次（全部）" clearable filterable
                         style="width:280px">
                <el-option v-for="e in examOptions" :key="e.id" :label="e.label" :value="e.id" />
              </el-select>
              <el-select v-model="query.exam_type" placeholder="考试类型（全部）" clearable
                         style="width:170px">
                <el-option v-for="t in types" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
              <el-button type="primary" @click="analyze" :loading="loading">开始分析</el-button>
              <el-button @click="resetQuery">重置</el-button>
            </div>
          </div>

          <div class="kpi-grid mb16">
            <div class="kpi c-blue">
              <div class="k-label">报名总数</div><div class="k-value">{{ ov.total || 0 }}</div>
              <div class="k-sub">覆盖 {{ ov.exam_count || 0 }} 个批次</div>
            </div>
            <div class="kpi c-orange">
              <div class="k-label">待审核</div><div class="k-value">{{ ov.pending || 0 }}</div>
              <div class="k-sub">占比 {{ ov.pending_rate || 0 }}%</div>
            </div>
            <div class="kpi c-green">
              <div class="k-label">审核通过</div><div class="k-value">{{ ov.approved || 0 }}</div>
              <div class="k-sub">通过率 {{ ov.pass_rate || 0 }}%</div>
            </div>
            <div class="kpi c-purple">
              <div class="k-label">数据健康分</div><div class="k-value">{{ health.score }}</div>
              <div class="k-sub">{{ healthText }}</div>
            </div>
          </div>

          <div class="card">
            <h3 class="card-title">分析结论</h3>
            <div class="ai-summary lh-17">{{ result.summary || '点击「开始分析」生成结论。' }}</div>
            <div class="mt8 hint" v-if="result.generated_at">生成时间 {{ result.generated_at }}</div>
            <el-alert v-if="llmText" type="success" :closable="false" style="margin-top:12px"
                      title="大模型解读">
              <div class="llm-text lh-17">{{ llmText }}</div>
            </el-alert>
            <el-alert v-if="llmError" type="warning" :closable="false" class="mt12"
                      :title="'大模型未参与本次分析：' + llmError">
              <div class="lh-17 fs-13" v-if="llmHint">{{ llmHint }}</div>
            </el-alert>
          </div>

          <div class="card">
            <h3 class="card-title">处置建议</h3>
            <el-empty v-if="!sugs.length && !loading" description="暂无处置建议" :image-size="60" />
            <div class="sug-list">
              <div v-for="(s, i) in sugs" :key="i" class="sug-item" :class="'lv-' + s.level">
                <div class="sug-main">
                  <div class="sug-title">{{ s.title }}</div>
                  <div class="sug-detail muted lh-17">{{ s.detail }}</div>
                </div>
                <el-button v-if="s.action" size="small" type="primary" plain
                           @click="$router.push(s.action.path)">{{ s.action.label }}</el-button>
              </div>
            </div>
          </div>

          <div class="card">
            <h3 class="card-title">数据体检（{{ issueCount }} 类问题）</h3>
            <el-table :data="checks" size="small" border stripe>
              <el-table-column type="expand">
                <template #default="{ row }">
                  <div class="sample-box" v-if="row.samples && row.samples.length">
                    <el-table :data="row.samples" size="small">
                      <el-table-column prop="exam_name" label="考试批次" min-width="180" />
                      <el-table-column prop="name" label="姓名" width="90" />
                      <el-table-column prop="id_number" label="证件号" width="150" />
                      <el-table-column prop="note" label="说明" min-width="200" />
                    </el-table>
                  </div>
                  <div class="hint" v-else>该检查项无问题记录。</div>
                </template>
              </el-table-column>
              <el-table-column label="检查项" prop="label" min-width="180" />
              <el-table-column label="结果" width="96">
                <template #default="{ row }">
                  <el-tag size="small" :type="levelType(row.level)">{{ levelText(row.level) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="数量" prop="count" width="80" align="right" />
              <el-table-column label="说明与建议" min-width="300">
                <template #default="{ row }">
                  <div class="lh-17">{{ row.detail }}</div>
                  <div class="muted fs-12 lh-17">建议：{{ row.advice }}</div>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <el-row :gutter="16">
            <el-col :span="12">
              <div class="card">
                <h3 class="card-title">审核瓶颈</h3>
                <el-descriptions :column="1" size="small" border>
                  <el-descriptions-item label="积压（>72 小时）">
                    {{ bn.stale_pending || 0 }} 条
                  </el-descriptions-item>
                  <el-descriptions-item label="平均审核耗时">
                    {{ bn.avg_review_hours || 0 }} 小时（已审 {{ bn.reviewed_count || 0 }} 条）
                  </el-descriptions-item>
                </el-descriptions>
                <div class="section-title">待审核最多的批次</div>
                <el-table :data="bn.pending_by_exam" size="small" border>
                  <el-table-column prop="exam_name" label="批次" min-width="180" />
                  <el-table-column prop="pending" label="待审" width="70" align="right" />
                  <el-table-column label="已等待" width="100" align="right">
                    <template #default="{ row }">{{ row.wait_hours }} 小时</template>
                  </el-table-column>
                </el-table>
                <div v-if="bn.closing_soon && bn.closing_soon.length" class="section-title">临近截止</div>
                <el-alert v-for="c in bn.closing_soon" :key="c.exam_id" type="warning"
                          :closable="false" style="margin-bottom:8px">
                  <div class="fs-13">《{{ c.exam_name }}》{{ c.days_left }} 天后截止报名，
                    仍有 {{ c.pending }} 条待审核</div>
                </el-alert>
              </div>
            </el-col>
            <el-col :span="12">
              <div class="card">
                <h3 class="card-title">近 14 天报名趋势</h3>
                <div ref="cTrend" class="chart"></div>
              </div>
            </el-col>
          </el-row>

          <div class="card" v-if="bn.reviewer_rank && bn.reviewer_rank.length">
            <h3 class="card-title">审核员工作量</h3>
            <div ref="cReviewer" class="chart"></div>
          </div>
        </template>
      </el-tab-pane>

      <el-tab-pane label="AI 客服" name="chat">
        <div class="card">
          <div class="chat-box" ref="chatBox">
            <div v-for="(m, i) in messages" :key="i" class="chat-row" :class="m.role">
              <div class="bubble lh-17">{{ m.content }}</div>
            </div>
            <div v-if="sending" class="chat-row bot"><div class="bubble muted">正在思考…</div></div>
          </div>
          <el-alert v-if="llmNotice" type="info" :closable="true" class="mt8"
                    :title="llmNotice" @close="llmNotice = ''" />
          <div class="chip-row">
            <el-tag v-for="q in questions" :key="q" class="chip" effect="plain" round
                    @click="ask(q)">{{ q }}</el-tag>
          </div>
          <div class="chat-input">
            <el-input v-model="draft" type="textarea" :rows="2" resize="none"
                      placeholder="输入你的问题，回车发送（Shift + Enter 换行）"
                      @keydown.enter.exact.prevent="send" />
            <div class="chat-actions">
              <el-button @click="clearChat">清空</el-button>
              <el-button type="primary" @click="send" :loading="sending">发送</el-button>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="大模型配置" name="config" v-if="canManage">
        <div class="card">
          <h3 class="card-title">外部大模型（可选）</h3>
          <div class="hint lh-17">
            默认使用内置本地规则引擎，<b>无需任何配置</b>即可完成智能分析与客服问答。
            填入 OpenAI 兼容服务后，分析结论会附加大模型的自然语言解读，客服遇到知识库未覆盖的问题由大模型兜底。
            未配置、超时或调用失败时一律自动回退到本地结果，不会影响软件本身的功能。
            密钥保存在本机 data/settings.json，接口只会返回「是否已配置」，不会回传明文。
          </div>
          <el-form :model="form" label-width="120px" class="ai-form">
            <el-form-item label="服务商">
              <el-select v-model="form.provider" filterable style="max-width:420px"
                         @change="onProviderChange">
                <el-option v-for="p in providers" :key="p.key" :label="p.label"
                           :value="p.key">
                  <span>{{ p.label }}</span>
                  <span class="muted fs-12" style="float:right;margin-left:16px">
                    {{ p.base_url || '手动填写' }}
                  </span>
                </el-option>
              </el-select>
              <div class="muted fs-12 lh-17 mt8">
                选一个预设会自动填好服务地址与候选模型，仍可手动修改；
                选「自定义」则完全自己填任何 OpenAI 兼容服务。
              </div>
              <el-alert v-if="providerNote" type="info" :closable="false" class="mt8"
                        :title="providerNote" />
              <el-alert v-if="providerOverseas" type="warning" :closable="false" class="mt8"
                        title="境外服务：国内网络通常需要在下方「网络代理」里填写本机代理地址才能连通。" />
            </el-form-item>
            <el-form-item label="启用大模型">
              <el-switch v-model="form.enable" :active-value="1" :inactive-value="0" />
              <span class="muted fs-12 ml8">关闭时完全不联网，仅用本地规则引擎</span>
            </el-form-item>
            <el-form-item label="服务地址">
              <el-input v-model="form.base_url" placeholder="https://api.openai.com/v1" />
              <div class="muted fs-12 lh-17 mt8">
                只填到版本目录（如 <code>.../v1</code>），系统会自动补
                <code>/chat/completions</code>。
              </div>
            </el-form-item>
            <el-form-item label="密钥">
              <el-input v-model="form.api_key" type="password" show-password
                        :placeholder="form.api_key_set ? '已配置，留空表示不修改' : (keyHint || 'sk-...')" />
              <div class="muted fs-12 lh-17 mt8" v-if="providerNoKey">
                本地服务无需密钥，留空即可。
              </div>
            </el-form-item>
            <el-form-item label="模型名称">
              <el-select v-model="form.model" filterable allow-create default-first-option
                         style="max-width:420px" placeholder="选择或输入模型名">
                <el-option v-for="m in modelOptions" :key="m" :label="m" :value="m" />
              </el-select>
              <div class="muted fs-12 lh-17 mt8">
                可直接从候选里选，也可以手动输入厂商控制台里的模型名 / 接入点 ID。
              </div>
            </el-form-item>
            <el-form-item label="超时（秒）">
              <el-input-number v-model="form.timeout" :min="5" :max="120" />
            </el-form-item>
            <el-form-item label="附加提示词">
              <el-input v-model="form.system_prompt" type="textarea" :rows="3"
                        placeholder="可选：追加到系统提示词，例如「回答控制在 100 字以内」" />
            </el-form-item>
            <el-form-item label="网络代理">
              <el-input v-model="form.proxy" style="max-width:420px"
                        placeholder="留空=跟随系统；境外模型常填 http://127.0.0.1:7890" />
              <div class="muted fs-12 lh-17 mt8">
                访问 OpenAI / Claude 等境外模型时填本机代理（Clash、V2Ray 一般为
                <code>http://127.0.0.1:7890</code>）；<b>none</b> 表示强制直连、忽略系统代理；
                留空则跟随系统环境变量。目标是本机模型（Ollama / vLLM）时始终直连，不受此项影响。
              </div>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveCfg" :loading="saving">保存配置</el-button>
              <el-button @click="testCfg" :loading="testing">测试连接（先验证，不保存也能测）</el-button>
              <el-button v-if="form.api_key_set" type="danger" plain @click="clearKey">
                清除已保存的密钥
              </el-button>
            </el-form-item>
          </el-form>

          <!-- 测试结果：不只说成功失败，要把「缺什么 / 哪里错 / 怎么改」讲清楚 -->
          <el-alert v-if="testResult" :type="testResult.ok ? 'success' : 'error'"
                    :closable="false" class="mt16" :title="testResult.message">
            <div class="lh-17 fs-13" v-if="testResult.hint">{{ testResult.hint }}</div>
            <div class="muted fs-12 lh-17 mt8" v-if="testResult.meta">
              服务地址 {{ testResult.meta.base_url }} · 模型 {{ testResult.meta.model }}
              · 代理 {{ testResult.meta.proxy }} · 耗时 {{ testResult.latency_ms }} ms
            </div>
            <ul class="diag-list" v-if="testResult.issues && testResult.issues.length">
              <li v-for="(it, i) in testResult.issues" :key="i">
                <b>{{ it.label }}</b>
                <span class="muted">{{ it.hint }}</span>
              </li>
            </ul>
          </el-alert>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>`,
  setup() {
    const tab = ref('analyze');
    const loading = ref(false);
    const result = ref({});
    const examOptions = ref([]);
    const types = ref([{ value: 'computer', label: '计算机类考试' },
      { value: 'mandarin', label: '普通话水平测试' }]);
    const query = reactive({ exam_id: '', exam_type: '' });
    const scopeLabel = ref('');
    const cfg = ref({});
    const form = reactive({
      provider: 'custom', enable: 0, base_url: '', api_key: '', model: '',
      timeout: 30, system_prompt: '', proxy: '', api_key_set: false,
    });
    const saving = ref(false);
    const testing = ref(false);
    const providers = ref([]);
    const testResult = ref(null);
    const llmNotice = ref('');

    const canAnalysis = computed(() => can('analysis'));
    const canManage = computed(() => can('user_manage'));
    const ov = computed(() => result.value.overview || {});
    const health = computed(() => result.value.health || { score: 0, level: 'good' });
    const bn = computed(() => result.value.bottlenecks || {});
    const checks = computed(() => (result.value.health || {}).checks || []);
    const sugs = computed(() => result.value.suggestions || []);
    const issueCount = computed(() => checks.value.filter((c) => c.count > 0).length);
    const llmText = computed(() => (result.value.llm || {}).text || '');
    const llmError = computed(() => (result.value.llm || {}).error || '');
    const llmHint = computed(() => (result.value.llm || {}).hint || '');
    const curProvider = computed(
      () => providers.value.find((p) => p.key === form.provider) || null);
    const providerNote = computed(() => (curProvider.value || {}).note || '');
    const providerOverseas = computed(() => !!(curProvider.value || {}).overseas);
    const providerNoKey = computed(() => !!(curProvider.value || {}).no_key);
    const keyHint = computed(() => (curProvider.value || {}).key_hint || '');
    const modelOptions = computed(() => {
      const preset = (curProvider.value || {}).models || [];
      const cur = (form.model || '').trim();
      return cur && !preset.includes(cur) ? [cur, ...preset] : preset;
    });

    /* 选预设厂商：填地址、给候选模型；地址/模型被手改后自动回到 custom */
    function onProviderChange(key) {
      const p = providers.value.find((x) => x.key === key);
      if (!p) return;
      if (p.base_url) form.base_url = p.base_url;
      if (p.models && p.models.length && !p.models.includes(form.model)) {
        form.model = p.models[0];
      }
      if (p.overseas && !form.proxy) form.proxy = 'http://127.0.0.1:7890';
      if (p.local) form.proxy = '';
      testResult.value = null;
    }

    function syncProviderByUrl() {
      const base = (form.base_url || '').trim().replace(/\/+$/, '').toLowerCase();
      const hit = providers.value.find(
        (p) => p.base_url && p.base_url.replace(/\/+$/, '').toLowerCase() === base);
      if (hit) form.provider = hit.key;
      else if (form.provider !== 'custom') form.provider = 'custom';
    }
    const engineLabel = computed(() => cfg.value.engine_label || '本地规则引擎');
    const engineType = computed(() => (cfg.value.ready ? 'success' : 'info'));
    const healthText = computed(() => (
      { good: '数据质量良好', fair: '有可改进项', poor: '需重点处理' }[health.value.level] || ''));

    const messages = ref([{ role: 'bot', content: '你好，我是本系统的智能助手。可以问我报名、审核、导入导出、权限相关的问题，也能直接查你自己的报名状态。' }]);
    const draft = ref('');
    const sending = ref(false);
    const questions = ref([]);
    const chatBox = ref(null);
    const cTrend = ref(null);
    const cReviewer = ref(null);
    let chartTrend = null;
    let chartReviewer = null;

    function levelText(lv) {
      return { ok: '正常', warn: '需关注', danger: '需处理' }[lv] || lv || '';
    }
    function levelType(lv) {
      return { ok: 'success', warn: 'warning', danger: 'danger' }[lv] || 'info';
    }

    // 地址被手动改过之后，厂商下拉要跟着变（否则会显示「OpenAI」却是自建地址）
    watch(() => form.base_url, syncProviderByUrl);

    function scrollBottom() {
      nextTick(() => {
        const el = chatBox.value;
        if (el) el.scrollTop = el.scrollHeight;
      });
    }

    async function send() {
      const text = (draft.value || '').trim();
      if (!text || sending.value) return;
      draft.value = '';
      // 历史要在「把本条追加进会话」之前取，否则本条会被算进 history，
      // 服务端再拼一次 message → 大模型收到两次相同的提问
      const history = messages.value.slice(-7).map((m) => ({
        role: m.role === 'me' ? 'user' : 'assistant', content: m.content,
      }));
      messages.value.push({ role: 'me', content: text });
      sending.value = true;
      scrollBottom();
      try {
        const d = await api.post('/api/ai/chat', { message: text, history });
        messages.value.push({ role: 'bot', content: d.reply || '（无回复）' });
        if (d.suggestions && d.suggestions.length) questions.value = d.suggestions;
        // 大模型没接上时明确告诉用户「本次由本地规则引擎回答 + 原因」，而不是沉默
        const llm = d.llm || {};
        llmNotice.value = (llm.used || !llm.error) ? '' :
          `本次由本地规则引擎回答（大模型不可用：${llm.error}${llm.hint ? '。' + llm.hint : ''}）`;
      } catch (e) { handleErr(e); } finally { sending.value = false; scrollBottom(); }
    }

    function ask(q) { draft.value = q; send(); }
    function clearChat() {
      messages.value = [{ role: 'bot', content: '已清空对话，还有什么可以帮你的？' }];
    }

    function drawTrend() {
      if (!cTrend.value) return;
      const days = (result.value.trend || {}).days || [];
      chartTrend = chartTrend || echarts.init(cTrend.value);
      chartTrend.setOption({
        grid: { left: 44, right: 20, top: 24, bottom: 40 },
        tooltip: { trigger: 'axis' },
        xAxis: {
          type: 'category', data: days.map((x) => x.date.slice(5)),
          axisLabel: { fontSize: 11, interval: 1 },
        },
        yAxis: { type: 'value', name: '报名数' },
        series: [{
          type: 'line', smooth: true, data: days.map((x) => x.count),
          areaStyle: { opacity: .16 }, lineStyle: { width: 3, color: '#1f6feb' },
          itemStyle: { color: '#1f6feb' }, symbolSize: 6,
        }],
      }, true);
    }

    function drawReviewer() {
      if (!cReviewer.value) return;
      const rank = bn.value.reviewer_rank || [];
      chartReviewer = chartReviewer || echarts.init(cReviewer.value);
      chartReviewer.setOption({
        grid: { left: 90, right: 40, top: 16, bottom: 24 },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'value' },
        yAxis: {
          type: 'category', inverse: true, data: rank.map((x) => x.name),
          axisLabel: { fontSize: 11, width: 82, overflow: 'truncate' },
        },
        series: [{
          type: 'bar', data: rank.map((x) => x.value), barMaxWidth: 16,
          itemStyle: { borderRadius: [0, 4, 4, 0], color: '#7c3aed' },
          label: { show: true, position: 'right', fontSize: 11 },
        }],
      }, true);
    }

    async function analyze() {
      loading.value = true;
      try {
        const d = await api.post('/api/ai/analyze', {
          exam_id: query.exam_id || 0,
          exam_type: query.exam_type || '',
        });
        result.value = d;
        scopeLabel.value = d.scope_label || '';
        await nextTick();
        drawTrend();
        drawReviewer();
      } catch (e) { handleErr(e); } finally { loading.value = false; }
    }

    function resetQuery() { query.exam_id = ''; query.exam_type = ''; analyze(); }

    async function loadOptions() {
      try {
        const d = await api.get('/api/exam-types/options');
        if (d.list && d.list.length) types.value = d.list;
      } catch (e) { /* 沿用内置两种类型 */ }
      if (!canAnalysis.value) return;
      try { examOptions.value = await api.get('/api/analysis/exam-options'); }
      catch (e) { /* 忽略 */ }
    }

    async function loadQuestions() {
      try {
        const d = await api.get('/api/ai/suggestions');
        questions.value = d.questions || [];
      } catch (e) { /* 忽略 */ }
    }

    async function loadCfg() {
      if (!canManage.value) return;
      try {
        const d = await api.get('/api/ai/config');
        cfg.value = d;
        if (d.providers && d.providers.length) providers.value = d.providers;
        const c = d.config || {};
        form.enable = c.enable || 0;
        form.base_url = c.base_url || '';
        form.model = c.model || '';
        form.timeout = c.timeout || 30;
        form.system_prompt = c.system_prompt || '';
        form.proxy = c.proxy || '';
        form.api_key = '';
        form.api_key_set = !!c.api_key_set;
        form.provider = c.provider || 'custom';
        if (form.provider === 'custom') syncProviderByUrl();
      } catch (e) { /* 忽略 */ }
    }

    async function saveCfg() {
      saving.value = true;
      try {
        const d = await api.put('/api/ai/config', {
          provider: form.provider, enable: form.enable, base_url: form.base_url,
          api_key: form.api_key, model: form.model, timeout: form.timeout,
          system_prompt: form.system_prompt, proxy: form.proxy,
        });
        cfg.value = d;
        const c = d.config || {};
        form.api_key = '';
        form.api_key_set = !!c.api_key_set;
        msg.ok('AI 配置已保存');
      } catch (e) { handleErr(e); } finally { saving.value = false; }
    }

    async function clearKey() {
      saving.value = true;
      try {
        const d = await api.put('/api/ai/config', { api_key: '__clear__' });
        cfg.value = d;
        form.api_key = '';
        form.api_key_set = !!(d.config || {}).api_key_set;
        msg.ok('已清除保存的密钥');
      } catch (e) { handleErr(e); } finally { saving.value = false; }
    }

    async function testCfg() {
      testing.value = true;
      testResult.value = null;
      try {
        // 把页面上的值原样带过去，未保存的配置也能先验证
        const d = await api.post('/api/ai/test', {
          base_url: form.base_url, api_key: form.api_key, model: form.model,
          proxy: form.proxy, timeout: form.timeout,
        });
        testResult.value = d;
        if (d.ok) msg.ok('连接成功'); else msg.warn(d.message);
      } catch (e) { handleErr(e); } finally { testing.value = false; }
    }

    async function reload() {
      if (tab.value === 'chat') await loadQuestions();
      else if (tab.value === 'config') await loadCfg();
      else if (canAnalysis.value) await analyze();
    }

    function onResize() {
      if (chartTrend) chartTrend.resize();
      if (chartReviewer) chartReviewer.resize();
    }

    onMounted(async () => {
      // 支持 URL ?tab=analyze|chat|config 深链直跳
      let initTab = '';
      try {
        initTab = new URL(location.href).searchParams.get('tab') || '';
      } catch (e) { /* ignore */ }
      const allowed = new Set(['analyze', 'chat', 'config']);
      if (allowed.has(initTab)) {
        if (initTab === 'analyze' && !canAnalysis.value) initTab = '';
        if (initTab === 'config' && !canManage.value) initTab = '';
      } else {
        initTab = '';
      }
      tab.value = initTab || (canAnalysis.value ? 'analyze' : 'chat');
      await loadOptions();
      await loadQuestions();
      await loadCfg();
      if (tab.value === 'analyze' && canAnalysis.value) await analyze();
      window.addEventListener('resize', onResize);
    });
    onBeforeUnmount(() => {
      window.removeEventListener('resize', onResize);
      if (chartTrend) { chartTrend.dispose(); chartTrend = null; }
      if (chartReviewer) { chartReviewer.dispose(); chartReviewer = null; }
    });

    return {
      tab, loading, result, examOptions, types, query, scopeLabel, cfg, form, saving, testing,
      canAnalysis, canManage, ov, health, bn, checks, sugs, issueCount, llmText, llmError,
      llmHint, llmNotice, providers, testResult, curProvider, providerNote, providerOverseas,
      providerNoKey, keyHint, modelOptions, onProviderChange,
      engineLabel, engineType, healthText, levelText, levelType,
      messages, draft, sending, questions, chatBox, cTrend, cReviewer,
      send, ask, clearChat, analyze, resetQuery, reload, saveCfg, testCfg, clearKey,
    };
  },
};
