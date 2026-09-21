/* 首页：开放报名考试列表 + 快捷概览 */
import { api, handleErr } from '../api.js';
import { can, store } from '../store.js';

const { ref, onMounted, computed } = Vue;

export const HomePage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>开放报名批次</h2>
        <div class="sub">当前处于报名开放期的考试批次，请在截止时间前完成报名</div>
      </div>
      <div class="toolbar">
        <el-button :icon="'Refresh'" @click="load" :loading="loading">刷新</el-button>
        <el-button v-if="canManageExam" @click="$router.push('/exams/new')">新建批次</el-button>
        <el-button v-if="canImport" @click="$router.push('/import')">批量导入</el-button>
        <el-button v-if="canApply" type="primary" @click="$router.push('/my-applications')">
          我的报名
        </el-button>
      </div>
    </div>

    <div v-if="isReviewer" class="kpi-grid mb16">
      <div class="kpi c-blue">
        <div class="k-label">报名总数</div>
        <div class="k-value">{{ summary.total || 0 }}</div>
        <div class="k-sub">全部批次累计</div>
      </div>
      <div class="kpi c-orange">
        <div class="k-label">待审核</div>
        <div class="k-value">{{ summary.pending || 0 }}</div>
        <div class="k-sub">待处理报名记录</div>
      </div>
      <div class="kpi c-green">
        <div class="k-label">审核通过率</div>
        <div class="k-value">{{ summary.pass_rate || 0 }}%</div>
        <div class="k-sub">已通过 / 已审核</div>
      </div>
      <div class="kpi c-purple">
        <div class="k-label">开放批次</div>
        <div class="k-value">{{ list.length }}</div>
        <div class="k-sub">报名期内</div>
      </div>
    </div>

    <div v-loading="loading">
      <div v-if="!list.length" class="card empty-box">
        <el-empty description="当前没有开放中的考试批次" />
      </div>
      <div v-else class="exam-grid">
        <div v-for="e in list" :key="e.id" class="exam-card" :class="{ md: e.exam_type_base === 'mandarin' }">
          <h3>{{ e.name }}</h3>
          <div class="meta">
            <div><span class="type-badge" :class="{ md: e.exam_type_base === 'mandarin' }">
              {{ e.exam_type_label }}</span></div>
            <div>报名时间：{{ e.signup_start_at }} 至 {{ e.signup_end_at }}</div>
            <div>报名人数：{{ e.stats.total }} 人
              <span class="muted">（待审 {{ e.stats.pending }} / 通过 {{ e.stats.approved }}）</span>
            </div>
            <div v-if="e.description" class="muted">{{ e.description }}</div>
          </div>
          <div class="foot">
            <span class="muted small">批次编号 #{{ e.id }}</span>
            <div class="toolbar">
              <template v-if="canApply">
                <el-button v-if="appliedIds.includes(e.id)" size="small" @click="$router.push('/my-applications')">
                  已报名
                </el-button>
                <el-button v-else type="primary" size="small" @click="$router.push('/apply/' + e.id)">
                  立即报名
                </el-button>
              </template>
              <el-button v-if="canAudit" size="small" @click="$router.push('/review?exam_id=' + e.id)">
                进入审核
              </el-button>
              <el-button v-if="canImport" size="small" plain
                         @click="$router.push('/import')">批量导入</el-button>
              <el-button v-if="canExport" size="small" type="primary" plain
                         @click="$router.push('/export?exam_id=' + e.id)">导出</el-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="isReviewer && charts.exam_bar && charts.exam_bar.length" class="card mt16">
      <div class="card-title"><span class="t">各批次报名量</span></div>
      <div ref="chartEl" class="chart" style="height:300px"></div>
    </div>
  </div>`,
  setup() {
    const list = ref([]);
    const loading = ref(false);
    const summary = ref({});
    const charts = ref({});
    const appliedIds = ref([]);
    const chartEl = ref(null);
    let chart = null;
    const canApply = computed(() => can('apply'));
    const canAudit = computed(() => can('audit'));
    const canExport = computed(() => can('export'));
    const canImport = computed(() => can('import'));
    const canManageExam = computed(() => can('exam_manage'));
    const isReviewer = computed(() => canAudit.value || canImport.value);

    async function load() {
      loading.value = true;
      try {
        const data = await api.get('/api/exams?page_size=100');
        list.value = data.list;
        if (canApply.value) {
          const mine = await api.get('/api/applications/mine');
          appliedIds.value = mine.map((m) => m.exam_id);
        }
        if (canAudit.value) {
          summary.value = await api.get('/api/analysis/summary');
          charts.value = await api.get('/api/analysis/charts');
          renderChart();
        }
      } catch (e) {
        handleErr(e);
      } finally {
        loading.value = false;
      }
    }

    function renderChart() {
      if (!chartEl.value || !charts.value.exam_bar) return;
      chart = chart || echarts.init(chartEl.value);
      chart.setOption({
        grid: { left: 40, right: 20, top: 30, bottom: 60 },
        tooltip: { trigger: 'axis' },
        xAxis: {
          type: 'category',
          data: charts.value.exam_bar.map((x) => x.name.replace(/(\d{4})年/, '$1\n')),
          axisLabel: { fontSize: 11, interval: 0, rotate: 20 },
        },
        yAxis: { type: 'value', name: '人数' },
        series: [{
          type: 'bar', barMaxWidth: 42, data: charts.value.exam_bar.map((x) => x.count),
          itemStyle: { borderRadius: [5, 5, 0, 0], color: '#1f6feb' },
          label: { show: true, position: 'top', fontSize: 11 },
        }],
      });
    }

    onMounted(load);
    return {
      list, loading, summary, charts, appliedIds, chartEl, load,
      canApply, canAudit, canExport, canImport, canManageExam, isReviewer, store,
    };
  },
};
