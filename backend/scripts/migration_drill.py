"""上线演练剧本：用真实 HTTP 请求走完「同名实验 A/B 迁移与评估」全流程。

运行：.venv/bin/python -I scripts/migration_drill.py
每一步都打印关键判定，便于值班员人工核对。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8012/api/migration"
DISPLAY_NAME = "首页推荐流改版实验"


def call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def step(title: str) -> None:
    print(f"\n===== {title} =====")


def main() -> None:
    step("0. 初始状态：映射表为空，存在两个同名实验")
    _, snap = call("GET", "/snapshot")
    print("入口模式:", snap["entry_mode"])
    print("同名实验:", [(e["exp_code"], e["version"], e["group_no"] or "组别待核查")
                       for e in snap["experiments"]])
    print("组别版本映射条数:", len(snap["group_version_mapping"]))

    step("1. 启动迁移（批次 MIG-BATCH-001）：A 可核实、B 待核查")
    _, r = call("POST", "/run", {"batch_no": "MIG-BATCH-001"})
    print("已迁移:", [(m["legacy_task_code"], m["exp_code"], m["group_code"])
                     for m in r["migrated"]])
    print("待核查:", [(m["legacy_task_code"], m["review_reason"])
                     for m in r["pending_review"]])
    print("迁移领取样本:", r["claimed_samples"])

    step("2. 同一批次重试：幂等，不重复记账")
    _, r2 = call("POST", "/run", {"batch_no": "MIG-BATCH-001"})
    print("idempotent =", r2["idempotent"], "| 已迁移条数 =", len(r2["migrated"]),
          "| 待核查条数 =", len(r2["pending_review"]))

    step("3. 换批次号重试：A 不会被记到第二个批次")
    _, r3 = call("POST", "/run", {"batch_no": "MIG-BATCH-002"})
    print("本批新迁移 =", r3["migrated"])
    print("已在他批迁移（只回显不重记） =", r3["already_migrated_elsewhere"])

    step("4. 提交 A 的新版评估：按映射表受理并领取样本")
    _, a = call("POST", "/evaluation/submit", {
        "exp_code": "EXP-HOME-001", "group_code": "GRP-A01", "version": "v1",
        "idem_key": "EVAL-A-01", "credential": "cred-A-101",
    })
    batch_no = a["batch"]["batch_no"]
    print("受理状态:", a["accepted"], "| 批次:", batch_no)
    print("领取样本:", sorted(a["claimed_samples"]), "| 退回:", a["rejected_samples"])

    step("5. 同幂等键重试 → 返回原批次；换幂等键重跑 → 样本已被领取")
    _, a_retry = call("POST", "/evaluation/submit", {
        "exp_code": "EXP-HOME-001", "group_code": "GRP-A01", "version": "v1",
        "idem_key": "EVAL-A-01", "credential": "cred-A-101",
    })
    print("重试 idempotent =", a_retry["idempotent"], "| 同批次 =",
          a_retry["batch"]["batch_no"] == batch_no)
    _, a_conflict = call("POST", "/evaluation/submit", {
        "exp_code": "EXP-HOME-001", "group_code": "GRP-A01", "version": "v1",
        "idem_key": "EVAL-A-02", "credential": "cred-A-101",
    })
    print("二次受理:", a_conflict["accepted"], "| 退回数:",
          len(a_conflict["rejected_samples"]), "| 原因示例:",
          a_conflict["rejected_samples"][0]["reason"])

    step("6. B 组别待核查期间提交评估：明确未受理原因")
    code, b_blocked = call("POST", "/evaluation/submit", {
        "exp_code": "EXP-HOME-002", "group_code": "GRP-B02-G02", "version": "v2",
        "idem_key": "EVAL-B-01", "credential": "cred-B-202",
    })
    print("HTTP", code, "| accepted =", b_blocked["accepted"], "| 原因:", b_blocked["reason"])

    step("7. 实验 A 改名：稳定标识与映射不变，旧名仍可查历史")
    _, renamed = call("POST", "/experiment/rename", {
        "exp_code": "EXP-HOME-001", "new_name": "首页推荐流灰度实验",
    })
    print("改名后显示名:", renamed["experiment"]["display_name"],
          "| exp_code 不变:", renamed["experiment"]["exp_code"])
    _, legacy = call("GET", f"/legacy/search?display_name={urllib.parse.quote(DISPLAY_NAME)}")
    print("按旧名命中版本:", [g["version"] for g in legacy["groups_by_version"]],
          "| 匹配名:", legacy["matched_names"])

    step("8. 补齐 B 的组别证据 G02，重跑迁移完成两组归属")
    code, comp = call("POST", "/links/2/complete-group", {
        "group_no": "G02", "evidence_ref": "人工核查工单#7788",
        "group_code": "GRP-B02-G02",
    })
    print("补齐证据 HTTP", code, "| verified =", comp["evidence"]["verified"])
    _, rerun = call("POST", "/run", {"batch_no": "MIG-BATCH-001"})
    print("迁移后已核实条数:", len(rerun["migrated"]),
          "| 待核查:", len(rerun["pending_review"]))
    b_ledger = next(m for m in rerun["migrated"] if m["legacy_task_code"] == "L-TASK-202")
    print("B 归属:", b_ledger["exp_code"], b_ledger["group_code"])
    _, b_eval = call("POST", "/evaluation/submit", {
        "exp_code": "EXP-HOME-002", "group_code": "GRP-B02-G02", "version": "v2",
        "idem_key": "EVAL-B-02", "credential": "cred-B-202",
    })
    print("B 新版评估受理:", b_eval["accepted"],
          "| 样本:", sorted(b_eval["claimed_samples"]))

    step("9. 回滚旧版入口应急")
    _, mode = call("POST", "/entry-mode", {"mode": "legacy", "reason": "值班应急回滚"})
    print("入口切换:", mode["previous_mode"], "->", mode["entry_mode"])
    code, blocked = call("POST", "/evaluation/submit", {
        "exp_code": "EXP-HOME-001", "group_code": "GRP-A01", "version": "v1",
        "idem_key": "EVAL-A-LEGACY", "credential": "cred-A-101",
    })
    print("旧版入口下新评估 HTTP", code, "|", blocked["code"])

    step("10. 回滚后按原凭据复核已完成的新版检验")
    code, verify = call("POST", f"/evaluation/{batch_no}/verify",
                        {"credential": "cred-A-101"})
    print("复核 HTTP", code, "| 所用组别:", verify["group_used"],
          "| 样本数:", len(verify["samples"]))

    step("11. 旧版入口复核：同名实验必须按版本隔离")
    code, ambiguous = call("POST", "/legacy/evaluate",
                           {"display_name": DISPLAY_NAME})
    print("不指定版本 HTTP", code, "|", ambiguous["code"])
    _, la = call("POST", "/legacy/evaluate",
                 {"display_name": DISPLAY_NAME, "version": "v1",
                  "credential": "cred-A-101"})
    _, lb = call("POST", "/legacy/evaluate",
                 {"display_name": DISPLAY_NAME, "version": "v2",
                  "credential": "cred-B-202"})
    print("v1 复核:", [(r["task_code"], r["group_no"]) for r in la["results"]])
    print("v2 复核:", [(r["task_code"], r["group_no"] or "无组别证据") for r in lb["results"]])

    step("12. 切回新版入口，恢复受理")
    _, back = call("POST", "/entry-mode", {"mode": "new", "reason": "故障恢复"})
    print("入口恢复为:", back["entry_mode"])

    step("最终核对")
    _, final = call("GET", "/snapshot")
    print("汇总:", json.dumps(final["summary"], ensure_ascii=False))
    scopes = {}
    for c in final["sample_claims"]:
        scopes.setdefault(c["scope"], set()).add(c["sample_code"])
    for scope, codes in scopes.items():
        print(f"作用域 {scope}: {len(codes)} 个样本，无重复 =",
              len(codes) == len([c for c in final["sample_claims"] if c["scope"] == scope]))


if __name__ == "__main__":
    main()
