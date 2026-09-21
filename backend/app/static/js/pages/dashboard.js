/* 模块7：数据看板（16:9 深色科技风大屏，自动刷新）

   分三层铺开：
     ① 总量层 —— 10 个 KPI（含审核时效、证件照完整率等过程指标）
     ② 常规层 —— 趋势 / 构成 / 漏斗 / 地区 / 排行
     ③ 深度层 —— 审核时效分布、驳回原因、考生画像、提交时段、
                  单位→班级、批次进度、审核员工作量、填报完整度
   深度层回答的是「卡在哪、为什么、谁最忙」，不是再堆一遍总量。 */
import { api, handleErr } from '../api.js';

const { ref, onMounted, onBeforeUnmount, nextTick } = Vue;

const AXIS = { axisLine: { lineStyle: { color: 'rgba(125,211,252,.35)' } },
  axisLabel: { color: '#9db8e8', fontSize: 11 },
  splitLine: { lineStyle: { color: 'rgba(125,211,252,.12)' } } };
const Y_CAT = { axisLine: { lineStyle: { color: 'rgba(125,211,252,.35)' } },
  axisLabel: { color: '#9db8e8', fontSize: 11 }, splitLine: { show: false } };

/* 横向条形图统一画法：类别轴在左、数值轴在下 */
function hbar(rows, colorFrom, colorTo, labelW = 78) {
  return {
    grid: { left: labelW, right: 40, top: 12, bottom: 20 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', ...AXIS },
    yAxis: { type: 'category', inverse: true, data: rows.map((x) => x.name), ...Y_CAT,
      axisLabel: { color: '#9db8e8', fontSize: 11, width: labelW - 8, overflow: 'truncate' } },
    series: [{
      type: 'bar', data: rows.map((x) => x.value), barMaxWidth: 13,
      itemStyle: { borderRadius: [0, 4, 4, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0,
          [{ offset: 0, color: colorFrom }, { offset: 1, color: colorTo }]) },
      label: { show: true, position: 'right', color: colorTo, fontSize: 11 },
    }],
  };
}

function vbar(rows, colorFrom, colorTo) {
  return {
    grid: { left: 40, right: 16, top: 20, bottom: 30 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'category', data: rows.map((x) => x.name), ...AXIS,
      axisLabel: { color: '#9db8e8', fontSize: 11, interval: 0, rotate: 30 } },
    yAxis: { type: 'value', ...AXIS },
    series: [{
      type: 'bar', data: rows.map((x) => x.value), barMaxWidth: 22,
      itemStyle: { borderRadius: [4, 4, 0, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1,
          [{ offset: 0, color: colorTo }, { offset: 1, color: colorFrom }]) },
    }],
  };
}

export const DashboardPage = {
  template: `
  <div class="screen" ref="rootEl">
    <div class="screen-head">
      <h1>考试报名数据看板</h1>
      <div class="clock">
        <span>{{ clock }}</span>
        <el-select v-model="interval" size="small" style="width:110px;margin-left:14px"
                   @change="restart">
          <el-option :value="30" label="30 秒刷新" />
          <el-option :value="60" label="60 秒刷新" />
        </el-select>
        <el-button size="small" type="primary" plain style="margin-left:10px" @click="load">
          立即刷新
        </el-button>
        <el-button size="small" style="margin-left:10px" @click="toggleFullscreen">
          {{ isFullscreen ? '退出全屏' : '全屏展示' }}
        </el-button>
      </div>
    </div>

    <div class="screen-kpis">
      <div class="skpi"><div class="l">报名总数</div><div class="v">{{ k.total || 0 }}</div></div>
      <div class="skpi g"><div class="l">今日新增</div><div class="v">{{ k.today || 0 }}</div></div>
      <div class="skpi g"><div class="l">审核通过</div><div class="v">{{ k.approved || 0 }}</div></div>
      <div class="skpi o"><div class="l">待审核</div><div class="v">{{ k.pending || 0 }}</div></div>
      <div class="skpi r"><div class="l">已驳回</div><div class="v">{{ k.rejected || 0 }}</div></div>
      <div class="skpi p"><div class="l">开放批次 / 通过率</div>
        <div class="v">{{ k.open_exam_count || 0 }} <span class="fs-16">/ {{ k.pass_rate || 0 }}%</span></div>
      </div>
    </div>

    <div class="screen-kpis2">
      <div class="skpi"><div class="l">平均审核耗时</div>
        <div class="v">{{ eff.avg_hours || 0 }}<span class="fs-16"> h</span></div></div>
      <div class="skpi g"><div class="l">已审核率</div>
        <div class="v">{{ k.reviewed_rate || 0 }}<span class="fs-16">%</span></div></div>
      <div class="skpi r"><div class="l">驳回率</div>
        <div class="v">{{ k.reject_rate || 0 }}<span class="fs-16">%</span></div></div>
      <div class="skpi p"><div class="l">证件照完整率</div>
        <div class="v">{{ photo.rate || 0 }}<span class="fs-16">%</span></div></div>
    </div>

    <div class="screen-grid">
      <div class="panel">
        <h4>近 30 天报名 / 审核趋势</h4>
        <div ref="cTrend30" class="chart tall"></div>
      </div>
      <div class="panel">
        <h4>当前开放批次</h4>
        <div class="open-list">
          <div v-for="e in openExams" :key="e.id" class="open-item">
            <div class="n">{{ e.name }}</div>
            <div class="d">截止 {{ e.signup_end_at }} · 已报 {{ e.stats.total }} 人 · 待审 {{ e.stats.pending }}</div>
          </div>
          <div v-if="!openExams.length" class="open-item"><div class="n">当前无开放批次</div></div>
        </div>
      </div>
    </div>

    <div class="screen-grid2">
      <div class="panel"><h4>考试类型占比</h4><div ref="cType" class="chart"></div></div>
      <div class="panel"><h4>审核漏斗（提交 → 已审 → 通过）</h4><div ref="cFunnel" class="chart"></div></div>
      <div class="panel"><h4>地区分布 Top10</h4><div ref="cRegion" class="chart"></div></div>
    </div>

    <div class="screen-sec">运行质量 · 找瓶颈</div>
    <div class="screen-grid2">
      <div class="panel">
        <h4>审核时效分布</h4>
        <div class="eff-hint">均 {{ eff.avg_hours }}h · 中位 {{ eff.median_hours }}h · P90 {{ eff.p90_hours }}h</div>
        <div ref="cEff" class="chart short"></div>
      </div>
      <div class="panel">
        <h4>驳回 / 退回原因归类</h4>
        <div ref="cReject" class="chart short"></div>
      </div>
      <div class="panel">
        <h4>考生画像（性别 / 年龄）</h4>
        <div class="pair">
          <div ref="cGender" class="chart mini"></div>
          <div ref="cAge" class="chart mini"></div>
        </div>
      </div>
    </div>

    <div class="screen-grid2">
      <div class="panel">
        <h4>报名提交时段分布</h4>
        <div ref="cHour" class="chart short"></div>
      </div>
      <div class="panel" style="grid-column: span 2">
        <h4>单位 / 院系 → 班级分布</h4>
        <div class="rank-list">
          <div v-for="o in orgClass" :key="o.name" class="org-item">
            <div class="pg-line">
              <span class="nm">{{ o.name }}</span>
              <span class="pg-bar"><i :style="{ width: pct(o.value, maxOrg) + '%' }"></i></span>
              <span class="vv">{{ o.value }}</span>
            </div>
            <div class="org-chips">
              <span v-for="c in o.children" :key="c.name" class="chip">{{ c.name }} · {{ c.value }}</span>
            </div>
          </div>
          <div v-if="!orgClass.length" class="pg-line"><span class="nm">暂无数据</span></div>
        </div>
      </div>
    </div>

    <div class="screen-grid2">
      <div class="panel"><h4>报考科目 / 职业排行 Top10</h4><div ref="cItem" class="chart"></div></div>
      <div class="panel">
        <h4>各批次审核进度</h4>
        <div class="rank-list">
          <div v-for="b in batchProgress" :key="b.exam_id" class="org-item">
            <div class="pg-line">
              <span class="nm">{{ b.name }}</span>
              <span class="pg-bar"><i :style="{ width: b.reviewed_rate + '%' }"></i></span>
              <span class="vv">{{ b.reviewed_rate }}%</span>
            </div>
            <div class="org-chips">
              <span class="chip">共 {{ b.total }}</span>
              <span class="chip">通过 {{ b.approved }}</span>
              <span class="chip">待审 {{ b.pending }}</span>
              <span class="chip">驳回 {{ b.rejected }}</span>
              <span class="chip">退回 {{ b.returned }}</span>
            </div>
          </div>
          <div v-if="!batchProgress.length" class="pg-line"><span class="nm">暂无数据</span></div>
        </div>
      </div>
      <div class="panel">
        <h4>审核员工作量</h4>
        <table class="mini-tb">
          <thead><tr><th>审核人</th><th style="text-align:right">件数</th>
            <th style="text-align:right">平均耗时</th></tr></thead>
          <tbody>
            <tr v-for="r in reviewerRank" :key="r.name">
              <td>{{ r.name }}</td>
              <td style="text-align:right">{{ r.count }}</td>
              <td style="text-align:right">{{ r.avg_hours }} h</td>
            </tr>
            <tr v-if="!reviewerRank.length"><td colspan="3">暂无审核流水</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="screen-sec">构成与动态</div>
    <div class="screen-grid2">
      <div class="panel"><h4>考点分布 Top8</h4><div ref="cSite" class="chart short"></div></div>
      <div class="panel"><h4>考试机构 Top8</h4><div ref="cOrg" class="chart short"></div></div>
      <div class="panel">
        <h4>必填项缺失（填报质量）</h4>
        <table class="mini-tb">
          <thead><tr><th>字段</th><th style="text-align:right">缺失</th>
            <th style="text-align:right">占比</th></tr></thead>
          <tbody>
            <tr v-for="c in completeness" :key="c.field + c.type">
              <td>{{ c.field }}</td>
              <td style="text-align:right">{{ c.missing }}</td>
              <td style="text-align:right">{{ c.rate }}%</td>
            </tr>
            <tr v-if="!completeness.length"><td colspan="3">必填项均已填写完整</td></tr>
          </tbody>
        </table>
        <div v-if="photo.missing" class="eff-hint">
          另有 {{ photo.missing }} 人未上传证件照（完整率 {{ photo.rate }}%）
        </div>
      </div>
    </div>

    <div class="screen-grid2">
      <div class="panel" style="grid-column: span 3">
        <h4>最新报名动态</h4>
        <div class="recent-row">
          <div v-for="r in recent" :key="r.app_type + '-' + r.id" class="open-item">
            <div class="n">{{ r.name }} · {{ r.exam_name }}</div>
            <div class="d">{{ r.created_at }} · {{ statusLabel(r.audit_status) }}</div>
          </div>
          <div v-if="!recent.length" class="open-item"><div class="n">暂无报名数据</div></div>
        </div>
      </div>
    </div>

    <div class="screen-foot">数据范围：{{ scopeLabel }} · 更新时间：{{ updatedAt }} · 每 {{ interval }} 秒自动刷新</div>
  </div>`,
  setup() {
    const k = ref({});
    const eff = ref({});
    const photo = ref({});
    const profile = ref({ gender: [], age: [] });
    const rejectReasons = ref([]);
    const batchProgress = ref([]);
    const orgClass = ref([]);
    const reviewerRank = ref([]);
    const completeness = ref([]);
    const openExams = ref([]);
    const recent = ref([]);
    const clock = ref('');
    const updatedAt = ref('');
    const scopeLabel = ref('');
    const interval = ref(30);
    const refs = {
      cTrend30: ref(null), cType: ref(null), cFunnel: ref(null),
      cRegion: ref(null), cItem: ref(null), cEff: ref(null),
      cReject: ref(null), cGender: ref(null), cAge: ref(null),
      cHour: ref(null), cSite: ref(null), cOrg: ref(null),
    };
    const instances = {};
    let timer = null;
    let clockTimer = null;

    const statusLabel = (s) => ({ pending: '待审核', approved: '已通过', rejected: '已驳回',
      returned: '已退回' }[s] || s);

    /* 迷你条形：宽度按最大值归一，避免小值只剩 1px 看不出来 */
    const maxOrg = ref(1);
    function pct(v, max) {
      if (!max) return 0;
      return Math.max(4, Math.round(v / max * 100));
    }

    function draw(key, option) {
      const el = refs[key].value;
      if (!el) return;
      instances[key] = instances[key] || echarts.init(el);
      instances[key].setOption(option, true);
    }

    function render(d) {
      const grad = new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: 'rgba(56,189,248,.75)' }, { offset: 1, color: 'rgba(56,189,248,.03)' }]);
      const grad2 = new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: 'rgba(74,222,128,.7)' }, { offset: 1, color: 'rgba(74,222,128,.03)' }]);

      // 近 30 天双线：报名量的柱 + 完成审核的折线，一眼看出是否积压
      draw('cTrend30', {
        grid: { left: 44, right: 20, top: 30, bottom: 34 },
        tooltip: { trigger: 'axis' },
        legend: { top: 0, textStyle: { color: '#9db8e8', fontSize: 11 },
          data: ['新增报名', '完成审核'] },
        xAxis: { type: 'category', data: (d.trend30 || []).map((x) => x.date), ...AXIS },
        yAxis: { type: 'value', ...AXIS },
        series: [
          { name: '新增报名', type: 'bar', data: (d.trend30 || []).map((x) => x.submitted),
            barMaxWidth: 14,
            itemStyle: { borderRadius: [3, 3, 0, 0],
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1,
                [{ offset: 0, color: '#38bdf8' }, { offset: 1, color: 'rgba(56,189,248,.15)' }]) } },
          { name: '完成审核', type: 'line', smooth: true, symbolSize: 5,
            data: (d.trend30 || []).map((x) => x.reviewed),
            lineStyle: { width: 2, color: '#4ade80' }, itemStyle: { color: '#4ade80' } },
        ],
      });
      draw('cType', {
        tooltip: { trigger: 'item' },
        legend: { bottom: 0, textStyle: { color: '#9db8e8' } },
        color: ['#38bdf8', '#a78bfa', '#4ade80'],
        series: [{
          type: 'pie', radius: ['40%', '64%'], center: ['50%', '44%'],
          data: d.type_pie, label: { color: '#d7e4ff', formatter: '{b}\n{c}' },
          itemStyle: { borderColor: '#0a162e', borderWidth: 2 },
        }],
      });
      draw('cFunnel', {
        tooltip: { trigger: 'item', formatter: '{b}: {c}' },
        color: ['#38bdf8', '#a78bfa', '#4ade80'],
        series: [{
          type: 'funnel', left: '12%', right: '12%', top: 20, bottom: 20,
          minSize: '32%', gap: 4,
          label: { color: '#e2ecff', formatter: '{b}: {c}' },
          data: d.funnel,
        }],
      });
      draw('cRegion', hbar(d.region_bar || [], '#1d4ed8', '#38bdf8', 86));
      draw('cItem', hbar(d.item_rank || [], '#6d28d9', '#c4b5fd', 140));
      draw('cSite', hbar(d.site_rank || [], '#0369a1', '#7dd3fc', 90));
      draw('cOrg', hbar(d.org_rank || [], '#0f766e', '#5eead4', 130));
      draw('cEff', vbar((d.efficiency || {}).buckets || [], '#1e3a8a', '#60a5fa'));
      draw('cReject', hbar(d.reject_reasons || [], '#7f1d1d', '#f87171', 96));
      draw('cAge', vbar((d.profile || {}).age || [], '#4c1d95', '#c4b5fd'));
      draw('cHour', {
        grid: { left: 40, right: 16, top: 20, bottom: 30 },
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', boundaryGap: false,
          data: (d.hourly || []).map((x) => x.name), ...AXIS,
          axisLabel: { color: '#9db8e8', fontSize: 10, interval: 3 } },
        yAxis: { type: 'value', ...AXIS },
        series: [{
          type: 'line', smooth: true, symbol: 'none',
          data: (d.hourly || []).map((x) => x.value),
          lineStyle: { width: 2, color: '#38bdf8' }, areaStyle: { color: grad },
        }],
      });
      draw('cGender', {
        tooltip: { trigger: 'item' },
        legend: { bottom: 0, textStyle: { color: '#9db8e8', fontSize: 11 } },
        color: ['#38bdf8', '#f472b6'],
        series: [{
          type: 'pie', radius: ['34%', '58%'], center: ['50%', '42%'],
          data: (d.profile || {}).gender || [],
          label: { color: '#d7e4ff', fontSize: 11, formatter: '{b} {c}' },
          itemStyle: { borderColor: '#0a162e', borderWidth: 2 },
        }],
      });
      Object.keys(instances).forEach((key) => instances[key].resize());
    }

    async function load() {
      try {
        const d = await api.get('/api/dashboard');
        k.value = d.kpi || {};
        eff.value = d.efficiency || {};
        photo.value = d.photo || {};
        profile.value = d.profile || { gender: [], age: [] };
        rejectReasons.value = d.reject_reasons || [];
        batchProgress.value = d.batch_progress || [];
        orgClass.value = d.org_class || [];
        reviewerRank.value = d.reviewer_rank || [];
        completeness.value = d.completeness || [];
        openExams.value = d.open_exams || [];
        recent.value = d.recent || [];
        updatedAt.value = d.updated_at || '';
        scopeLabel.value = d.scope_label || '';
        maxOrg.value = Math.max(1, ...orgClass.value.map((x) => x.value));
        await nextTick();
        render(d);
      } catch (e) { handleErr(e); }
    }

    function restart() {
      if (timer) clearInterval(timer);
      timer = setInterval(load, interval.value * 1000);
    }

    function onResize() { Object.keys(instances).forEach((key) => instances[key].resize()); }

    /* 全屏展示：大屏投放时用。ESC 即可退出。

       ⚠ 必须对「大屏容器本身」而不是 document.documentElement 请求全屏：
       对整篇文档全屏时侧边栏与顶栏仍在，达不到「全屏时只展示大屏」的效果。
       对元素全屏后，:fullscreen 伪类会把它铺满视口，天然遮挡外围布局。
       进出全屏都会改变可视尺寸，必须重算图表尺寸，否则画布留在旧大小上。 */
    const isFullscreen = ref(false);
    const rootEl = ref(null);
    function fullscreenEl() {
      // 取不到容器（如已卸载）时退回文档，避免按钮点了没反应
      return rootEl.value || document.documentElement;
    }
    function toggleFullscreen() {
      const el = fullscreenEl();
      try {
        if (!document.fullscreenElement) {
          const req = el.requestFullscreen || el.webkitRequestFullscreen;
          if (req) req.call(el);
        } else {
          const exit = document.exitFullscreen || document.webkitExitFullscreen;
          if (exit) exit.call(document);
        }
      } catch (e) { /* 浏览器拒绝时忽略，页面照常可用 */ }
    }
    function onFullscreenChange() {
      isFullscreen.value = !!document.fullscreenElement;
      // 全屏切换后布局尺寸变化，等一帧再重算，避免读到旧宽度
      setTimeout(onResize, 60);
    }

    onMounted(() => {
      load();
      restart();
      clockTimer = setInterval(() => {
        const d = new Date();
        const p = (n) => String(n).padStart(2, '0');
        clock.value = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
          `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
      }, 1000);
      window.addEventListener('resize', onResize);
      document.addEventListener('fullscreenchange', onFullscreenChange);
      document.addEventListener('webkitfullscreenchange', onFullscreenChange);
    });

    onBeforeUnmount(() => {
      if (timer) clearInterval(timer);
      if (clockTimer) clearInterval(clockTimer);
      window.removeEventListener('resize', onResize);
      document.removeEventListener('fullscreenchange', onFullscreenChange);
      document.removeEventListener('webkitfullscreenchange', onFullscreenChange);
      Object.keys(instances).forEach((key) => { instances[key].dispose(); delete instances[key]; });
    });

    return { k, eff, photo, profile, rejectReasons, batchProgress, orgClass, reviewerRank,
      completeness, openExams, recent, clock, updatedAt, scopeLabel, interval,
      load, restart, statusLabel, pct, maxOrg,
      isFullscreen, toggleFullscreen, rootEl, ...refs };
  },
};
