"""实验名称关联迁移演练接口。

路由层只负责参数接收与错误转译，所有业务判定都在 MigrationService 内完成，
符合「状态流转只允许在 services 里改，路由层不做业务判断」的项目约定。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.migration import MigrationError, MigrationService

router = APIRouter(prefix="/api/migration", tags=["实验关联迁移演练"])
service = MigrationService()


def _error(exc: MigrationError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "code": exc.code, "message": exc.message},
    )


class MigrationRunPayload(BaseModel):
    batch_no: str = Field(..., description="迁移批次号，重试使用同一批次号")
    operator: str = "值班员"


class EvidencePayload(BaseModel):
    group_no: str
    evidence_ref: str
    group_code: str = ""


class EvaluationPayload(BaseModel):
    exp_code: str
    group_code: str
    version: str
    idem_key: str
    credential: str
    sample_codes: list[str] | None = None


class VerifyPayload(BaseModel):
    credential: str


class RenamePayload(BaseModel):
    exp_code: str
    new_name: str


class EntryModePayload(BaseModel):
    mode: str
    reason: str = ""


class LegacyEvaluatePayload(BaseModel):
    display_name: str
    version: str = ""
    credential: str = ""


@router.get("/snapshot")
def snapshot() -> dict[str, Any]:
    """演练总览：映射表、迁移台账、评估批次、样本领取与入口模式一站式查看。"""
    return service.snapshot()


@router.post("/run")
def run_migration(payload: MigrationRunPayload) -> dict[str, Any]:
    """执行（或幂等重试）存量名称关联 → 组别版本映射迁移。"""
    try:
        return service.run_migration(payload.batch_no, operator=payload.operator)
    except MigrationError as exc:
        return _error(exc)


@router.post("/links/{link_id}/complete-group")
def complete_group(link_id: int, payload: EvidencePayload) -> dict[str, Any]:
    """人工核查补齐 B 的组别证据，回填稳定组别标识。"""
    try:
        return service.complete_group_evidence(
            link_id,
            group_no=payload.group_no,
            evidence_ref=payload.evidence_ref,
            group_code=payload.group_code,
        )
    except MigrationError as exc:
        return _error(exc)


@router.post("/evaluation/submit")
def submit_evaluation(payload: EvaluationPayload) -> dict[str, Any]:
    """提交新版评估：查映射表决定能否领取样本，幂等键防重复受理。"""
    try:
        return service.submit_evaluation(
            exp_code=payload.exp_code,
            group_code=payload.group_code,
            version=payload.version,
            idem_key=payload.idem_key,
            credential=payload.credential,
            sample_codes=payload.sample_codes,
        )
    except MigrationError as exc:
        return _error(exc)


@router.post("/evaluation/{batch_no}/verify")
def verify_evaluation(batch_no: str, payload: VerifyPayload) -> dict[str, Any]:
    """按原凭据复核新版评估，回查所用组别与已领取样本。"""
    try:
        return service.verify_evaluation(batch_no, payload.credential)
    except MigrationError as exc:
        return _error(exc)


@router.post("/experiment/rename")
def rename_experiment(payload: RenamePayload) -> dict[str, Any]:
    """实验改名：稳定标识与映射不变，登记旧名别名供历史检索。"""
    try:
        return service.rename_experiment(payload.exp_code, payload.new_name)
    except MigrationError as exc:
        return _error(exc)


@router.get("/legacy/search")
def legacy_search(display_name: str) -> dict[str, Any]:
    """按旧显示名（含改名前别名）查历史检验，结果按版本隔离。"""
    try:
        return service.legacy_search(display_name)
    except MigrationError as exc:
        return _error(exc)


@router.post("/legacy/evaluate")
def legacy_evaluate(payload: LegacyEvaluatePayload) -> dict[str, Any]:
    """旧版入口应急评估：必须按版本隔离，同名实验不指定版本直接拒绝。"""
    try:
        return service.legacy_evaluate(
            display_name=payload.display_name,
            version=payload.version,
            credential=payload.credential,
        )
    except MigrationError as exc:
        return _error(exc)


@router.post("/entry-mode")
def set_entry_mode(payload: EntryModePayload) -> dict[str, Any]:
    """值班员应急切换新版/旧版入口并留痕。"""
    try:
        return service.set_entry_mode(payload.mode, reason=payload.reason)
    except MigrationError as exc:
        return _error(exc)
