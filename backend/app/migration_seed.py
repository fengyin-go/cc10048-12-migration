"""迁移演练情景数据（独立于 18 个业务模块的内存表）。

背景：存量显著性检验只靠「实验显示名称」关联，存在两个同名、不同版本的实验。
- A（v1）：存量关联里带可核对的组别号 G01，能在组别证据底账中核对到稳定标识；
- B（v2）：只写了显示名，没有组别号，组别归属待核查，迁移时不许猜组。

新版只认稳定的实验标识 exp_code 与组别标识 group_code，组别版本映射表
group_version_mapping 初始为空，由迁移过程登记，实验改名也不影响这些标识。
"""
from __future__ import annotations

from typing import Any

SAME_DISPLAY_NAME = "首页推荐流改版实验"

# 新版实验台账：稳定实验标识 + 稳定组别标识，显示名可改、标识不变。
SCENARIO_EXPERIMENTS: list[dict[str, Any]] = [
    {
        "id": 1,
        "exp_code": "EXP-HOME-001",
        "display_name": SAME_DISPLAY_NAME,
        "version": "v1",
        "group_code": "GRP-A01",
        "group_no": "G01",
        "group_name": "对照组A",
        "status": "运行中",
    },
    {
        "id": 2,
        "exp_code": "EXP-HOME-002",
        "display_name": SAME_DISPLAY_NAME,
        "version": "v2",
        # B 的组别证据待核查，新版标识先留空，补齐后再回填。
        "group_code": "",
        "group_no": "",
        "group_name": "实验组B（组别待核查）",
        "status": "运行中",
    },
]

# 组别证据底账：组别号能否核对到稳定实验/组别标识，以这里的已核实记录为准。
# 上线时只有 G01 可核对；G02 等同事补齐 B 的核查后才登记进来。
GROUP_EVIDENCE: list[dict[str, Any]] = [
    {
        "id": 1,
        "group_no": "G01",
        "exp_code": "EXP-HOME-001",
        "group_code": "GRP-A01",
        "group_name": "对照组A",
        "evidence_ref": "组别登记底账#4401",
        "verified": True,
    },
]

# 存量名称关联：旧版只存显示名。A 附了可核对组别号，B 只有显示名。
LEGACY_LINKS: list[dict[str, Any]] = [
    {
        "id": 1,
        "legacy_task_code": "L-TASK-101",
        "display_name": SAME_DISPLAY_NAME,
        "version": "v1",
        "group_no": "G01",
        "evidence_note": "随单留存组别号，可核对",
        "link_status": "待迁移",
        "migration_batch_no": "",
        "review_reason": "",
    },
    {
        "id": 2,
        "legacy_task_code": "L-TASK-202",
        "display_name": SAME_DISPLAY_NAME,
        "version": "v2",
        "group_no": "",
        "evidence_note": "仅填写实验显示名，无组别号",
        "link_status": "待迁移",
        "migration_batch_no": "",
        "review_reason": "",
    },
]

# 存量历史检验：旧凭据仍可复核，回滚后也要能查到当时用的组别和样本。
LEGACY_TASKS: list[dict[str, Any]] = [
    {
        "id": 1,
        "task_code": "L-TASK-101",
        "display_name": SAME_DISPLAY_NAME,
        "version": "v1",
        "group_no": "G01",
        "method": "双样本T检验",
        "result": "显著",
        "ran_at": "2026-08-10 10:00:00",
        "credential": "cred-A-101",
        "sample_codes": ["S-A-1001", "S-A-1002", "S-A-1003", "S-A-1004"],
    },
    {
        "id": 2,
        "task_code": "L-TASK-202",
        "display_name": SAME_DISPLAY_NAME,
        "version": "v2",
        "group_no": "",
        "method": "双样本T检验",
        "result": "不显著",
        "ran_at": "2026-08-20 15:30:00",
        "credential": "cred-B-202",
        "sample_codes": ["S-B-2001", "S-B-2002", "S-B-2003", "S-B-2004"],
    },
]

# 存量样本池：A 的样本带组别号，B 的样本只能证明同名、无法证明组别归属。
SAMPLE_POOL: list[dict[str, Any]] = [
    {
        "id": 1, "sample_code": "S-A-1001", "display_name": SAME_DISPLAY_NAME,
        "version": "v1", "group_no": "G01",
    },
    {
        "id": 2, "sample_code": "S-A-1002", "display_name": SAME_DISPLAY_NAME,
        "version": "v1", "group_no": "G01",
    },
    {
        "id": 3, "sample_code": "S-A-1003", "display_name": SAME_DISPLAY_NAME,
        "version": "v1", "group_no": "G01",
    },
    {
        "id": 4, "sample_code": "S-A-1004", "display_name": SAME_DISPLAY_NAME,
        "version": "v1", "group_no": "G01",
    },
    {
        "id": 5, "sample_code": "S-B-2001", "display_name": SAME_DISPLAY_NAME,
        "version": "v2", "group_no": "",
    },
    {
        "id": 6, "sample_code": "S-B-2002", "display_name": SAME_DISPLAY_NAME,
        "version": "v2", "group_no": "",
    },
    {
        "id": 7, "sample_code": "S-B-2003", "display_name": SAME_DISPLAY_NAME,
        "version": "v2", "group_no": "",
    },
    {
        "id": 8, "sample_code": "S-B-2004", "display_name": SAME_DISPLAY_NAME,
        "version": "v2", "group_no": "",
    },
]

# 组别版本映射表：新版评估唯一认可的「实验标识 × 组别标识 × 版本」登记表，迁移前为空。
GROUP_VERSION_MAPPING: list[dict[str, Any]] = []

# 迁移批次与迁移台账：台账对存量检验唯一，重试不会把同一检验记进两个批次。
MIGRATION_BATCHES: list[dict[str, Any]] = []
MIGRATION_LEDGER: list[dict[str, Any]] = []

# 新版评估批次与样本领取台账：样本全局只能被一个评估批次领取。
EVALUATION_BATCHES: list[dict[str, Any]] = []
SAMPLE_CLAIMS: list[dict[str, Any]] = []

# 实验改名别名：旧显示名 → 新显示名，保证按旧名仍能查历史。
EXPERIMENT_ALIASES: list[dict[str, Any]] = []

# 入口切换留痕。
ENTRY_MODE_LOG: list[dict[str, Any]] = [
    {"id": 1, "mode": "new", "reason": "新版上线默认入口", "changed_at": "2026-11-25 09:00:00"},
]

# 应用级键值状态（当前入口模式）。
APP_STATE: list[dict[str, Any]] = [
    {"key": "entry_mode", "value": "new"},
]


def build_migration_tables() -> dict[str, list[dict[str, Any]]]:
    """每次深拷贝一份情景表，保证测试间互不污染。"""
    source = {
        "scenario_experiment": SCENARIO_EXPERIMENTS,
        "group_evidence": GROUP_EVIDENCE,
        "legacy_link": LEGACY_LINKS,
        "legacy_task": LEGACY_TASKS,
        "sample_pool": SAMPLE_POOL,
        "group_version_mapping": GROUP_VERSION_MAPPING,
        "migration_batch": MIGRATION_BATCHES,
        "migration_ledger": MIGRATION_LEDGER,
        "evaluation_batch": EVALUATION_BATCHES,
        "sample_claim": SAMPLE_CLAIMS,
        "experiment_alias": EXPERIMENT_ALIASES,
        "entry_mode_log": ENTRY_MODE_LOG,
        "app_state": APP_STATE,
    }
    return {name: [dict(row) for row in rows] for name, rows in source.items()}
