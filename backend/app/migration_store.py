"""迁移演练内存仓库：独立于业务模块 Store，承载组别版本映射与迁移/评估台账。

设计要点：
- 所有写入走 insert_* / claim_*，由仓库层兜底唯一约束，防止重试把同一份
  存量检验或同一个样本记到两个新版批次；
- 提供 reset() 深拷贝重建情景数据，测试之间互不污染；
- 不改变 app.store.Store 的模块计数，18 个业务模块的既有测试不受影响。
"""
from __future__ import annotations

import threading
from typing import Any

from app.migration_seed import build_migration_tables


class MigrationStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tables: dict[str, list[dict[str, Any]]] = build_migration_tables()

    # ---- 基础读写 ----
    def reset(self) -> None:
        with self._lock:
            self._tables = build_migration_tables()

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(table, [])

    def find(self, table: str, row_id: int) -> dict[str, Any] | None:
        for row in self.rows(table):
            if int(row.get("id", 0)) == row_id:
                return row
        return None

    def next_id(self, table: str) -> int:
        return max((int(row.get("id", 0)) for row in self.rows(table)), default=0) + 1

    def state_get(self, key: str, default: str = "") -> str:
        for row in self.rows("app_state"):
            if row.get("key") == key:
                return str(row.get("value", default))
        return default

    def state_set(self, key: str, value: str) -> None:
        for row in self.rows("app_state"):
            if row.get("key") == key:
                row["value"] = value
                return
        self.rows("app_state").append({"key": key, "value": value})

    # ---- 带唯一约束的登记 ----
    def insert_unique(
        self,
        table: str,
        unique: dict[str, Any],
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """按 unique 字段判重后插入。返回 (行, 是否新建)；已存在则原样返回。"""
        with self._lock:
            for row in self.rows(table):
                if all(row.get(k) == v for k, v in unique.items()):
                    return row, False
            entry = {"id": self.next_id(table), **payload}
            self.rows(table).append(entry)
            return entry, True

    def upsert_unique(
        self,
        table: str,
        unique: dict[str, Any],
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """按 unique 字段判重：不存在则插入，已存在则用 payload 原地更新。

        用于待核查项补齐证据后的台账回填，保证同一存量检验始终只有一条台账。
        """
        with self._lock:
            for row in self.rows(table):
                if all(row.get(k) == v for k, v in unique.items()):
                    row.update(payload)
                    return row, False
            entry = {"id": self.next_id(table), **payload}
            self.rows(table).append(entry)
            return entry, True

    def claim_sample(
        self,
        *,
        sample_code: str,
        scope: str,
        batch_no: str,
        batch_id: int,
        legacy_task_code: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """在指定作用域内领取样本；同一样本在同一作用域只能被一个批次领取。

        迁移作用域与评估作用域各自独立，但作用域内全局唯一，因此同一份历史
        样本不会被两次迁移或两个评估批次重复受理。
        """
        with self._lock:
            for claim in self.rows("sample_claim"):
                if claim.get("scope") == scope and claim.get("sample_code") == sample_code:
                    return claim, False
            entry = {
                "id": self.next_id("sample_claim"),
                "sample_code": sample_code,
                "scope": scope,
                "batch_no": batch_no,
                "batch_id": batch_id,
                "legacy_task_code": legacy_task_code,
            }
            self.rows("sample_claim").append(entry)
            return entry, True

    def claims_for_batch(self, batch_no: str, scope: str) -> list[dict[str, Any]]:
        return [
            row for row in self.rows("sample_claim")
            if row.get("batch_no") == batch_no and row.get("scope") == scope
        ]

    def sample_owner(self, sample_code: str, scope: str) -> dict[str, Any] | None:
        for claim in self.rows("sample_claim"):
            if claim.get("scope") == scope and claim.get("sample_code") == sample_code:
                return claim
        return None


migration_store = MigrationStore()
