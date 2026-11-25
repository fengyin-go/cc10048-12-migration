<template>
  <section class="page migration-page" data-testid="migration-page">
    <header class="page-head">
      <div>
        <h2>实验名称关联迁移演练</h2>
        <p class="page-desc">
          存量检验按显示名关联，两个同名实验（A 带可核对组别号 / B 仅显示名）演练上线：
          迁移到组别版本映射表，新版只认稳定实验与组别标识，支持幂等重试与旧版应急回滚。
        </p>
      </div>
      <div class="entry-switch" :data-testid="`entry-mode-${snapshot?.entry_mode ?? 'new'}`">
        <span class="mode-tag" :class="snapshot?.entry_mode === 'legacy' ? 'tag-legacy' : 'tag-new'">
          当前入口：{{ snapshot?.entry_mode === 'legacy' ? '旧版（应急）' : '新版' }}
        </span>
        <button
          class="btn"
          type="button"
          data-testid="toggle-entry"
          :disabled="!snapshot"
          @click="toggleEntryMode"
        >
          {{ snapshot?.entry_mode === 'legacy' ? '切回新版入口' : '回滚旧版入口' }}
        </button>
      </div>
    </header>

    <div v-if="message" class="notice" :class="message.kind" data-testid="notice">
      {{ message.text }}
    </div>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">映射表登记</span>
        <strong class="stat-value">{{ snapshot?.summary.mapping_count ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已迁移历史检验</span>
        <strong class="stat-value">{{ snapshot?.summary.migrated_legacy ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待核查（不猜组）</span>
        <strong class="stat-value">{{ snapshot?.summary.pending_review ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">评估批次 / 样本领取</span>
        <strong class="stat-value">
          {{ snapshot?.summary.evaluation_batches ?? 0 }} / {{ snapshot?.summary.claimed_samples ?? 0 }}
        </strong>
      </article>
    </div>

    <div class="panel">
      <h3>① 启动迁移（同一批次号重试幂等）</h3>
      <form class="inline-form" @submit.prevent="doRunMigration">
        <label>
          <span>迁移批次号</span>
          <input v-model="batchNo" data-testid="batch-no" required />
        </label>
        <button class="btn primary" type="submit" data-testid="run-migration">执行 / 重试迁移</button>
        <button class="btn ghost" type="button" data-testid="refresh" @click="load">刷新数据</button>
      </form>
      <p v-if="lastMigration" class="panel-note" data-testid="migration-result">
        批次 {{ lastMigration.batch?.batch_no }}：{{ lastMigration.batch?.status }}，
        已迁移 {{ lastMigration.migrated?.length ?? 0 }} 条，
        待核查 {{ lastMigration.pending_review?.length ?? 0 }} 条，
        幂等重试 = {{ String(Boolean(lastMigration.idempotent)) }}
      </p>    </div>

    <div class="panel two-col">
      <div>
        <h3>② 新版实验台账（稳定标识，改名不变）</h3>
        <table class="data-table">
          <thead>
            <tr><th>实验标识</th><th>版本</th><th>显示名</th><th>组别标识</th><th>组别号</th></tr>
          </thead>
          <tbody>
            <tr v-for="exp in snapshot?.experiments ?? []" :key="String(exp.exp_code)">
              <td>{{ exp.exp_code }}</td>
              <td>{{ exp.version }}</td>
              <td>{{ exp.display_name }}</td>
              <td>{{ exp.group_code || '—' }}</td>
              <td>{{ exp.group_no || '待核查' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div>
        <h3>③ 组别版本映射表（新版评估唯一依据）</h3>
        <table class="data-table">
          <thead>
            <tr><th>实验标识</th><th>组别标识</th><th>版本</th><th>组别号</th><th>证据</th></tr>
          </thead>
          <tbody>
            <tr v-for="m in snapshot?.group_version_mapping ?? []" :key="`${m.exp_code}-${m.group_code}-${m.version}`">
              <td>{{ m.exp_code }}</td>
              <td>{{ m.group_code }}</td>
              <td>{{ m.version }}</td>
              <td>{{ m.group_no }}</td>
              <td>{{ m.evidence_ref }}</td>
            </tr>
            <tr v-if="!(snapshot?.group_version_mapping.length)">
              <td colspan="5" class="empty-state">迁移前为空，由迁移过程登记</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="panel">
      <h3>④ 存量名称关联与待核查处理</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>历史检验</th><th>显示名</th><th>版本</th><th>组别号</th>
            <th>迁移状态</th><th>核查说明</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="link in snapshot?.legacy_links ?? []" :key="String(link.id)"
              :data-testid="`link-row-${link.id}`">
            <td>{{ link.legacy_task_code }}</td>
            <td>{{ link.display_name }}</td>
            <td>{{ link.version }}</td>
            <td>{{ link.group_no || '—' }}</td>
            <td>
              <span class="status-pill" :class="link.link_status === '已迁移' ? 'ok' : 'warn'">
                {{ link.link_status }}
              </span>
            </td>
            <td>{{ link.review_reason || link.evidence_note }}</td>
            <td>
              <button
                v-if="link.link_status !== '已迁移'"
                class="link"
                type="button"
                :data-testid="`complete-${link.id}`"
                @click="openComplete(Number(link.id))"
              >
                补齐组别证据
              </button>
              <span v-else class="muted">已登记映射</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>⑤ 提交新版评估（查映射表决定是否受理样本）</h3>
      <form class="grid-form" @submit.prevent="doSubmitEvaluation">
        <label><span>实验标识 exp_code</span><input v-model="evalForm.exp_code" required /></label>
        <label><span>组别标识 group_code</span><input v-model="evalForm.group_code" required /></label>
        <label><span>版本</span><input v-model="evalForm.version" required /></label>
        <label><span>幂等键</span><input v-model="evalForm.idem_key" required /></label>
        <label><span>评估凭据</span><input v-model="evalForm.credential" required /></label>
        <div class="form-actions">
          <button class="btn primary" type="submit" data-testid="submit-eval">提交评估</button>
        </div>
      </form>
      <div v-if="evalResult" class="result-box" data-testid="eval-result">
        <p>
          受理：<strong :class="evalResult.accepted ? 'ok-text' : 'warn-text'">
            {{ String(Boolean(evalResult.accepted)) }}
          </strong>
          <span v-if="evalResult.idempotent">（幂等重试，返回原批次）</span>
        </p>
        <p v-if="evalResult.reason" class="warn-text">原因：{{ evalResult.reason }}</p>
        <p v-if="evalResult.batch">
          批次 {{ evalResult.batch.batch_no }}，组别号 {{ evalResult.batch.group_no }}，
          领取样本 {{ evalResult.claimed_samples?.join('、') || '无' }}
        </p>
        <ul v-if="evalResult.rejected_samples?.length">
          <li v-for="r in evalResult.rejected_samples" :key="r.sample_code">
            {{ r.sample_code }}：{{ r.reason }}
          </li>
        </ul>
      </div>
    </div>

    <div class="panel two-col">
      <div>
        <h3>⑥ 评估批次与样本领取台账</h3>
        <table class="data-table">
          <thead><tr><th>批次</th><th>实验/组别</th><th>状态</th><th>凭据复核</th></tr></thead>
          <tbody>
            <tr v-for="b in snapshot?.evaluation_batches ?? []" :key="String(b.batch_no)">
              <td>{{ b.batch_no }}</td>
              <td>{{ b.exp_code }}<br />{{ b.group_code }}</td>
              <td>{{ b.status }}</td>
              <td>
                <button class="link" type="button" :data-testid="`verify-${b.batch_no}`"
                        @click="doVerify(String(b.batch_no))">
                  按凭据复核
                </button>
              </td>
            </tr>
            <tr v-if="!(snapshot?.evaluation_batches.length)">
              <td colspan="4" class="empty-state">暂无评估批次</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div>
        <h3>⑦ 实验改名（稳定标识不变）</h3>
        <form class="inline-form" @submit.prevent="doRename">
          <label><span>实验标识</span><input v-model="renameForm.exp_code" required /></label>
          <label><span>新显示名</span><input v-model="renameForm.new_name" required /></label>
          <button class="btn primary" type="submit" data-testid="rename">确认改名</button>
        </form>
        <h3 class="mt">按旧显示名查历史检验（版本隔离）</h3>
        <form class="inline-form" @submit.prevent="doLegacySearch">
          <label><span>显示名</span><input v-model="legacyKeyword" required /></label>
          <button class="btn" type="submit" data-testid="legacy-search">查询历史</button>
        </form>
        <div v-if="legacyResult" class="result-box" data-testid="legacy-result">
          <p>命中 {{ legacyResult.total }} 条历史检验，按版本分组：</p>
          <div v-for="g in legacyResult.groups_by_version" :key="g.version" class="version-group">
            <strong>版本 {{ g.version }}（组别号 {{ g.group_no || '无证据' }}）</strong>
            <ul>
              <li v-for="t in g.tasks" :key="t.task_code">
                {{ t.task_code }} / {{ t.result }} / 样本 {{ t.sample_codes.join('、') }}
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showComplete" class="modal-backdrop" data-testid="complete-modal">
      <form class="modal" @submit.prevent="doComplete">
        <h3>补齐 B 的组别证据（不允许猜组）</h3>
        <label class="create-field"><span>组别号 group_no</span>
          <input v-model="completeForm.group_no" data-testid="complete-group-no" required />
        </label>
        <label class="create-field"><span>稳定组别标识 group_code</span>
          <input v-model="completeForm.group_code" placeholder="留空则按实验标识生成" />
        </label>
        <label class="create-field"><span>核查凭据 evidence_ref</span>
          <input v-model="completeForm.evidence_ref" data-testid="complete-evidence" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showComplete = false">取消</button>
          <button class="btn primary" type="submit" data-testid="complete-submit">登记证据</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  completeGroup,
  getSnapshot,
  legacySearch,
  renameExperiment,
  runMigration,
  setEntryMode,
  submitEvaluation,
  verifyEvaluation,
  type EvaluationResult,
  type LegacySearchResult,
  type MigrationRunResult,
  type MigrationSnapshot,
} from '@/api/migration-service'

const snapshot = ref<MigrationSnapshot | null>(null)
const message = ref<{ text: string; kind: 'ok' | 'warn' } | null>(null)
const batchNo = ref('MIG-BATCH-001')
const lastMigration = ref<MigrationRunResult | null>(null)

const evalForm = ref({
  exp_code: 'EXP-HOME-001',
  group_code: 'GRP-A01',
  version: 'v1',
  idem_key: 'EVAL-A-01',
  credential: 'cred-A-101',
})
const evalResult = ref<EvaluationResult | null>(null)

const renameForm = ref({ exp_code: 'EXP-HOME-001', new_name: '' })
const legacyKeyword = ref('首页推荐流改版实验')
const legacyResult = ref<LegacySearchResult | null>(null)

const showComplete = ref(false)
const completeLinkId = ref(0)
const completeForm = ref({ group_no: '', group_code: '', evidence_ref: '' })

function notify(text: string, kind: 'ok' | 'warn' = 'ok') {
  message.value = { text, kind }
}

async function load() {
  snapshot.value = await getSnapshot()
}

async function doRunMigration() {
  const result = await runMigration(batchNo.value)
  lastMigration.value = result
  notify(
    result.ok
      ? `迁移完成：已迁移 ${result.migrated?.length ?? 0} 条，待核查 ${result.pending_review?.length ?? 0} 条`
      : `迁移失败：${result.message ?? '未知错误'}`,
    result.ok ? 'ok' : 'warn',
  )
  await load()
}

function openComplete(linkId: number) {
  completeLinkId.value = linkId
  completeForm.value = { group_no: '', group_code: '', evidence_ref: '' }
  showComplete.value = true
}

async function doComplete() {
  const result = await completeGroup(completeLinkId.value, {
    group_no: completeForm.value.group_no,
    group_code: completeForm.value.group_code,
    evidence_ref: completeForm.value.evidence_ref,
  })
  showComplete.value = false
  notify(result.ok ? '组别证据已登记，可重跑迁移补齐归属' : `登记被拒：${result.message}`,
         result.ok ? 'ok' : 'warn')
  await load()
}

async function doSubmitEvaluation() {
  const result = await submitEvaluation({ ...evalForm.value })
  evalResult.value = result
  notify(result.ok ? '评估请求已处理' : `评估未受理：${result.message ?? result.reason ?? ''}`,
         result.ok ? 'ok' : 'warn')
  await load()
}

async function doVerify(batchNoValue: string) {
  const credential = window.prompt('请输入该评估批次登记的原凭据')
  if (!credential) return
  const result = await verifyEvaluation(batchNoValue, credential)
  if (result.ok) {
    const group = result.group_used
    const samples = result.samples.map((item) => item.sample_code).join('、')
    notify(`复核通过：${group.exp_code} / ${group.group_code} / 组别号 ${group.group_no}，样本 ${samples}`)
  } else {
    notify(`复核被拒：${result.message}`, 'warn')
  }
}

async function doRename() {
  const result = await renameExperiment(renameForm.value.exp_code, renameForm.value.new_name)
  notify(result.ok ? '改名完成，稳定标识与映射保持不变，旧名已登记别名' : `改名失败：${result.message}`,
         result.ok ? 'ok' : 'warn')
  await load()
}

async function doLegacySearch() {
  legacyResult.value = await legacySearch(legacyKeyword.value)
}

async function toggleEntryMode() {
  const goingLegacy = snapshot.value?.entry_mode !== 'legacy'
  const reason = window.prompt(
    goingLegacy ? '请填写回滚旧版入口的应急原因' : '请填写恢复新版入口的原因',
    goingLegacy ? '值班应急回滚' : '故障恢复',
  )
  if (reason === null) return
  const result = await setEntryMode(goingLegacy ? 'legacy' : 'new', reason || '值班员手动切换')
  notify(result.ok ? `入口已切换为${goingLegacy ? '旧版（应急）' : '新版'}` : `切换失败：${result.message}`,
         result.ok ? 'ok' : 'warn')
  await load()
}

onMounted(load)
</script>

<style scoped>
.migration-page { display: flex; flex-direction: column; gap: 14px; }
.entry-switch { display: flex; align-items: center; gap: 10px; }
.mode-tag { padding: 4px 10px; border-radius: 999px; font-size: 12px; }
.tag-new { background: #e7f0ff; color: #1f6feb; }
.tag-legacy { background: #fef3c7; color: #b45309; }
.notice { padding: 10px 12px; border-radius: 8px; font-size: 13px; }
.notice.ok { background: #ecfdf3; color: #027a48; border: 1px solid #abefc6; }
.notice.warn { background: #fef3c7; color: #b45309; border: 1px solid #fde68a; }
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
.panel h3 { margin: 0 0 10px; font-size: 15px; }
.panel h3.mt { margin-top: 14px; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.inline-form { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; }
.inline-form label span, .grid-form label span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.inline-form input, .grid-form input { border: 1px solid var(--border); border-radius: 6px; padding: 7px 9px; }
.grid-form { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)) auto; gap: 10px; align-items: end; }
.form-actions { display: flex; align-items: end; }
.panel-note { font-size: 13px; color: var(--muted); margin: 10px 0 0; }
.status-pill { padding: 2px 8px; border-radius: 999px; font-size: 12px; }
.status-pill.ok { background: #ecfdf3; color: #027a48; }
.status-pill.warn { background: #fef3c7; color: #b45309; }
.muted { color: var(--muted); font-size: 12px; }
.result-box { margin-top: 10px; border: 1px dashed var(--border); border-radius: 8px; padding: 10px; font-size: 13px; }
.ok-text { color: #027a48; }
.warn-text { color: #b45309; }
.version-group { margin-bottom: 8px; }
@media (max-width: 1100px) {
  .two-col, .grid-form { grid-template-columns: 1fr; }
}
</style>
