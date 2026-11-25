"""实验名称关联迁移演练端到端测试。

完整复刻上线演练剧本：
两个同名实验 A（带可核对组别号 G01）/ B（仅有显示名）→ 迁移与重试幂等 →
B 待核查期间提交 A 的新版评估并查询受理/未受理原因 → 补齐 B → 改名不影响归属 →
回滚旧版入口应急、原凭据复核、旧版同名结果不混淆。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.migration_seed import SAME_DISPLAY_NAME
from app.migration_store import migration_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_scenario():
    migration_store.reset()
    yield
    migration_store.reset()


def _run(batch_no: str = "MIG-BATCH-001"):
    return client.post("/api/migration/run", json={"batch_no": batch_no, "operator": "值班员"})


class TestMigrationBaseline:
    def test_initial_mapping_empty_and_two_same_name_experiments(self):
        snap = client.get("/api/migration/snapshot").json()
        assert snap["entry_mode"] == "new"
        assert len(snap["group_version_mapping"]) == 0
        names = {row["display_name"] for row in snap["experiments"]}
        assert names == {SAME_DISPLAY_NAME}
        versions = sorted(row["version"] for row in snap["experiments"])
        assert versions == ["v1", "v2"]

    def test_migration_registers_a_mapping_and_keeps_b_pending(self):
        resp = _run()
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["migrated"]) == 1
        assert data["migrated"][0]["legacy_task_code"] == "L-TASK-101"
        assert data["migrated"][0]["exp_code"] == "EXP-HOME-001"
        assert data["migrated"][0]["group_code"] == "GRP-A01"

        pending = data["pending_review"]
        assert len(pending) == 1
        assert pending[0]["legacy_task_code"] == "L-TASK-202"
        assert "无法核对" in pending[0]["review_reason"]

        snap = client.get("/api/migration/snapshot").json()
        # 只登记 A 一条映射，不能给 B 猜一个组。
        assert snap["summary"]["mapping_count"] == 1
        assert snap["summary"]["pending_review"] == 1
        assert snap["summary"]["migrated_legacy"] == 1
        link_b = next(row for row in snap["legacy_links"] if row["legacy_task_code"] == "L-TASK-202")
        assert link_b["link_status"] == "待核查"
        assert link_b["migration_batch_no"] == ""

    def test_retry_same_batch_is_idempotent_and_never_double_books(self):
        first = _run().json()
        second = _run().json()
        # 第二次是同一批次的重试：幂等返回，A 不会再产生新的迁移登记。
        assert second["idempotent"] is True
        assert len(second["migrated"]) == 1
        assert len(second["pending_review"]) == 1

        snap = client.get("/api/migration/snapshot").json()
        migration_claims = [
            c for c in snap["sample_claims"] if c["scope"] == "migration"
        ]
        # A 的 4 个历史样本各只被领取一次，没有样本出现在两个批次。
        assert len(migration_claims) == 4
        batch_nos = {c["batch_no"] for c in migration_claims}
        assert batch_nos == {"MIG-BATCH-001"}
        ledger = snap["migration_ledger"]
        assert len(ledger) == 2
        assert sum(1 for r in ledger if r["mapping_status"] == "已核实归属") == 1

    def test_another_batch_still_cannot_claim_already_migrated_history(self):
        _run()
        resp = client.post("/api/migration/run", json={"batch_no": "MIG-BATCH-002"})
        data = resp.json()
        # 新批次里 A 已迁移，只能看到待核查的 B，A 的检验不会进第二个批次。
        assert data["idempotent"] is False
        assert data["migrated"] == []
        assert len(data["pending_review"]) == 1
        snap = client.get("/api/migration/snapshot").json()
        migration_claims = [c for c in snap["sample_claims"] if c["scope"] == "migration"]
        assert all(c["batch_no"] == "MIG-BATCH-001" for c in migration_claims)


class TestNewEvaluation:
    def test_a_evaluation_accepted_with_claimed_samples(self):
        _run()
        resp = client.post("/api/migration/evaluation/submit", json={
            "exp_code": "EXP-HOME-001",
            "group_code": "GRP-A01",
            "version": "v1",
            "idem_key": "EVAL-A-RERUN-01",
            "credential": "cred-A-101",
        })
        data = resp.json()
        assert resp.status_code == 200
        assert data["ok"] is True and data["accepted"] is True
        assert sorted(data["claimed_samples"]) == [
            "S-A-1001", "S-A-1002", "S-A-1003", "S-A-1004",
        ]
        assert data["rejected_samples"] == []
        assert data["batch"]["group_no"] == "G01"

    def test_b_evaluation_rejected_with_explicit_reason_while_pending(self):
        _run()
        resp = client.post("/api/migration/evaluation/submit", json={
            "exp_code": "EXP-HOME-002",
            "group_code": "GRP-B02-G02",
            "version": "v2",
            "idem_key": "EVAL-B-BLOCKED-01",
            "credential": "cred-B-202",
        })
        data = resp.json()
        assert data["ok"] is False
        assert data["accepted"] is False
        assert "映射" in data["reason"]
        # 未受理时不能产生任何评估批次与样本领取。
        snap = client.get("/api/migration/snapshot").json()
        assert snap["summary"]["evaluation_batches"] == 0
        assert all(c["scope"] != "evaluation" for c in snap["sample_claims"])

    def test_evaluation_idempotency_and_sample_not_double_claimed(self):
        _run()
        payload = {
            "exp_code": "EXP-HOME-001",
            "group_code": "GRP-A01",
            "version": "v1",
            "idem_key": "EVAL-A-RERUN-01",
            "credential": "cred-A-101",
        }
        first = client.post("/api/migration/evaluation/submit", json=payload).json()
        second = client.post("/api/migration/evaluation/submit", json=payload).json()
        assert second["idempotent"] is True
        assert second["batch"]["batch_no"] == first["batch"]["batch_no"]
        assert len(second["claimed_samples"]) == 4

        # 换一个幂等键重跑同一批样本：原样本已被前批次领取，不能再受理。
        conflict = client.post("/api/migration/evaluation/submit", json={
            **payload, "idem_key": "EVAL-A-RERUN-02",
        }).json()
        assert conflict["accepted"] is False
        assert len(conflict["claimed_samples"]) == 0
        assert len(conflict["rejected_samples"]) == 4
        assert all("已被评估批次" in r["reason"] for r in conflict["rejected_samples"])

        snap = client.get("/api/migration/snapshot").json()
        eval_claims = [c for c in snap["sample_claims"] if c["scope"] == "evaluation"]
        assert len(eval_claims) == 4
        assert len({c["batch_no"] for c in eval_claims}) == 1

    def test_verify_with_original_credential_returns_group_and_samples(self):
        _run()
        batch_no = client.post("/api/migration/evaluation/submit", json={
            "exp_code": "EXP-HOME-001",
            "group_code": "GRP-A01",
            "version": "v1",
            "idem_key": "EVAL-A-RERUN-01",
            "credential": "cred-A-101",
        }).json()["batch"]["batch_no"]

        ok = client.post(f"/api/migration/evaluation/{batch_no}/verify",
                         json={"credential": "cred-A-101"})
        assert ok.status_code == 200
        data = ok.json()
        assert data["group_used"]["group_no"] == "G01"
        assert data["group_used"]["exp_code"] == "EXP-HOME-001"
        assert len(data["samples"]) == 4

        bad = client.post(f"/api/migration/evaluation/{batch_no}/verify",
                          json={"credential": "wrong-cred"})
        assert bad.status_code == 403
        assert bad.json()["code"] == "BAD_CREDENTIAL"


class TestCompleteBAndRename:
    def test_complete_b_evidence_then_full_migration_and_two_partitions(self):
        _run()
        # 补齐 B 的组别证据（G02）。
        comp = client.post("/api/migration/links/2/complete-group", json={
            "group_no": "G02",
            "evidence_ref": "人工核查工单#7788",
            "group_code": "GRP-B02-G02",
        })
        assert comp.status_code == 200
        assert comp.json()["evidence"]["verified"] is True

        # 重跑同一批次：B 此时可唯一证明归属，映射补齐。
        rerun = _run().json()
        assert len(rerun["migrated"]) == 2
        b_ledger = next(r for r in rerun["migrated"] if r["legacy_task_code"] == "L-TASK-202")
        assert b_ledger["exp_code"] == "EXP-HOME-002"
        assert b_ledger["group_code"] == "GRP-B02-G02"

        snap = client.get("/api/migration/snapshot").json()
        assert snap["summary"]["mapping_count"] == 2
        assert snap["summary"]["pending_review"] == 0
        # 两个版本的样本各归各的批次，合计 8 个样本、无重复领取。
        migration_claims = [c for c in snap["sample_claims"] if c["scope"] == "migration"]
        assert len(migration_claims) == 8
        assert len({c["sample_code"] for c in migration_claims}) == 8

        # B 的新版评估现在可以受理。
        b_eval = client.post("/api/migration/evaluation/submit", json={
            "exp_code": "EXP-HOME-002",
            "group_code": "GRP-B02-G02",
            "version": "v2",
            "idem_key": "EVAL-B-OK-01",
            "credential": "cred-B-202",
        }).json()
        assert b_eval["accepted"] is True
        assert sorted(b_eval["claimed_samples"]) == [
            "S-B-2001", "S-B-2002", "S-B-2003", "S-B-2004",
        ]

    def test_group_no_cannot_be_assigned_to_two_experiments(self):
        # G01 已核实属于 EXP-HOME-001，不能再补到 B 上。
        resp = client.post("/api/migration/links/2/complete-group", json={
            "group_no": "G01",
            "evidence_ref": "伪造凭据",
        })
        assert resp.status_code == 409
        assert resp.json()["code"] == "GROUP_NO_CONFLICT"

    def test_rename_does_not_move_history_and_old_name_still_searchable(self):
        _run()
        renamed = client.post("/api/migration/experiment/rename", json={
            "exp_code": "EXP-HOME-001",
            "new_name": "首页推荐流灰度实验",
        })
        assert renamed.status_code == 200
        snap = client.get("/api/migration/snapshot").json()
        exp_a = next(row for row in snap["experiments"] if row["exp_code"] == "EXP-HOME-001")
        assert exp_a["display_name"] == "首页推荐流灰度实验"

        # 映射表按稳定标识登记，改名后仍然在，归属没有被移动。
        mapping = next(
            row for row in snap["group_version_mapping"]
            if row["exp_code"] == "EXP-HOME-001"
        )
        assert mapping["group_code"] == "GRP-A01"

        # 按旧显示名仍能查到 A 的历史检验。
        legacy = client.get(f"/api/migration/legacy/search?display_name={SAME_DISPLAY_NAME}")
        data = legacy.json()
        assert data["total"] >= 1
        all_task_codes = {
            t["task_code"]
            for group in data["groups_by_version"]
            for t in group["tasks"]
        }
        assert "L-TASK-101" in all_task_codes
        assert SAME_DISPLAY_NAME in data["matched_names"]

        # 新版评估用的仍是稳定标识，改名不影响受理结果。
        eval_after = client.post("/api/migration/evaluation/submit", json={
            "exp_code": "EXP-HOME-001",
            "group_code": "GRP-A01",
            "version": "v1",
            "idem_key": "EVAL-A-AFTER-RENAME",
            "credential": "cred-A-101",
        }).json()
        assert eval_after["accepted"] is True


class TestRollbackAndLegacyEntry:
    def test_switch_to_legacy_blocks_new_evaluation_and_is_logged(self):
        _run()
        resp = client.post("/api/migration/entry-mode", json={
            "mode": "legacy", "reason": "值班应急回滚",
        })
        assert resp.json()["entry_mode"] == "legacy"
        blocked = client.post("/api/migration/evaluation/submit", json={
            "exp_code": "EXP-HOME-001",
            "group_code": "GRP-A01",
            "version": "v1",
            "idem_key": "EVAL-A-IN-LEGACY",
            "credential": "cred-A-101",
        })
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "ENTRY_LEGACY_MODE"

        snap = client.get("/api/migration/snapshot").json()
        assert snap["entry_mode"] == "legacy"
        assert snap["entry_mode_log"][-1]["reason"] == "值班应急回滚"

    def test_after_rollback_credential_review_still_returns_group_and_samples(self):
        _run()
        batch_no = client.post("/api/migration/evaluation/submit", json={
            "exp_code": "EXP-HOME-001",
            "group_code": "GRP-A01",
            "version": "v1",
            "idem_key": "EVAL-A-RERUN-01",
            "credential": "cred-A-101",
        }).json()["batch"]["batch_no"]

        client.post("/api/migration/entry-mode", json={"mode": "legacy", "reason": "应急"})
        # 回滚不影响已完成的新版复核：原凭据仍能查到组别和样本。
        verify = client.post(f"/api/migration/evaluation/{batch_no}/verify",
                             json={"credential": "cred-A-101"})
        assert verify.status_code == 200
        data = verify.json()
        assert data["group_used"]["group_code"] == "GRP-A01"
        assert len(data["samples"]) == 4

    def test_legacy_evaluate_requires_version_and_never_mixes_same_names(self):
        _run()
        # 只给显示名、不给版本：存在两个同名实验，必须拒绝混算。
        ambiguous = client.post("/api/migration/legacy/evaluate", json={
            "display_name": SAME_DISPLAY_NAME,
        })
        assert ambiguous.status_code == 409
        assert ambiguous.json()["code"] == "AMBIGUOUS_NAME"

        a_resp = client.post("/api/migration/legacy/evaluate", json={
            "display_name": SAME_DISPLAY_NAME, "version": "v1",
            "credential": "cred-A-101",
        })
        b_resp = client.post("/api/migration/legacy/evaluate", json={
            "display_name": SAME_DISPLAY_NAME, "version": "v2",
            "credential": "cred-B-202",
        })
        a_data = a_resp.json()
        b_data = b_resp.json()
        assert a_data["partition_by_version"] is True
        assert {r["version"] for r in a_data["results"]} == {"v1"}
        assert {r["version"] for r in b_data["results"]} == {"v2"}
        assert all(r["group_no"] == "G01" for r in a_data["results"])
        assert all(r["group_no"] == "" for r in b_data["results"])

        # 错误凭据拒绝复核。
        bad = client.post("/api/migration/legacy/evaluate", json={
            "display_name": SAME_DISPLAY_NAME, "version": "v1",
            "credential": "nope",
        })
        assert bad.status_code == 403
        assert bad.json()["code"] == "BAD_CREDENTIAL"

    def test_legacy_search_partitions_two_same_name_experiments(self):
        _run()
        data = client.get(f"/api/migration/legacy/search?display_name={SAME_DISPLAY_NAME}").json()
        versions = {group["version"] for group in data["groups_by_version"]}
        assert versions == {"v1", "v2"}
        for group in data["groups_by_version"]:
            # 每个版本分组里只允许出现本版本的检验。
            assert all(t["task_code"].startswith("L-TASK-") for group in [group] for t in group["tasks"])
        v1 = next(g for g in data["groups_by_version"] if g["version"] == "v1")
        v2 = next(g for g in data["groups_by_version"] if g["version"] == "v2")
        assert v1["tasks"][0]["task_code"] == "L-TASK-101"
        assert v2["tasks"][0]["task_code"] == "L-TASK-202"

    def test_switch_back_to_new_resumes_new_evaluation(self):
        client.post("/api/migration/entry-mode", json={"mode": "legacy", "reason": "应急"})
        client.post("/api/migration/entry-mode", json={"mode": "new", "reason": "故障恢复"})
        _run()
        resp = client.post("/api/migration/evaluation/submit", json={
            "exp_code": "EXP-HOME-001",
            "group_code": "GRP-A01",
            "version": "v1",
            "idem_key": "EVAL-A-RESUME",
            "credential": "cred-A-101",
        })
        assert resp.json()["accepted"] is True
