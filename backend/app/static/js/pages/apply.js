/* 模块3：考试报名（按考试类型渲染 13 / 20 字段动态表单） */
import { api, handleErr, msg } from '../api.js';
import { loadDicts, regionIndex, store } from '../store.js';

const { ref, reactive, computed, onMounted, watch } = Vue;

export const ApplyPage = {
  template: `
  <div v-loading="loading">
    <div class="page-head">
      <div>
        <h2>{{ exam ? exam.name : '考试报名' }}</h2>
        <div class="sub" v-if="exam">
          <span class="type-badge" :class="{ md: exam.exam_type_base === 'mandarin', gn: exam.exam_type_base === 'generic' }">{{ exam.exam_type_label }}</span>
          &nbsp;报名时间：{{ exam.signup_start_at }} 至 {{ exam.signup_end_at }}
        </div>
      </div>
      <el-button @click="$router.back()">返回</el-button>
    </div>

    <el-alert v-if="editMode" type="warning" :closable="false" show-icon class="mb16"
              title="您正在修改已提交的报名信息，保存后将重新进入待审核状态。" />
    <el-alert v-else type="info" :closable="false" show-icon class="mb16"
              :title="'本次报名共 ' + fieldCount + ' 个采集字段，带 * 为必填项。' + tipText" />

    <div class="card" v-if="exam">
      <!-- ===================== 计算机类（含基于该模板的自定义类型） ===================== -->
      <el-form v-if="exam.exam_type_base === 'computer'" :model="form" :rules="rules" ref="formRef"
               label-width="140px" label-position="right">
        <el-row :gutter="18">
          <el-col :span="12" v-if="on('org_code')">
            <el-form-item :label="lb('org_code','考试机构编码')" prop="org_code">
              <el-select v-model="form.org_code" filterable placeholder="请选择考试机构" style="width:100%"
                         @change="onOrgChange">
                <el-option v-for="o in store.dicts.orgs" :key="o.code"
                           :label="o.code + ' - ' + o.name" :value="o.code" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('org_code')">
            <el-form-item label="机构名称">
              <el-input :model-value="orgName" disabled placeholder="选择机构编码后自动带出" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('exam_site_code')">
            <el-form-item :label="lb('exam_site_code','考点编码')" prop="exam_site_code">
              <el-input v-model="form.exam_site_code" placeholder="如 110101" maxlength="12" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('name')">
            <el-form-item :label="lb('name','姓名')" prop="name">
              <el-input v-model="form.name" placeholder="考生姓名" maxlength="50" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('gender')">
            <el-form-item :label="lb('gender','性别')" prop="gender">
              <el-radio-group v-model="form.gender">
                <el-radio value="男">男</el-radio>
                <el-radio value="女">女</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('id_type')">
            <el-form-item :label="lb('id_type','证件类型')" prop="id_type">
              <el-select v-model="form.id_type" style="width:100%">
                <el-option v-for="t in store.dicts.idTypes" :key="t.code"
                           :label="t.code + ' - ' + t.name" :value="t.code" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('id_number')">
            <el-form-item :label="lb('id_number','证件号码')" prop="id_number">
              <el-input v-model="form.id_number" placeholder="请输入证件号码" maxlength="30" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('subject')">
            <el-form-item :label="lb('subject','报考科目')" prop="subject">
              <el-select v-model="form.subject" filterable placeholder="请选择报考科目" style="width:100%">
                <el-option v-for="s in store.dicts.subjects" :key="s" :label="s" :value="s" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('school')">
            <el-form-item :label="lb('school','就读或者毕业院校')" prop="school">
              <el-input v-model="form.school" maxlength="100" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('class_name')">
            <el-form-item :label="lb('class_name','班级')" prop="class_name">
              <el-input v-model="form.class_name" maxlength="50" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('education')">
            <el-form-item :label="lb('education','学历')" prop="education">
              <el-select v-model="form.education" filterable allow-create default-first-option
                         placeholder="选择或输入学历" style="width:100%">
                <el-option v-for="e in store.dicts.educations" :key="e" :label="e" :value="e" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('phone')">
            <el-form-item :label="lb('phone','手机号码')" prop="phone">
              <el-input v-model="form.phone" maxlength="11" placeholder="11 位数字" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('email')">
            <el-form-item :label="lb('email','Email')" prop="email">
              <el-input v-model="form.email" placeholder="选填" />
            </el-form-item>
          </el-col>
          <el-col :span="24" v-if="on('address')">
            <el-form-item :label="lb('address','通讯地址')" prop="address">
              <el-input v-model="form.address" maxlength="255" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>

      <!-- ===================== 普通话 ===================== -->
      <el-form v-else-if="exam.exam_type_base === 'mandarin'" :model="form" :rules="rules" ref="formRef" label-width="140px" label-position="right">
        <el-row :gutter="18">
          <el-col :span="12" v-if="on('name')">
            <el-form-item :label="lb('name','考生姓名')" prop="name">
              <el-input v-model="form.name" maxlength="50" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('gender')">
            <el-form-item :label="lb('gender','考生性别')" prop="gender">
              <el-radio-group v-model="form.gender">
                <el-radio value="男">男</el-radio><el-radio value="女">女</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('ethnicity')">
            <el-form-item :label="lb('ethnicity','考生民族')" prop="ethnicity">
              <el-select v-model="form.ethnicity" filterable allow-create default-first-option
                         placeholder="请选择民族" style="width:100%">
                <el-option v-for="e in store.dicts.ethnicities" :key="e" :label="e" :value="e" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('id_type')">
            <el-form-item :label="lb('id_type','证件类型')" prop="id_type">
              <el-select v-model="form.id_type" style="width:100%">
                <el-option v-for="t in store.dicts.idTypes" :key="t.code"
                           :label="t.code + ' - ' + t.name" :value="t.code" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('id_number')">
            <el-form-item :label="lb('id_number','证件编号')" prop="id_number">
              <el-input v-model="form.id_number" maxlength="30" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('occupation')">
            <el-form-item :label="lb('occupation','从事职业')" prop="occupation">
              <el-select v-model="form.occupation" filterable placeholder="请选择从事职业" style="width:100%">
                <el-option v-for="o in store.dicts.occupations" :key="o" :label="o" :value="o" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('employer')">
            <el-form-item :label="lb('employer','所在单位')" prop="employer">
              <el-input v-model="form.employer" maxlength="100" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('phone')">
            <el-form-item :label="lb('phone','联系电话')" prop="phone">
              <el-input v-model="form.phone" maxlength="11" placeholder="11 位数字" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('student_no')">
            <el-form-item :label="lb('student_no','考生学号')" prop="student_no">
              <el-input v-model="form.student_no" maxlength="30" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('class_name')">
            <el-form-item :label="lb('class_name','考生班级')" prop="class_name">
              <el-input v-model="form.class_name" maxlength="50" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('department')">
            <el-form-item :label="lb('department','考生院系')" prop="department">
              <el-select v-model="form.department" filterable allow-create
                         default-first-option placeholder="选择或输入二级学院" style="width:100%">
                <el-option v-for="c in store.dicts.colleges" :key="c" :label="c" :value="c" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('postcode')">
            <el-form-item :label="lb('postcode','邮政编码')" prop="postcode">
              <el-input v-model="form.postcode" maxlength="6" placeholder="6 位数字" />
            </el-form-item>
          </el-col>
          <el-col :span="24" v-if="on('contact_address')">
            <el-form-item :label="lb('contact_address','联系地址')" prop="contact_address">
              <el-input v-model="form.contact_address" maxlength="255" />
            </el-form-item>
          </el-col>
          <el-col :span="24" v-if="on('mail_address')">
            <el-form-item :label="lb('mail_address','邮寄地址')" prop="mail_address">
              <el-input v-model="form.mail_address" maxlength="255" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider v-if="on('birth_province')" content-position="left">
          出生所在地（省 / 市 / 县区三级联动）
        </el-divider>
        <el-row :gutter="18">
          <el-col :span="8" v-if="on('birth_province')">
            <el-form-item :label="lb('birth_province','出生所在省')" prop="birth_province" label-width="130px">
              <el-select v-model="form.birth_province" filterable placeholder="请选择省"
                         style="width:100%" @change="onProvinceChange('birth')">
                <el-option v-for="p in provinces" :key="p" :label="p" :value="p" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8" v-if="on('birth_city')">
            <el-form-item :label="lb('birth_city','出生所在城市')" prop="birth_city" label-width="130px">
              <el-select v-if="birthCities.length" v-model="form.birth_city" filterable
                         placeholder="请选择市" style="width:100%" @change="onCityChange('birth')">
                <el-option v-for="c in birthCities" :key="c" :label="c" :value="c" />
              </el-select>
              <el-input v-else :model-value="form.birth_province ? '该地区无需填写' : ''" disabled />
            </el-form-item>
          </el-col>
          <el-col :span="8" v-if="on('birth_county')">
            <el-form-item :label="lb('birth_county','出生所在县(区)')" prop="birth_county" label-width="130px">
              <el-select v-if="birthCounties.length" v-model="form.birth_county" filterable
                         placeholder="请选择县(区)" style="width:100%">
                <el-option v-for="c in birthCounties" :key="c" :label="c" :value="c" />
              </el-select>
              <el-input v-else :model-value="form.birth_city && !birthCounties.length ? '该地区无需填写' : ''"
                        disabled />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider v-if="on('live_province')" content-position="left">
          现居住地（省 / 市 / 县区三级联动）
        </el-divider>
        <el-row :gutter="18">
          <el-col :span="8" v-if="on('live_province')">
            <el-form-item :label="lb('live_province','现居住省')" prop="live_province" label-width="130px">
              <el-select v-model="form.live_province" filterable placeholder="请选择省"
                         style="width:100%" @change="onProvinceChange('live')">
                <el-option v-for="p in provinces" :key="p" :label="p" :value="p" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8" v-if="on('live_city')">
            <el-form-item :label="lb('live_city','现居住城市')" prop="live_city" label-width="130px">
              <el-select v-if="liveCities.length" v-model="form.live_city" filterable
                         placeholder="请选择市" style="width:100%" @change="onCityChange('live')">
                <el-option v-for="c in liveCities" :key="c" :label="c" :value="c" />
              </el-select>
              <el-input v-else :model-value="form.live_province ? '该地区无需填写' : ''" disabled />
            </el-form-item>
          </el-col>
          <el-col :span="8" v-if="on('live_county')">
            <el-form-item :label="lb('live_county','现居住县(区)')" prop="live_county" label-width="130px">
              <el-select v-if="liveCounties.length" v-model="form.live_county" filterable
                         placeholder="请选择县(区)" style="width:100%">
                <el-option v-for="c in liveCounties" :key="c" :label="c" :value="c" />
              </el-select>
              <el-input v-else :model-value="form.live_city && !liveCounties.length ? '该地区无需填写' : ''"
                        disabled />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>

      <!-- ============ 通用模板：8 个核心字段 + 管理员自定义字段 ============ -->
      <el-form v-else :model="form" :rules="rules" ref="formRef" label-width="140px" label-position="right">
        <el-row :gutter="18">
          <el-col :span="12" v-if="on('name')">
            <el-form-item :label="lb('name','姓名')" prop="name">
              <el-input v-model="form.name" placeholder="考生姓名" maxlength="50" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('gender')">
            <el-form-item :label="lb('gender','性别')" prop="gender">
              <el-radio-group v-model="form.gender">
                <el-radio value="男">男</el-radio><el-radio value="女">女</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('id_type')">
            <el-form-item :label="lb('id_type','证件类型')" prop="id_type">
              <el-select v-model="form.id_type" style="width:100%">
                <el-option v-for="t in store.dicts.idTypes" :key="t.code"
                           :label="t.code + ' - ' + t.name" :value="t.code" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('id_number')">
            <el-form-item :label="lb('id_number','证件号码')" prop="id_number">
              <el-input v-model="form.id_number" placeholder="请输入证件号码" maxlength="30" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('phone')">
            <el-form-item :label="lb('phone','手机号码')" prop="phone">
              <el-input v-model="form.phone" maxlength="11" placeholder="11 位数字" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('email')">
            <el-form-item :label="lb('email','电子邮箱')" prop="email">
              <el-input v-model="form.email" placeholder="选填" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('class_name')">
            <el-form-item :label="lb('class_name','班级')" prop="class_name">
              <el-input v-model="form.class_name" maxlength="50" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="on('college')">
            <el-form-item :label="lb('college','院系')" prop="college">
              <el-select v-model="form.college" filterable allow-create
                         default-first-option placeholder="选择或输入二级学院" style="width:100%">
                <el-option v-for="c in store.dicts.colleges" :key="c" :label="c" :value="c" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider v-if="customMeta.length" content-position="left">
          自定义字段（由管理员在该考试类型中配置）
        </el-divider>
        <el-row :gutter="18">
          <el-col :span="12" v-for="f in customMeta" :key="f.key">
            <el-form-item :label="f.label" :prop="'extra.' + f.key">
              <el-select v-if="f.type === 'select'" v-model="form.extra[f.key]"
                         :placeholder="f.placeholder || '请选择'" style="width:100%">
                <el-option v-for="o in f.options" :key="o" :label="o" :value="o" />
              </el-select>
              <el-select v-else-if="f.type === 'multiselect'" v-model="form.extra[f.key]"
                         multiple :placeholder="f.placeholder || '可多选'" style="width:100%">
                <el-option v-for="o in f.options" :key="o" :label="o" :value="o" />
              </el-select>
              <el-date-picker v-else-if="f.type === 'date'" v-model="form.extra[f.key]"
                              type="date" value-format="YYYY-MM-DD"
                              :placeholder="f.placeholder || '请选择日期'" style="width:100%" />
              <el-input v-else-if="f.type === 'textarea'" v-model="form.extra[f.key]"
                        type="textarea" :rows="3" maxlength="500"
                        :placeholder="f.placeholder || '请填写'" />
              <el-input v-else v-model="form.extra[f.key]" maxlength="200"
                        :placeholder="f.placeholder || (f.type === 'number' ? '请填写数字' : '请填写')" />
            </el-form-item>
          </el-col>
        </el-row>
        <div class="hint" v-if="!customMeta.length">
          该考试类型尚未配置自定义字段，可在「考试类型管理」中为其添加。
        </div>
      </el-form>

      <div class="center mt16">
        <el-button size="large" @click="$router.back()">取消</el-button>
        <el-button size="large" type="primary" :loading="saving" @click="submit">
          {{ editMode ? '保存并重新提交' : '提交报名' }}
        </el-button>
      </div>
    </div>
  </div>`,
  setup() {
    const route = VueRouter.useRoute();
    const router = VueRouter.useRouter();
    const examId = parseInt(route.params.examId, 10);
    const appId = route.query.appId ? parseInt(route.query.appId, 10) : null;
    const exam = ref(null);
    const loading = ref(true);
    const saving = ref(false);
    const formRef = ref(null);
    // extra：通用模板的自定义字段值（{字段标识: 值}），与固定字段分开提交
    // employer 先占位：默认值要在渲染前就有 key，否则 el-form-item 拿不到初始值
    const form = reactive({ exam_id: examId, extra: {}, employer: '' });
    const editMode = computed(() => !!appId);

    const PHONE = /^1[3-9]\d{9}$/;

    // 居民身份证合法性校验（GB 11643：省份代码 + 出生日期 + 校验位）
    const ID_PROV = new Set(['11', '12', '13', '14', '15', '21', '22', '23', '31', '32',
      '33', '34', '35', '36', '37', '41', '42', '43', '44', '45', '46', '50', '51', '52',
      '53', '54', '61', '62', '63', '64', '65', '71', '81', '82', '91']);
    const ID_W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2];
    const ID_C = ['1', '0', 'X', '9', '8', '7', '6', '5', '4', '3', '2'];
    const ID_DAYS = [0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

    function idErr(v, type) {
      const s = (v || '').trim().toUpperCase();
      if (!s) return '请输入证件号码';
      if (String(type || '1') === '1') {
        if (!/^\d{17}[\dX]$/.test(s)) return '居民身份证号应为 18 位（末位可为数字或 X）';
        if (!ID_PROV.has(s.slice(0, 2))) return '身份证号前两位省份代码无效，请核对';
        const y = +s.slice(6, 10); const m = +s.slice(10, 12); const d = +s.slice(12, 14);
        if (y < 1900 || y > 2099 || m < 1 || m > 12 || d < 1 || d > ID_DAYS[m]) {
          return '身份证号中的出生日期无效，请核对';
        }
        let sum = 0;
        for (let i = 0; i < 17; i += 1) sum += +s[i] * ID_W[i];
        if (ID_C[sum % 11] !== s[17]) return '身份证号校验位不正确，请核对后重新输入';
        return '';
      }
      if (!/^[A-Za-z0-9]{6,20}$/.test(s)) return '证件号码格式不正确（应为 6-20 位字母或数字）';
      return '';
    }

    const RULES_BASE = {
      org_code: [{ required: true, message: '请选择考试机构编码', trigger: 'change' }],
      exam_site_code: [
        { required: true, message: '请输入考点编码', trigger: 'blur' },
        { pattern: /^[A-Za-z0-9]{4,12}$/, message: '4-12 位数字或字母', trigger: 'blur' },
      ],
      name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
      gender: [{ required: true, message: '请选择性别', trigger: 'change' }],
      id_type: [{ required: true, message: '请选择证件类型', trigger: 'change' }],
      id_number: [
        { required: true, message: '请输入证件号码', trigger: 'blur' },
        { validator: (rule, value, cb) => {
          const e = idErr(value, form.id_type || '1');
          return e ? cb(new Error(e)) : cb();
        }, trigger: 'blur' },
      ],
      subject: [{ required: true, message: '请选择报考科目', trigger: 'change' }],
      school: [{ required: true, message: '请输入就读或者毕业院校', trigger: 'blur' }],
      class_name: [{ required: true, message: '请输入班级', trigger: 'blur' }],
      education: [{ required: true, message: '请选择学历', trigger: 'change' }],
      phone: [
        { required: true, message: '请输入手机号码', trigger: 'blur' },
        { pattern: PHONE, message: '手机号码必须为 11 位数字', trigger: 'blur' },
      ],
      email: [{ type: 'email', message: 'Email 格式不正确', trigger: 'blur' }],
      ethnicity: [{ required: true, message: '请选择民族', trigger: 'change' }],
      occupation: [{ required: true, message: '请选择从事职业', trigger: 'change' }],
      employer: [{ required: true, message: '请输入所在单位', trigger: 'blur' }],
      postcode: [{ pattern: /^\d{6}$/, message: '邮政编码必须为 6 位数字', trigger: 'blur' }],
      birth_province: [],
      live_province: [],
    };

    // 字段元信息（来自考试类型配置）：决定哪些字段采集、是否必填、显示名
    const cfg = ref({});
    function on(f) { const c = cfg.value[f]; return !c || c.enabled !== false; }
    function lb(f, dft) { const c = cfg.value[f]; return (c && c.label) || dft; }
    function applyFields(list) {
      const m = {};
      (list || []).forEach((f) => { m[f.key] = f; });
      cfg.value = m;
      // 自定义字段占用 form.extra，先按类型备好默认值（多选是数组）
      const ex = {};
      (list || []).filter((f) => f.custom).forEach((f) => {
        ex[f.key] = f.type === 'multiselect' ? [] : '';
      });
      form.extra = ex;
    }

    /** 通用模板的自定义字段定义（按 sort 排序） */
    const customMeta = computed(() => {
      const all = (exam.value && exam.value.fields) || [];
      return all.filter((f) => f.custom && f.enabled !== false)
        .slice().sort((a, b) => (a.sort || 0) - (b.sort || 0));
    });

    // 下拉/单选类字段：必填提示用 change 触发，文本框用 blur
    const PICKERS = new Set(['org_code', 'subject', 'education', 'id_type', 'gender',
      'ethnicity', 'occupation', 'birth_province', 'birth_city', 'birth_county',
      'live_province', 'live_city', 'live_county']);
    const rules = computed(() => {
      const out = {};
      // 基础规则未覆盖但被管理员设为必填的字段（如 address / student_no）也要生成规则
      const keys = new Set(Object.keys(RULES_BASE));
      Object.keys(cfg.value).forEach((k) => { if (cfg.value[k].required) keys.add(k); });
      keys.forEach((k) => {
        const c = cfg.value[k];
        if (c && c.enabled === false) { out[k] = []; return; }
        let arr = (RULES_BASE[k] || []).slice();
        if (c && c.required === false) {
          arr = arr.filter((r) => !r.required);   // 管理员取消必填
        }
        // 管理员追加必填，而基础规则里没有必填项时补一条
        if (c && c.required && !arr.some((r) => r.required)) {
          const t = PICKERS.has(k) ? 'change' : 'blur';
          arr.unshift({ required: true, message: '请填写' + (c.label || k), trigger: t });
        }
        out[k] = arr;
      });
      // 通用模板的自定义字段：必填与数字格式在前端先拦一道，服务端再兜底
      customMeta.value.forEach((f) => {
        const arr = [];
        const isPick = f.type === 'select' || f.type === 'multiselect' || f.type === 'date';
        if (f.required) {
          arr.push({ required: true, type: f.type === 'multiselect' ? 'array' : undefined,
            message: (isPick ? '请选择' : '请填写') + f.label,
            trigger: isPick ? 'change' : 'blur' });
        }
        if (f.type === 'number') {
          arr.push({ pattern: /^-?\d+(\.\d+)?$/, message: f.label + '需要填写数字', trigger: 'blur' });
        }
        out['extra.' + f.key] = arr;
      });
      return out;
    });
    const fieldCount = computed(() => {
      const all = (exam.value && exam.value.fields) || [];
      if (!all.length) return exam.value && exam.value.exam_type_base === 'mandarin' ? 20 : 13;
      return all.filter((f) => f.enabled !== false).length;
    });
    /** 顶部提示随模板变化：通用模板要说明自定义字段的数量 */
    const tipText = computed(() => {
      const b = exam.value && exam.value.exam_type_base;
      if (b === 'computer') return '考试机构与报考科目请从下拉字典中选择。';
      if (b === 'mandarin') return '出生地与现居住地需选择省 / 市 / 县(区)。';
      if (b === 'generic') {
        return customMeta.value.length
          ? `其中 ${customMeta.value.length} 个是管理员为该类型配置的自定义字段。`
          : '该类型暂未配置自定义字段，只有 8 个核心字段。';
      }
      return '';
    });

    const provinces = computed(() => store.regions.map((r) => r.province));
    const birthCities = computed(() => regionIndex.cities(form.birth_province || ''));
    const birthCounties = computed(() => regionIndex.counties(form.birth_province || '', form.birth_city || ''));
    const liveCities = computed(() => regionIndex.cities(form.live_province || ''));
    const liveCounties = computed(() => regionIndex.counties(form.live_province || '', form.live_city || ''));
    const orgName = computed(() => {
      const o = store.dicts.orgs.find((x) => x.code === form.org_code);
      return o ? o.name : '';
    });

    function onOrgChange() { /* 机构名称自动带出，由 orgName 计算属性渲染 */ }
    function onProvinceChange(prefix) {
      form[prefix + '_city'] = '';
      form[prefix + '_county'] = '';
    }
    function onCityChange(prefix) { form[prefix + '_county'] = ''; }

    async function loadExam() {
      loading.value = true;
      try {
        await loadDicts();
        exam.value = await api.get('/api/exams/' + examId);
        applyFields(exam.value.fields);
        // 新增报名时用管理员配置的默认值预填（编辑时不覆盖考生自己填的内容）
        if (!appId) {
          const d = (exam.value.defaults || {}).employer;
          if (d && !form.employer) form.employer = d;
        }
        if (appId) {
          const d = await api.get(`/api/applications/${appId}?app_type=${exam.value.exam_type_base}`);
          Object.keys(d).forEach((k) => {
            if (k in form || ['org_code', 'exam_site_code', 'name', 'gender', 'id_type', 'id_number',
              'subject', 'school', 'class_name', 'education', 'phone', 'email', 'address',
              'ethnicity', 'occupation', 'employer', 'student_no', 'department', 'contact_address',
              'mail_address', 'postcode', 'birth_province', 'birth_city', 'birth_county',
              'live_province', 'live_city', 'live_county'].includes(k)) form[k] = d[k];
          });
          // 自定义字段回显：合并而非整体替换，避免缺 key 的字段变成 undefined
          if (d.extra && typeof d.extra === 'object' && !Array.isArray(d.extra)) {
            form.extra = { ...(form.extra || {}), ...d.extra };
          }
        }
      } catch (e) {
        handleErr(e);
        router.push('/');
      } finally { loading.value = false; }
    }

    async function submit() {
      try { await formRef.value.validate(); } catch (e) {
        msg.err('请检查表单中标红的必填项');
        return;
      }
      saving.value = true;
      try {
        const payload = { ...form, exam_id: examId };
        if (appId) {
          await api.put(`/api/applications/${appId}?app_type=${exam.value.exam_type_base}`, payload);
          msg.ok('修改成功，已重新提交待审核');
        } else {
          await api.post('/api/applications', payload);
          msg.ok('报名提交成功，请等待审核');
        }
        router.push('/my-applications');
      } catch (e) {
        handleErr(e);
      } finally { saving.value = false; }
    }

    onMounted(loadExam);
    return {
      exam, loading, saving, formRef, form, rules, submit, store, provinces,
      birthCities, birthCounties, liveCities, liveCounties, orgName, onOrgChange,
      onProvinceChange, onCityChange, editMode, on, lb, fieldCount, customMeta, tipText,
    };
  },
};
