/** 实验名称关联迁移演练接口封装：迁移、评估受理、改名、旧版入口、应急切换。 */
import { request } from './client'

export type MigrationSnapshot = {
  entry_mode: string
  same_display_name: string
  experiments: Record<string, string | number>[]
  group_evidence: Record<string, string | number | boolean>[]
  legacy_links: Record<string, string>[]
  legacy_tasks: Record<string, unknown>[]
  sample_pool: Record<string, string>[]
  group_version_mapping: Record<string, string>[]
  migration_batches: Record<string, string | number>[]
  migration_ledger: Record<string, string | boolean>[]
  evaluation_batches: Record<string, unknown>[]
  sample_claims: Record<string, string | number>[]
  experiment_aliases: Record<string, string>[]
  entry_mode_log: Record<string, string | number>[]
  summary: Record<string, number>
}

export type RejectedSample = { sample_code: string; reason: string }

export type MigrationRunResult = {
  ok: boolean
  code?: string
  message?: string
  idempotent?: boolean
  batch?: { batch_no: string; status: string }
  migrated?: Array<{ legacy_task_code: string; exp_code: string; group_code: string }>
  pending_review?: Array<{ legacy_task_code: string; review_reason: string }>
  already_migrated_elsewhere?: Array<{ legacy_task_code: string; batch_no: string }>
  claimed_samples?: string[]
}

export type EvaluationResult = {
  ok: boolean
  code?: string
  message?: string
  accepted: boolean
  idempotent?: boolean
  reason?: string
  batch?: { batch_no: string; group_no: string; status: string }
  mapping?: Record<string, string>
  claimed_samples?: string[]
  rejected_samples?: RejectedSample[]
}

export type LegacyTaskBrief = {
  task_code: string
  result: string
  group_no: string
  sample_codes: string[]
}

export type LegacyVersionGroup = {
  version: string
  group_no: string
  tasks: LegacyTaskBrief[]
}

export type LegacySearchResult = {
  ok: boolean
  total: number
  matched_names: string[]
  groups_by_version: LegacyVersionGroup[]
}

export type VerifyResult = {
  ok: boolean
  code?: string
  message?: string
  group_used: { exp_code: string; group_code: string; group_no: string; version: string }
  samples: Array<{ sample_code: string; group_no: string; version: string }>
}

export type MigrationResult = {
  ok: boolean
  code?: string
  message?: string
  [key: string]: unknown
}

async function post(path: string, body: Record<string, unknown>): Promise<MigrationResult> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return (await response.json()) as MigrationResult
}

export async function getSnapshot(): Promise<MigrationSnapshot> {
  const response = await request('/api/migration/snapshot')
  if (!response.ok) throw new Error('迁移演练数据读取失败')
  return (await response.json()) as MigrationSnapshot
}

export const runMigration = async (batchNo: string): Promise<MigrationRunResult> =>
  (await post('/api/migration/run', { batch_no: batchNo, operator: '值班员' })) as MigrationRunResult

export const completeGroup = (
  linkId: number,
  payload: { group_no: string; evidence_ref: string; group_code?: string },
) => post(`/api/migration/links/${linkId}/complete-group`, payload)

export const submitEvaluation = async (
  payload: Record<string, unknown>,
): Promise<EvaluationResult> =>
  (await post('/api/migration/evaluation/submit', payload)) as EvaluationResult

export const verifyEvaluation = async (
  batchNo: string,
  credential: string,
): Promise<VerifyResult> =>
  (await post(`/api/migration/evaluation/${batchNo}/verify`, { credential })) as VerifyResult

export const renameExperiment = (expCode: string, newName: string) =>
  post('/api/migration/experiment/rename', { exp_code: expCode, new_name: newName })

export const legacySearch = async (displayName: string): Promise<LegacySearchResult> => {
  const response = await request(
    `/api/migration/legacy/search?display_name=${encodeURIComponent(displayName)}`,
  )
  return (await response.json()) as LegacySearchResult
}

export const legacyEvaluate = (payload: Record<string, string>) =>
  post('/api/migration/legacy/evaluate', payload)

export const setEntryMode = (mode: string, reason: string) =>
  post('/api/migration/entry-mode', { mode, reason })
