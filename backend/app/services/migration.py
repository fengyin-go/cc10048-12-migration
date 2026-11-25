"""实验名称关联迁移与新版评估服务。

核心规则（与上线演练要求一一对应）：
1. 存量检验只按显示名关联；迁移时以组别证据底账核对，能唯一证明归属才登记
   组别版本映射，不能证明的保留旧记录进入待核查，绝不猜组；
2. 组别版本映射表按稳定实验标识 exp_code + 组别标识 group_code + 版本登记，
   实验改名不改变标识，映射与历史归属不受影响；
3. 迁移台账按存量检验唯一、样本领取按作用域内样本唯一，同一批数据重试不会把
   一条历史检验或一个样本记到两个新版批次；
4. 新版评估先查映射表决定能否领取样本；映射缺失时明确给出未受理原因；
5. 入口可在新版/旧版间切换应急；回滚后按原凭据仍可复核组别和样本，旧版按
   版本隔离，两个同名实验的结果不会再混在一起。
"""
from __future__ import annotations

import time
from typing import Any

from app.migration_seed import SAME_DISPLAY_NAME
from app.migration_store import MigrationStore, migration_store


class MigrationError(Exception):
    """领域错误：携带 HTTP 状态码与稳定错误码，路由层只做转译。"""

    def __init__(self, message: str, *, code: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class MigrationService:
    MIGRATION_SCOPE = "migration"
    EVALUATION_SCOPE = "evaluation"
    LEGACY_SCOPE = "legacy"

    def __init__(self, store: MigrationStore | None = None) -> None:
        self.store = store or migration_store

    # ------------------------------------------------------------------
    # 总览快照
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        s = self.store
        mapping_rows = s.rows("group_version_mapping")
        eval_rows = s.rows("evaluation_batch")
        return {
            "entry_mode": s.state_get("entry_mode", "new"),
            "same_display_name": SAME_DISPLAY_NAME,
            "experiments": s.rows("scenario_experiment"),
            "group_evidence": s.rows("group_evidence"),
            "legacy_links": s.rows("legacy_link"),
            "legacy_tasks": s.rows("legacy_task"),
            "sample_pool": s.rows("sample_pool"),
            "group_version_mapping": mapping_rows,
            "migration_batches": s.rows("migration_batch"),
            "migration_ledger": s.rows("migration_ledger"),
            "evaluation_batches": eval_rows,
            "sample_claims": s.rows("sample_claim"),
            "experiment_aliases": s.rows("experiment_alias"),
            "entry_mode_log": s.rows("entry_mode_log"),
            "summary": {
                "mapping_count": len(mapping_rows),
                "migrated_legacy": sum(1 for r in s.rows("migration_ledger")
                                       if r.get("mapping_status") == "已核实归属"),
                "pending_review": sum(1 for r in s.rows("legacy_link")
                                      if r.get("link_status") == "待核查"),
                "evaluation_batches": len(eval_rows),
                "claimed_samples": len(s.rows("sample_claim")),
            },
        }

    # ------------------------------------------------------------------
    # 迁移：存量名称关联 → 组别版本映射
    # ------------------------------------------------------------------
    def run_migration(self, batch_no: str, *, operator: str = "值班员") -> dict[str, Any]:
        s = self.store
        batch_no = batch_no.strip()
        if not batch_no:
            raise MigrationError("迁移批次号不能为空", code="BATCH_NO_EMPTY", status_code=400)

        batch, created = s.insert_unique(
            "migration_batch",
            {"batch_no": batch_no},
            {
                "batch_no": batch_no,
                "operator": operator,
                "started_at": _now(),
                "status": "执行中",
                "migrated_count": 0,
                "pending_count": 0,
            },
        )

        migrated: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        already_migrated: list[dict[str, Any]] = []
        for link in list(s.rows("legacy_link")):
            task_code = str(link.get("legacy_task_code", ""))
            # 台账已登记过的存量检验：按批次决定重试还是跳过，保证不会进第二个批次。
            existed = next(
                (r for r in s.rows("migration_ledger")
                 if r.get("legacy_task_code") == task_code),
                None,
            )
            if existed is not None and existed.get("mapping_status") == "已核实归属":
                if str(existed.get("batch_no", "")) == batch_no:
                    # 同一批次重试：原样返回，不重复登记、不重复领样本。
                    migrated.append(existed)
                else:
                    # 已被其他批次迁移：本批次不得再记一遍，仅回显原归属。
                    already_migrated.append(existed)
                continue

            group_no = str(link.get("group_no", "")).strip()
            evidence = self._verified_evidence(group_no) if group_no else None
            if evidence is not None:
                mapping = self._register_mapping(link, evidence, task_code)
                link["link_status"] = "已迁移"
                link["migration_batch_no"] = batch_no
                link["review_reason"] = ""
                # upsert：待核查项补齐证据后回填同一条台账，不留两行冲突记录。
                ledger, _ = s.upsert_unique(
                    "migration_ledger",
                    {"legacy_task_code": task_code},
                    {
                        "legacy_task_code": task_code,
                        "display_name": link.get("display_name", ""),
                        "version": link.get("version", ""),
                        "exp_code": mapping["exp_code"],
                        "group_code": mapping["group_code"],
                        "group_no": mapping["group_no"],
                        "mapping_status": "已核实归属",
                        "batch_no": batch_no,
                        "reuse_evidence": True,
                        "recorded_at": _now(),
                        "review_reason": "",
                    },
                )
                current_batch = next(
                    (row for row in s.rows("migration_batch") if row.get("batch_no") == batch_no),
                    batch,
                )
                self._claim_legacy_samples(task_code, batch_no, int(current_batch["id"]))
                migrated.append(ledger)
            else:
                reason = ("存量关联仅填写实验显示名、无组别号，"
                          "组别证据底账无法核对，保留旧记录待人工核查")
                link["link_status"] = "待核查"
                link["review_reason"] = reason
                ledger, _ = s.upsert_unique(
                    "migration_ledger",
                    {"legacy_task_code": task_code},
                    {
                        "legacy_task_code": task_code,
                        "display_name": link.get("display_name", ""),
                        "version": link.get("version", ""),
                        "exp_code": "",
                        "group_code": "",
                        "group_no": group_no,
                        "mapping_status": "待核查",
                        "batch_no": "",
                        "reuse_evidence": False,
                        "recorded_at": _now(),
                        "review_reason": reason,
                    },
                )
                pending.append(ledger)

        batch["migrated_count"] = len(migrated)
        batch["pending_count"] = len(pending)
        batch["status"] = "已完成" if not pending else "部分完成（有待核查项）"
        batch["finished_at"] = _now()
        return {
            "ok": True,
            "idempotent": not created,
            "batch": batch,
            "migrated": migrated,
            "pending_review": pending,
            "already_migrated_elsewhere": [
                {"legacy_task_code": row.get("legacy_task_code"),
                 "batch_no": row.get("batch_no"),
                 "exp_code": row.get("exp_code"),
                 "group_code": row.get("group_code")}
                for row in already_migrated
            ],
            "claimed_samples": [
                claim["sample_code"]
                for claim in s.claims_for_batch(batch_no, self.MIGRATION_SCOPE)
            ],
        }

    def _verified_evidence(self, group_no: str) -> dict[str, Any] | None:
        matches = [
            row for row in self.store.rows("group_evidence")
            if row.get("group_no") == group_no and row.get("verified") is True
        ]
        # 只能证明一个归属时才成立；多条互相冲突的证据同样不自动登记。
        return matches[0] if len(matches) == 1 else None

    def _register_mapping(
        self, link: dict[str, Any], evidence: dict[str, Any], task_code: str
    ) -> dict[str, Any]:
        s = self.store
        payload = {
            "exp_code": evidence["exp_code"],
            "group_code": evidence["group_code"],
            "version": link.get("version", ""),
            "group_no": evidence["group_no"],
            "group_name": evidence.get("group_name", ""),
            "source_legacy_task": task_code,
            "evidence_ref": evidence.get("evidence_ref", ""),
            "mapping_status": "已核实",
            "registered_at": _now(),
        }
        mapping, _ = s.insert_unique(
            "group_version_mapping",
            {
                "exp_code": payload["exp_code"],
                "group_code": payload["group_code"],
                "version": payload["version"],
            },
            payload,
        )
        return mapping

    def _claim_legacy_samples(self, task_code: str, batch_no: str, batch_id: int) -> list[str]:
        s = self.store
        task = next(
            (row for row in s.rows("legacy_task") if row.get("task_code") == task_code),
            None,
        )
        claimed: list[str] = []
        if task is None:
            return claimed
        for sample_code in list(task.get("sample_codes", [])):
            claim, is_new = s.claim_sample(
                sample_code=sample_code,
                scope=self.MIGRATION_SCOPE,
                batch_no=batch_no,
                batch_id=batch_id,
                legacy_task_code=task_code,
            )
            if is_new:
                claimed.append(sample_code)
        return claimed

    # ------------------------------------------------------------------
    # 补齐 B 的组别证据
    # ------------------------------------------------------------------
    def complete_group_evidence(
        self,
        link_id: int,
        *,
        group_no: str,
        evidence_ref: str,
        group_code: str = "",
    ) -> dict[str, Any]:
        s = self.store
        link = s.find("legacy_link", link_id)
        if link is None:
            raise MigrationError(f"存量名称关联 {link_id} 不存在", code="LINK_NOT_FOUND",
                                 status_code=404)
        group_no = group_no.strip()
        evidence_ref = evidence_ref.strip()
        if not group_no or not evidence_ref:
            raise MigrationError("组别号和核查凭据不能为空", code="EVIDENCE_INCOMPLETE",
                                 status_code=400)
        version = str(link.get("version", ""))
        experiment = next(
            (row for row in s.rows("scenario_experiment") if row.get("version") == version),
            None,
        )
        if experiment is None:
            raise MigrationError(f"版本 {version} 找不到对应实验台账",
                                 code="EXPERIMENT_NOT_FOUND", status_code=404)
        exp_code = str(experiment["exp_code"])
        group_code = group_code.strip() or f"GRP-{exp_code.split('-')[-1]}-G{group_no[-2:]}"

        # 登记/核对组别证据底账；同一组别号不允许指向两个实验。
        for row in s.rows("group_evidence"):
            if row.get("group_no") == group_no and row.get("exp_code") != exp_code:
                raise MigrationError(
                    f"组别号 {group_no} 已登记给其他实验，不能重复归属",
                    code="GROUP_NO_CONFLICT",
                )
        evidence, _ = s.insert_unique(
            "group_evidence",
            {"group_no": group_no, "exp_code": exp_code},
            {
                "group_no": group_no,
                "exp_code": exp_code,
                "group_code": group_code,
                "group_name": experiment.get("group_name", ""),
                "evidence_ref": evidence_ref,
                "verified": True,
            },
        )
        experiment["group_no"] = group_no
        experiment["group_code"] = group_code
        link["group_no"] = group_no
        link["evidence_note"] = f"人工核查补齐：{evidence_ref}"
        link["link_status"] = "待迁移"
        link["review_reason"] = ""
        return {"ok": True, "link": link, "evidence": evidence,
                "experiment": experiment}

    # ------------------------------------------------------------------
    # 新版评估：按映射表决定能否领取样本
    # ------------------------------------------------------------------
    def submit_evaluation(
        self,
        *,
        exp_code: str,
        group_code: str,
        version: str,
        idem_key: str,
        credential: str,
        sample_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        s = self.store
        if s.state_get("entry_mode", "new") != "new":
            raise MigrationError(
                "当前已回滚到旧版入口，新版评估暂停受理；请使用旧版评估入口应急",
                code="ENTRY_LEGACY_MODE",
            )
        idem_key = idem_key.strip()
        credential = credential.strip()
        if not idem_key or not credential:
            raise MigrationError("幂等键和评估凭据不能为空", code="PARAM_INCOMPLETE",
                                 status_code=400)

        mapping = next(
            (row for row in s.rows("group_version_mapping")
             if row.get("exp_code") == exp_code
             and row.get("group_code") == group_code
             and row.get("version") == version
             and row.get("mapping_status") == "已核实"),
            None,
        )
        if mapping is None:
            return {
                "ok": False,
                "accepted": False,
                "reason": ("组别版本映射表缺少已核实的实验/组别标识登记，"
                           "无法证明样本归属，本次评估不予受理"),
                "exp_code": exp_code,
                "group_code": group_code,
                "version": version,
                "claimed_samples": [],
                "rejected_samples": [],
            }

        existing = next(
            (row for row in s.rows("evaluation_batch") if row.get("idem_key") == idem_key),
            None,
        )
        if existing is not None:
            batch_no = str(existing["batch_no"])
            return {
                "ok": True,
                "idempotent": True,
                "accepted": existing["status"] == "已受理",
                "batch": existing,
                "mapping": mapping,
                "claimed_samples": [
                    c["sample_code"]
                    for c in s.claims_for_batch(batch_no, self.EVALUATION_SCOPE)
                ],
                "rejected_samples": existing.get("rejected_samples", []),
                "reason": "同一幂等键重试，返回原受理批次，未重复领取样本",
            }

        wanted = list(sample_codes) if sample_codes else [
            row["sample_code"]
            for row in s.rows("sample_pool")
            if row.get("version") == version
        ]
        eval_id = s.next_id("evaluation_batch")
        batch_no = f"EVAL-{exp_code}-{version}-{eval_id:03d}"
        batch = {
            "id": eval_id,
            "batch_no": batch_no,
            "idem_key": idem_key,
            "exp_code": exp_code,
            "group_code": group_code,
            "version": version,
            "group_no": mapping["group_no"],
            "credential": credential,
            "mapping_ref": mapping.get("evidence_ref", ""),
            "status": "已受理",
            "submitted_at": _now(),
            "rejected_samples": [],
        }
        claimed: list[str] = []
        rejected: list[dict[str, str]] = []
        for sample_code in wanted:
            owner = s.sample_owner(sample_code, self.EVALUATION_SCOPE)
            if owner is not None and owner.get("batch_no") != batch_no:
                rejected.append({
                    "sample_code": sample_code,
                    "reason": f"样本已被评估批次 {owner.get('batch_no')} 领取，不能重复受理",
                })
                continue
            claim, is_new = s.claim_sample(
                sample_code=sample_code,
                scope=self.EVALUATION_SCOPE,
                batch_no=batch_no,
                batch_id=batch["id"],
            )
            if is_new:
                claimed.append(sample_code)
        if rejected and not claimed:
            batch["status"] = "未受理"
        elif rejected:
            batch["status"] = "部分受理"
        batch["rejected_samples"] = rejected
        s.rows("evaluation_batch").append(batch)
        return {
            "ok": True,
            "idempotent": False,
            "accepted": batch["status"] != "未受理",
            "batch": batch,
            "mapping": mapping,
            "claimed_samples": claimed,
            "rejected_samples": rejected,
            "reason": "" if claimed else "候选样本均已被其他批次领取",
        }

    def verify_evaluation(self, batch_no: str, credential: str) -> dict[str, Any]:
        s = self.store
        batch = next(
            (row for row in s.rows("evaluation_batch") if row.get("batch_no") == batch_no),
            None,
        )
        if batch is None:
            raise MigrationError(f"评估批次 {batch_no} 不存在", code="BATCH_NOT_FOUND",
                                 status_code=404)
        if str(batch.get("credential", "")) != credential.strip():
            raise MigrationError("凭据与评估批次登记不一致，拒绝复核",
                                 code="BAD_CREDENTIAL", status_code=403)
        mapping = next(
            (row for row in s.rows("group_version_mapping")
             if row.get("exp_code") == batch.get("exp_code")
             and row.get("group_code") == batch.get("group_code")
             and row.get("version") == batch.get("version")),
            None,
        )
        claims = s.claims_for_batch(batch_no, self.EVALUATION_SCOPE)
        samples = []
        for claim in claims:
            pool_row = next(
                (row for row in s.rows("sample_pool")
                 if row.get("sample_code") == claim["sample_code"]),
                None,
            )
            samples.append({
                "sample_code": claim["sample_code"],
                "group_no": (pool_row or {}).get("group_no", ""),
                "version": (pool_row or {}).get("version", ""),
            })
        return {
            "ok": True,
            "batch": batch,
            "mapping": mapping,
            "group_used": {
                "exp_code": batch.get("exp_code"),
                "group_code": batch.get("group_code"),
                "group_no": batch.get("group_no"),
                "version": batch.get("version"),
            },
            "samples": samples,
        }

    # ------------------------------------------------------------------
    # 实验改名：稳定标识不变，旧显示名仍可查历史
    # ------------------------------------------------------------------
    def rename_experiment(self, exp_code: str, new_name: str) -> dict[str, Any]:
        s = self.store
        new_name = new_name.strip()
        if not new_name:
            raise MigrationError("新实验名称不能为空", code="NAME_EMPTY", status_code=400)
        experiment = next(
            (row for row in s.rows("scenario_experiment") if row.get("exp_code") == exp_code),
            None,
        )
        if experiment is None:
            raise MigrationError(f"实验 {exp_code} 不存在", code="EXPERIMENT_NOT_FOUND",
                                 status_code=404)
        old_name = str(experiment.get("display_name", ""))
        if old_name == new_name:
            return {"ok": True, "experiment": experiment, "alias": None, "idempotent": True}
        alias, _ = s.insert_unique(
            "experiment_alias",
            {"exp_code": exp_code, "old_name": old_name},
            {
                "exp_code": exp_code,
                "old_name": old_name,
                "new_name": new_name,
                "changed_at": _now(),
            },
        )
        experiment["display_name"] = new_name
        # 映射表以稳定标识为键，改名不动映射；存量旧记录的显示名也原样保留。
        return {"ok": True, "experiment": experiment, "alias": alias, "idempotent": False}

    # ------------------------------------------------------------------
    # 旧版入口：按旧显示名查历史、按版本隔离复核
    # ------------------------------------------------------------------
    def legacy_search(self, display_name: str) -> dict[str, Any]:
        s = self.store
        keyword = display_name.strip()
        alias_old_names = {
            row.get("old_name")
            for row in s.rows("experiment_alias")
            if row.get("new_name") == keyword or row.get("old_name") == keyword
        }
        names = {keyword, *alias_old_names}
        tasks = [
            row for row in s.rows("legacy_task")
            if row.get("display_name") in names
        ]
        links = [
            row for row in s.rows("legacy_link")
            if row.get("display_name") in names
        ]
        # 同名实验按版本分组，从结果结构上杜绝两个实验被混到一起。
        grouped: dict[str, dict[str, Any]] = {}
        for task in tasks:
            version = str(task.get("version", ""))
            grouped.setdefault(version, {
                "version": version,
                "display_name": task.get("display_name", ""),
                "group_no": task.get("group_no", ""),
                "tasks": [],
            })
            grouped[version]["tasks"].append({
                "task_code": task.get("task_code"),
                "method": task.get("method"),
                "result": task.get("result"),
                "ran_at": task.get("ran_at"),
                "credential": task.get("credential"),
                "group_no": task.get("group_no", ""),
                "sample_codes": task.get("sample_codes", []),
            })
        return {
            "ok": True,
            "keyword": keyword,
            "matched_names": sorted(n for n in names if n),
            "legacy_links": links,
            "groups_by_version": [grouped[key] for key in sorted(grouped)],
            "total": len(tasks),
        }

    def legacy_evaluate(
        self, *, display_name: str, version: str = "", credential: str = ""
    ) -> dict[str, Any]:
        s = self.store
        keyword = display_name.strip()
        alias_old_names = {
            row.get("old_name")
            for row in s.rows("experiment_alias")
            if row.get("new_name") == keyword or row.get("old_name") == keyword
        }
        names = {keyword, *alias_old_names}
        tasks = [
            row for row in s.rows("legacy_task")
            if row.get("display_name") in names
            and (not version or row.get("version") == version)
        ]
        if not tasks:
            raise MigrationError("旧版入口查不到该显示名/版本对应的历史检验",
                                 code="LEGACY_NOT_FOUND", status_code=404)
        if not version and len({row.get("version") for row in tasks}) > 1:
            raise MigrationError(
                "存在两个同名实验，旧版入口要求显式指定版本，禁止混算",
                code="AMBIGUOUS_NAME",
            )
        results = []
        for task in tasks:
            if credential and str(task.get("credential", "")) != credential.strip():
                raise MigrationError(
                    f"凭据与历史检验 {task.get('task_code')} 登记不一致，拒绝复核",
                    code="BAD_CREDENTIAL", status_code=403,
                )
            batch_no = f"LEGACY-{task['task_code']}"
            claimed: list[str] = []
            for sample_code in task.get("sample_codes", []):
                _, is_new = s.claim_sample(
                    sample_code=sample_code,
                    scope=self.LEGACY_SCOPE,
                    batch_no=batch_no,
                    batch_id=task["id"],
                    legacy_task_code=task["task_code"],
                )
                if is_new:
                    claimed.append(sample_code)
            results.append({
                "task_code": task["task_code"],
                "version": task["version"],
                "group_no": task.get("group_no", ""),
                "method": task.get("method"),
                "result": task.get("result"),
                "credential": task.get("credential"),
                "sample_codes": task.get("sample_codes", []),
                "newly_claimed": claimed,
            })
        return {
            "ok": True,
            "entry_mode": s.state_get("entry_mode", "new"),
            "partition_by_version": True,
            "results": results,
        }

    # ------------------------------------------------------------------
    # 应急入口切换
    # ------------------------------------------------------------------
    def set_entry_mode(self, mode: str, *, reason: str) -> dict[str, Any]:
        s = self.store
        mode = mode.strip().lower()
        if mode not in ("new", "legacy"):
            raise MigrationError("入口模式只允许 new 或 legacy", code="BAD_MODE",
                                 status_code=400)
        previous = s.state_get("entry_mode", "new")
        s.state_set("entry_mode", mode)
        log_row = {
            "id": s.next_id("entry_mode_log"),
            "mode": mode,
            "previous_mode": previous,
            "reason": reason.strip() or "值班员手动切换",
            "changed_at": _now(),
        }
        s.rows("entry_mode_log").append(log_row)
        return {"ok": True, "entry_mode": mode, "previous_mode": previous, "log": log_row}
