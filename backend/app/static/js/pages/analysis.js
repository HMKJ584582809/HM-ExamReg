/* 模块6：数据分析（指标 + 多维图表联动） */
import { api, handleErr } from '../api.js';

const { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } = Vue;

export const AnalysisPage = {
  template: `
  <div>
    <div class="page-head">
      <div>
        <h2>数据分析</h2>
        <div class="sub">
          报名总数、审核状态、批次 / 月度 / 地区（省→市）/ 考点 / 机构 / 班级 / 院系 / 职业 / 性别 / 学历多维统计
          <el-tag v-if="scopeLabel" size="small" type="info" style="margin-left:6px">{{ scopeLabel }}</el-tag>
        </div>
      </div>
      <el-button @click="load" :loading="loading">刷新</el-button>
    </div>

    <div class="card">
      <div class="filter-bar">
        <el-select v-model="query.exam_id" placeholder="考试批次（全部）" clearable filterable
                   style="width:300px">
          <el-option v-for="e in examOptions" :key="e.id" :label="e.label" :value="e.id" />
        </el-select>
        <el-select v-model="query.exam_type" placeholder="考试类型（全部）" clearable style="width:170px">
          <el-option v-for="t in types" :key="t.value" :label="t.label" :value="t.value" />
        </el-select>
        <el-date-picker v-model="dateRange" type="daterange" range-separator="至"
                        start-placeholder="开始日期" end-placeholder="结束日期"
                        value-format="YYYY-MM-DD" style="width:260px" />
        <el-button type="primary" @click="load">统计</el-button>
        <el-button @click="reset">重置</el-button>
      </div>
    </div>

    <div class="kpi-grid mb16">
      <div class="kpi c-blue">
        <div class="k-label">报名总数</div><div class="k-value">{{ s.total || 0 }}</div>
        <div class="k-sub">今日新增 {{ s.today || 0 }}</div>
      </div>
      <div class="kpi c-orange">
        <div class="k-label">待审核</div><div class="k-value">{{ s.pending || 0 }}</div>
        <div class="k-sub">占比 {{ s.pending_rate || 0 }}%</div>
      </div>
      <div class="kpi c-green">
        <div class="k-label">审核通过</div><div class="k-value">{{ s.approved || 0 }}</div>
        <div class="k-sub">通过率 {{ s.pass_rate || 0 }}%</div>
      </div>
      <div class="kpi c-red">
        <div class="k-label">已驳回</div><div class="k-value">{{ s.rejected || 0 }}</div>
        <div class="k-sub">驳回率 {{ s.reject_rate || 0 }}%</div>
      </div>
      <div class="kpi c-purple">
        <div class="k-label">已退回</div><div class="k-value">{{ s.returned || 0 }}</div>
        <div class="k-sub">待考生修改</div>
      </div>
      <div class="kpi c-blue">
        <div class="k-label">已审核</div><div class="k-value">{{ s.reviewed || 0 }}</div>
        <div class="k-sub">通过 + 驳回 + 退回</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span class="t">各批次报名量</span></div>
      <div ref="cExam" class="chart tall"></div>
    </div>

    <div class="card">
      <div class="card-title"><span class="t">按月报名趋势</span></div>
      <div ref="cMonth" class="chart"></div>
    </div>

    <el-row :gutter="16">
      <el-col :span="12">
        <div class="card"><div class="card-title"><span class="t">审核状态构成</span></div>
          <div ref="cStatus" class="chart"></div></div>
      </el-col>
      <el-col :span="12">
        <div class="card"><div class="card-title"><span class="t">考生性别构成</span></div>
          <div ref="cGender" class="chart"></div></div>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="12">
        <div class="card"><div class="card-title"><span class="t">地区分布 Top12（普通话出生省 / 计算机机构）</span></div>
          <div ref="cRegion" class="chart tall"></div></div>
      </el-col>
      <el-col :span="12">
        <div class="card"><div class="card-title"><span class="t">职业 / 报考科目排行 Top12</span></div>
          <div ref="cItem" class="chart tall"></div></div>
      </el-col>
    </el-row>

    <el-row :gutter="16" v-if="(charts.site_rank && charts.site_rank.length) || (charts.org_rank && charts.org_rank.length)">
      <el-col :span="12">
        <div class="card"><div class="card-title"><span class="t">考点报名量 Top12（计算机类）</span></div>
          <div ref="cSite" class="chart tall"></div></div>
      </el-col>
      <el-col :span="12">
        <div class="card"><div class="card-title"><span class="t">考试机构报名量 Top12（计算机类）</span></div>
          <div ref="cOrg" class="chart tall"></div></div>
      </el-col>
    </el-row>

    <el-row :gutter="16" v-if="charts.birth_tree && charts.birth_tree.length">
      <el-col :span="12">
        <div class="card"><div class="card-title"><span class="t">出生地 省 → 市 分布（普通话）</span></div>
          <div ref="cBirth" class="chart tall"></div></div>
      </el-col>
      <el-col :span="12">
        <div class="card"><div class="card-title"><span class="t">现居住地 省 → 市 分布（普通话）</span></div>
          <div ref="cLive" class="chart tall"></div></div>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="12">
        <div class="card"><div class="card-title"><span class="t">班级报名量 Top12</span></div>
          <div ref="cClass" class="chart tall"></div></div>
      </el-col>
      <el-col :span="12">
        <div class="card">
          <div class="card-title"><span class="t">院系报名量 Top12（普通话）</span></div>
          <div ref="cDept" class="chart tall"></div>
        </div>
      </el-col>
    </el-row>

    <div class="card" v-if="charts.edu_bar && charts.edu_bar.length">
      <div class="card-title"><span class="t">学历分布（计算机类）</span></div>
      <div ref="cEdu" class="chart"></div>
    </div>

    <div class="card" v-if="charts.ethnicity_bar && charts.ethnicity_bar.length">
      <div class="card-title"><span class="t">民族分布 Top10（普通话）</span></div>
      <div ref="cEth" class="chart"></div>
    </div>

    <!-- ───────────────── 需求4：深度分析 ───────────────── -->
    <div class="section-title mt16">深度分析</div>

    <div class="kpi-grid mb16">
      <div class="kpi c-blue">
        <div class="k-label">平均审核耗时</div>
        <div class="k-value">{{ eff.avg_hours || 0 }}<span class="k-unit">小时</span></div>
        <div class="k-sub">中位 {{ eff.median_hours || 0 }} · P90 {{ eff.p90_hours || 0 }}</div>
      </div>
      <div class="kpi c-green">
        <div class="k-label">证件照完整率</div>
        <div class="k-value">{{ photo.rate || 0 }}<span class="k-unit">%</span></div>
        <div class="k-sub">{{ photo.with_photo || 0 }} / {{ photo.total || 0 }} 人已上传</div>
      </div>
      <div class="kpi c-orange">
        <div class="k-label">待审核</div>
        <div class="k-value">{{ s.pending || 0 }}</div>
        <div class="k-sub">最久一批已积压</div>
      </div>
      <div class="kpi c-purple">
        <div class="k-label">平均批次进度</div>
        <div class="k-value">{{ avgReviewedRate }}<span class="k-unit">%</span></div>
        <div class="k-sub">各批次已审占比均值</div>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :span="12">
        <div class="card">
          <div class="card-title"><span class="t">考生年龄结构</span>
            <span class="muted fs-12 ml8">按证件号出生年份推算，{{ profile.age_known || 0 }} 条可识别</span>
          </div>
          <div ref="cAge" class="chart"></div>
        </div>
      </el-col>
      <el-col :span="12">
        <div class="card">
          <div class="card-title"><span class="t">审核时效分布</span></div>
          <div ref="cHours" class="chart"></div>
        </div>
      </el-col>
    </el-row>

    <div class="card">
      <div class="card-title"><span class="t">近 30 天 报名 / 审核 趋势</span></div>
      <div ref="cDaily" class="chart"></div>
    </div>

    <el-row :gutter="16">
      <el-col :span="14">
        <div class="card">
          <div class="card-title"><span class="t">单位 / 院系 → 班级 分布</span></div>
          <div ref="cMatrix" class="chart tall"></div>
        </div>
      </el-col>
      <el-col :span="10">
        <div class="card">
          <div class="card-title"><span class="t">驳回 / 退回原因归类</span></div>
          <el-table :data="deep.reject_reasons || []" size="small" border max-height="320">
            <el-table-column prop="name" label="原因" width="110" />
            <el-table-column prop="value" label="条数" width="70" align="right" />
            <el-table-column label="典型意见" min-width="180">
              <template #default="{ row }">
                <div v-for="(x, i) in (row.samples || [])" :key="i" class="fs-12 lh-17">
                  · {{ x }}
                </div>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!(deep.reject_reasons || []).length" description="暂无驳回记录"
                    :image-size="60" />
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="12">
        <div class="card">
          <div class="card-title"><span class="t">各批次审核进度</span></div>
          <el-table :data="deep.batch_progress || []" size="small" border max-height="320">
            <el-table-column prop="name" label="批次" min-width="160" show-overflow-tooltip />
            <el-table-column prop="total" label="报名" width="70" align="right" />
            <el-table-column prop="approved" label="通过" width="70" align="right" />
            <el-table-column prop="pending" label="待审" width="70" align="right" />
            <el-table-column label="已审" width="90">
              <template #default="{ row }">
                <el-progress :percentage="row.reviewed_rate" :stroke-width="10"
                             :text-inside="true" />
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-col>
      <el-col :span="12">
        <div class="card">
          <div class="card-title"><span class="t">填报完整度（必填字段缺失）</span></div>
          <el-table :data="deep.completeness || []" size="small" border max-height="320">
            <el-table-column prop="field" label="字段" min-width="120" />
            <el-table-column prop="missing" label="缺失" width="70" align="right" />
            <el-table-column label="缺失率" width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="row.rate >= 20 ? 'danger' : 'warning'">
                  {{ row.rate }}%
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!(deep.completeness || []).length" description="必填项无缺失，填报质量良好"
                    :image-size="60" />
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="12">
        <div class="card">
          <div class="card-title"><span class="t">审核员工作量</span></div>
          <el-table :data="deep.reviewer_rank || []" size="small" border max-height="300">
            <el-table-column prop="name" label="审核员" min-width="140" />
            <el-table-column prop="count" label="处理" width="80" align="right" />
            <el-table-column label="平均耗时" width="100" align="right">
              <template #default="{ row }">{{ row.avg_hours }} 小时</template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!(deep.reviewer_rank || []).length" description="暂无审核记录"
                    :image-size="60" />
        </div>
      </el-col>
      <el-col :span="12">
        <div class="card">
          <div class="card-title"><span class="t">证件照完整率（按单位 / 院系）</span></div>
          <el-table :data="photo.by_org || []" size="small" border max-height="300">
            <el-table-column prop="name" label="单位 / 院系" min-width="140" show-overflow-tooltip />
            <el-table-column label="已上传 / 总数" width="120" align="right">
              <template #default="{ row }">{{ row.with_photo }} / {{ row.total }}</template>
            </el-table-column>
            <el-table-column label="完整率" width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="row.rate >= 90 ? 'success' : (row.rate >= 60 ? 'warning' : 'danger')">
                  {{ row.rate }}%
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-col>
    </el-row>

    <div class="card" v-if="(profile.occupation || []).length || (profile.education || []).length">
      <div class="card-title"><span class="t">职业 / 学历构成</span></div>
      <el-row :gutter="16">
        <el-col :span="12" v-if="(profile.occupation || []).length">
          <div ref="cOcc" class="chart"></div>
        </el-col>
        <el-col :span="12" v-if="(profile.education || []).length">
          <div ref="cEdu2" class="chart"></div>
        </el-col>
      </el-row>
    </div>
  </div>`,
  setup() {
    const query = reactive({ exam_id: '', exam_type: '' });
    const dateRange = ref(null);
    const examOptions = ref([]);
    // 考试类型下拉与「考试类型管理」同步，含管理员新增的自定义类型
    const types = ref([{ value: 'computer', label: '计算机类考试' },
      { value: 'mandarin', label: '普通话水平测试' }]);
    const s = ref({});
    const charts = ref({});
    const loading = ref(false);
    const refs = {
      cExam: ref(null), cMonth: ref(null), cStatus: ref(null), cGender: ref(null),
      cRegion: ref(null), cItem: ref(null), cEdu: ref(null), cEth: ref(null),
      cSite: ref(null), cOrg: ref(null), cBirth: ref(null), cLive: ref(null),
      cClass: ref(null), cDept: ref(null),
      // 需求4 新增
      cAge: ref(null), cHours: ref(null), cDaily: ref(null), cMatrix: ref(null),
      cOcc: ref(null), cEdu2: ref(null),
    };
    const instances = {};
    const scopeLabel = ref('');
    const deep = ref({});
    const profile = ref({});
    const eff = ref({});
    const photo = ref({});
    const avgReviewedRate = computed(() => {
      const b = deep.value.batch_progress || [];
      if (!b.length) return 0;
      return Math.round(b.reduce((a, x) => a + (x.reviewed_rate || 0), 0) / b.length);
    });

    function hbar(key, data, color, labelWidth = 130) {
      if (!refs[key].value || !data || !data.length) return;
      draw(key, {
        grid: { left: labelWidth + 20, right: 44, top: 16, bottom: 24 },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'value' },
        yAxis: {
          type: 'category', inverse: true, data: data.map((x) => x.name),
          axisLabel: { fontSize: 11, width: labelWidth, overflow: 'truncate' },
        },
        series: [{
          type: 'bar', data: data.map((x) => x.value), barMaxWidth: 16,
          itemStyle: { borderRadius: [0, 4, 4, 0], color: color },
          label: { show: true, position: 'right', fontSize: 11 },
        }],
      });
    }

    /* 省 → 市 两级柱状：外层按省，内层按市堆叠显示 */
    function treeBar(key, tree) {
      if (!refs[key].value || !tree || !tree.length) return;
      const provinces = tree.map((x) => x.province);
      const cityNames = [];
      tree.forEach((n) => (n.cities || []).forEach((c) => {
        if (!cityNames.includes(c.name)) cityNames.push(c.name);
      }));
      const palette = ['#1f6feb', '#7c3aed', '#16a34a', '#e6a23c', '#dc2626', '#0ea5e9',
        '#f472b6', '#14b8a6', '#a3e635', '#fb923c', '#8b5cf6', '#38bdf8'];
      const series = cityNames.map((name, i) => ({
        name, type: 'bar', stack: 'city', barMaxWidth: 30,
        itemStyle: { color: palette[i % palette.length] },
        data: tree.map((n) => {
          const c = (n.cities || []).find((x) => x.name === name);
          return c ? c.value : 0;
        }),
      }));
      // 未细分到市的余量
      const rest = tree.map((n) => n.total - (n.cities || []).reduce((a, b) => a + b.value, 0));
      if (rest.some((v) => v > 0)) {
        series.push({ name: '其他/未细分', type: 'bar', stack: 'city', barMaxWidth: 30,
          itemStyle: { color: '#cbd5e1' }, data: rest });
      }
      draw(key, {
        grid: { left: 50, right: 24, top: 30, bottom: 70 },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 10 } },
        xAxis: { type: 'category', data: provinces, axisLabel: { interval: 0, rotate: 20, fontSize: 11 } },
        yAxis: { type: 'value', name: '人数' },
        series,
      });
    }

    function draw(key, option) {
      const el = refs[key].value;
      if (!el) return;
      instances[key] = instances[key] || echarts.init(el);
      instances[key].setOption(option, true);
    }

    function render() {
      const d = charts.value;
      const palette = ['#1f6feb', '#7c3aed', '#16a34a', '#e6a23c', '#dc2626', '#0ea5e9',
        '#f472b6', '#14b8a6', '#a3e635', '#fb923c', '#8b5cf6', '#38bdf8'];
      draw('cExam', {
        grid: { left: 50, right: 24, top: 30, bottom: 70 },
        tooltip: { trigger: 'axis' },
        xAxis: {
          type: 'category', data: (d.exam_bar || []).map((x) => x.name),
          axisLabel: { interval: 0, rotate: 22, fontSize: 11 },
        },
        yAxis: { type: 'value', name: '报名人数' },
        series: [{
          type: 'bar', data: (d.exam_bar || []).map((x) => x.count), barMaxWidth: 46,
          itemStyle: { borderRadius: [5, 5, 0, 0], color: '#1f6feb' },
          label: { show: true, position: 'top', fontSize: 11 },
        }],
      });
      draw('cMonth', {
        grid: { left: 50, right: 24, top: 30, bottom: 40 },
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: (d.month_line || []).map((x) => x.month) },
        yAxis: { type: 'value', name: '报名人数' },
        series: [{
          type: 'line', smooth: true, data: (d.month_line || []).map((x) => x.count),
          areaStyle: { opacity: .18 }, lineStyle: { width: 3, color: '#1f6feb' },
          itemStyle: { color: '#1f6feb' }, symbolSize: 7,
        }],
      });
      draw('cStatus', {
        tooltip: { trigger: 'item' },
        legend: { bottom: 0 },
        series: [{
          type: 'pie', radius: ['42%', '66%'], center: ['50%', '44%'],
          data: d.status_pie || [], label: { formatter: '{b}: {c}' },
          itemStyle: { borderColor: '#fff', borderWidth: 2 },
        }],
      });
      draw('cGender', {
        tooltip: { trigger: 'item' },
        legend: { bottom: 0 },
        color: ['#1f6feb', '#f472b6'],
        series: [{
          type: 'pie', radius: '62%', center: ['50%', '44%'],
          data: d.gender_pie || [], label: { formatter: '{b}: {c} ({d}%)' },
        }],
      });
      draw('cRegion', {
        grid: { left: 100, right: 40, top: 20, bottom: 30 },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'value' },
        yAxis: {
          type: 'category', inverse: true,
          data: (d.region_bar || []).map((x) => x.name),
          axisLabel: { fontSize: 11, width: 92, overflow: 'truncate' },
        },
        series: [{
          type: 'bar', data: (d.region_bar || []).map((x) => x.value), barMaxWidth: 16,
          itemStyle: { borderRadius: [0, 4, 4, 0], color: '#0ea5e9' },
          label: { show: true, position: 'right', fontSize: 11 },
        }],
      });
      draw('cItem', {
        grid: { left: 150, right: 40, top: 20, bottom: 30 },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'value' },
        yAxis: {
          type: 'category', inverse: true,
          data: (d.item_rank || []).map((x) => x.name),
          axisLabel: { fontSize: 11, width: 142, overflow: 'truncate' },
        },
        series: [{
          type: 'bar', data: (d.item_rank || []).map((x) => x.value), barMaxWidth: 16,
          itemStyle: { borderRadius: [0, 4, 4, 0], color: '#7c3aed' },
          label: { show: true, position: 'right', fontSize: 11 },
        }],
      });
      if (refs.cEdu.value) {
        draw('cEdu', {
          grid: { left: 50, right: 24, top: 30, bottom: 40 },
          tooltip: { trigger: 'axis' },
          xAxis: { type: 'category', data: (d.edu_bar || []).map((x) => x.name) },
          yAxis: { type: 'value' },
          series: [{
            type: 'bar', data: (d.edu_bar || []).map((x) => x.value), barMaxWidth: 44,
            itemStyle: { borderRadius: [5, 5, 0, 0], color: '#16a34a' },
            label: { show: true, position: 'top' },
          }],
        });
      }
      if (refs.cEth.value) {
        draw('cEth', {
          grid: { left: 50, right: 24, top: 30, bottom: 40 },
          tooltip: { trigger: 'axis' },
          xAxis: { type: 'category', data: (d.ethnicity_bar || []).map((x) => x.name),
                   axisLabel: { interval: 0, rotate: 18, fontSize: 11 } },
          yAxis: { type: 'value' },
          series: [{
            type: 'bar', data: (d.ethnicity_bar || []).map((x) => x.value), barMaxWidth: 44,
            itemStyle: { borderRadius: [5, 5, 0, 0], color: '#7c3aed' },
            label: { show: true, position: 'top' },
          }],
        });
      }
      hbar('cSite', d.site_rank, '#0ea5e9', 120);
      hbar('cOrg', d.org_rank, '#f59e0b', 150);
      treeBar('cBirth', d.birth_tree);
      treeBar('cLive', d.live_tree);
      hbar('cClass', d.class_rank, '#16a34a', 120);
      hbar('cDept', d.dept_rank, '#7c3aed', 140);
      renderDeep();
      Object.keys(instances).forEach((k) => instances[k].resize());
    }

    /* ---- 需求4：深度分析图表 ---- */
    function vbar(key, data, color, rotate = 0) {
      if (!refs[key] || !refs[key].value || !data || !data.length) return;
      draw(key, {
        grid: { left: 46, right: 24, top: 26, bottom: rotate ? 60 : 40 },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: {
          type: 'category', data: data.map((x) => x.name),
          axisLabel: { fontSize: 11, interval: 0, rotate },
        },
        yAxis: { type: 'value' },
        series: [{
          type: 'bar', data: data.map((x) => x.value), barMaxWidth: 40,
          itemStyle: { borderRadius: [5, 5, 0, 0], color },
          label: { show: true, position: 'top', fontSize: 11 },
        }],
      });
    }

    function renderDeep() {
      const p = profile.value;
      vbar('cAge', p.age, '#0ea5e9', 18);
      vbar('cHours', (eff.value || {}).buckets, '#e6a23c', 18);

      const days = ((deep.value || {}).trend || {}).days || [];
      if (refs.cDaily.value && days.length) {
        draw('cDaily', {
          grid: { left: 46, right: 24, top: 34, bottom: 46 },
          tooltip: { trigger: 'axis' },
          legend: { bottom: 0, textStyle: { fontSize: 11 } },
          xAxis: { type: 'category', data: days.map((x) => x.date),
                   axisLabel: { fontSize: 10, interval: 2 } },
          yAxis: { type: 'value', name: '条数' },
          series: [
            { name: '新增报名', type: 'line', smooth: true, data: days.map((x) => x.submitted),
              areaStyle: { opacity: .16 }, lineStyle: { width: 3, color: '#1f6feb' },
              itemStyle: { color: '#1f6feb' }, symbolSize: 5 },
            { name: '完成审核', type: 'line', smooth: true, data: days.map((x) => x.reviewed),
              lineStyle: { width: 3, color: '#16a34a' }, itemStyle: { color: '#16a34a' },
              symbolSize: 5 },
          ],
        });
      }

      // 单位/院系 → 班级：横向堆叠（复用省→市的画法）
      const tree = (deep.value || {}).org_class || [];
      if (refs.cMatrix.value && tree.length) {
        const names = tree.map((x) => x.name);
        const cls = [];
        tree.forEach((n) => (n.children || []).forEach((c) => {
          if (!cls.includes(c.name)) cls.push(c.name);
        }));
        const palette = ['#1f6feb', '#7c3aed', '#16a34a', '#e6a23c', '#dc2626', '#0ea5e9',
          '#f472b6', '#14b8a6', '#a3e635', '#fb923c'];
        const series = cls.map((name, i) => ({
          name, type: 'bar', stack: 'c', barMaxWidth: 26,
          itemStyle: { color: palette[i % palette.length] },
          data: tree.map((n) => {
            const c = (n.children || []).find((x) => x.name === name);
            return c ? c.value : 0;
          }),
        }));
        draw('cMatrix', {
          grid: { left: 110, right: 24, top: 30, bottom: 24 },
          tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
          xAxis: { type: 'value' },
          yAxis: {
            type: 'category', inverse: true, data: names,
            axisLabel: { fontSize: 11, width: 100, overflow: 'truncate' },
          },
          series,
        });
      }

      if (refs.cOcc.value && (p.occupation || []).length) {
        hbar('cOcc', p.occupation, '#7c3aed', 150);
      }
      if (refs.cEdu2.value && (p.education || []).length) {
        vbar('cEdu2', p.education, '#16a34a', 18);
      }
    }

    async function load() {
      loading.value = true;
      try {
        const qs = new URLSearchParams();
        if (query.exam_id) qs.append('exam_id', query.exam_id);
        if (query.exam_type) qs.append('exam_type', query.exam_type);
        if (dateRange.value && dateRange.value.length === 2) {
          qs.append('date_from', dateRange.value[0]);
          qs.append('date_to', dateRange.value[1]);
        }
        const [d, dd] = await Promise.all([
          api.get('/api/analysis/charts?' + qs.toString()),
          api.get('/api/analysis/deep?' + qs.toString()),
        ]);
        charts.value = d;
        s.value = d.summary;
        scopeLabel.value = d.scope_label || '';
        deep.value = dd || {};
        profile.value = (dd || {}).profile || {};
        eff.value = (dd || {}).efficiency || {};
        photo.value = (dd || {}).photo || {};
        await nextTick();
        render();
      } catch (e) { handleErr(e); } finally { loading.value = false; }
    }

    function reset() {
      query.exam_id = ''; query.exam_type = ''; dateRange.value = null; load();
    }

    function onResize() { Object.keys(instances).forEach((k) => instances[k].resize()); }

    async function loadTypes() {
      try {
        const d = await api.get('/api/exam-types/options');
        if (d.list && d.list.length) types.value = d.list;
      } catch (e) { /* 下拉加载失败时沿用内置两种类型 */ }
    }

    onMounted(async () => {
      try { examOptions.value = await api.get('/api/analysis/exam-options'); } catch (e) { /* ignore */ }
      await loadTypes();
      await load();
      window.addEventListener('resize', onResize);
    });
    onBeforeUnmount(() => {
      window.removeEventListener('resize', onResize);
      Object.keys(instances).forEach((k) => { instances[k].dispose(); delete instances[k]; });
    });

    return { query, dateRange, examOptions, types, s, charts, loading, load, reset, scopeLabel,
      deep, profile, eff, photo, avgReviewedRate,
      ...refs };
  },
};
