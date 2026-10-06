"""Runs the same case set through both architectures and scores them.

The public runner checks the minimum expectations. This one asks the harder
questions from the brief: is the next action right, is every piece of evidence
really from a tool, were the policy rules followed, and did the request reach
the right humans. It also records latency and the LLM/tool call counts.

Usage:
    python evals/run_comparison.py                 # both architectures, 1 run
    python evals/run_comparison.py --runs 3        # 3 runs each, for variance
    python evals/run_comparison.py --architecture single
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals import preflight
from src.contracts import ProcurementDecision
from src.rules import ACTION_LABELS, action_rank
from src.solution import handle_request

RESULTS_DIR = ROOT / "evals" / "results"

# Evidence may also be written by the orchestrator itself, for example when it
# notes that it had to run a check the agent skipped. Everything else has to
# name a tool that really ran.
NON_TOOL_SOURCES = {"orchestrator"}


def resolved_action(decision: ProcurementDecision) -> str:
    """Read the action back out of the recommendation line."""
    text = decision.recommendation or ""
    best, best_len = "", -1
    for action, label in ACTION_LABELS.items():
        if text.startswith(label) and len(label) > best_len:
            best, best_len = action, len(label)
    return best or "unknown"


def score(decision: ProcurementDecision, case: dict) -> dict:
    flags = {f.strip().lower() for f in decision.risk_flags}
    approvals = {a.strip().lower() for a in decision.required_approvals}
    expected_approvals = {a.strip().lower() for a in case["expected_approvals"]}
    required_flags = {f.lower() for f in case["risk_flags_required"]}
    forbidden_flags = {f.lower() for f in case["risk_flags_forbidden"]}

    action = resolved_action(decision)
    expected_action = case["expected_action"]

    tool_names = set(decision.telemetry.tool_names if decision.telemetry else [])
    evidence_sources = {item.source for item in decision.evidence}
    ungrounded = evidence_sources - tool_names - NON_TOOL_SOURCES

    missing_required = sorted(required_flags - flags)
    forbidden_present = sorted(forbidden_flags & flags)
    approvals_missing = sorted(expected_approvals - approvals)
    approvals_extra = sorted(approvals - expected_approvals)

    has_missing_info = bool(decision.missing_information)

    problems: list[str] = []
    if action != expected_action:
        problems.append(f"action {action} != expected {expected_action}")
    if missing_required:
        problems.append("missing risk flags: " + ", ".join(missing_required))
    if forbidden_present:
        problems.append("wrong risk flags: " + ", ".join(forbidden_present))
    if approvals_missing:
        problems.append("missing approvals: " + ", ".join(approvals_missing))
    if approvals_extra:
        problems.append("unexpected approvals: " + ", ".join(approvals_extra))
    if ungrounded:
        problems.append("evidence not traceable to a tool: " + ", ".join(sorted(ungrounded)))
    if has_missing_info != case["expect_missing_information"]:
        problems.append(
            f"missing_information present={has_missing_info}, expected={case['expect_missing_information']}"
        )
    if not decision.human_review_required:
        problems.append("human_review_required is False")

    return {
        "correct_next_action": action == expected_action,
        # The dangerous direction: a weaker action than the policy demands.
        "under_escalated": action_rank(action) < action_rank(expected_action),
        "grounded_evidence": not ungrounded,
        "policy_followed": not (
            missing_required or forbidden_present or approvals_missing or approvals_extra
        ),
        "human_escalation_correct": (
            decision.human_review_required and has_missing_info == case["expect_missing_information"]
        ),
        "resolved_action": action,
        "evidence_items": len(decision.evidence),
        "problems": " | ".join(problems),
    }


DIMENSIONS = [
    "correct_next_action",
    "grounded_evidence",
    "policy_followed",
    "human_escalation_correct",
]


def run_architecture(architecture: str, cases: list[dict], runs: int) -> list[dict]:
    rows: list[dict] = []
    print(f"\n--- {architecture} ---")
    for run_index in range(1, runs + 1):
        for case in cases:
            started = time.perf_counter()
            try:
                raw = handle_request(case["request_id"], architecture=architecture)
                decision = (
                    raw if isinstance(raw, ProcurementDecision)
                    else ProcurementDecision.model_validate(raw)
                )
                latency_ms = (time.perf_counter() - started) * 1000
                result = score(decision, case)
                telemetry = decision.telemetry
                row = {
                    "run": run_index,
                    "case_id": case["case_id"],
                    "request_id": case["request_id"],
                    "edge_case": case["edge_case"],
                    "architecture": architecture,
                    **{k: result[k] for k in DIMENSIONS},
                    "under_escalated": result["under_escalated"],
                    "resolved_action": result["resolved_action"],
                    "evidence_items": result["evidence_items"],
                    "latency_ms": round(latency_ms, 1),
                    "llm_calls": telemetry.llm_calls if telemetry else "",
                    "tool_calls": telemetry.tool_calls if telemetry else "",
                    "problems": result["problems"],
                }
                passed_all = all(result[k] for k in DIMENSIONS)
                mark = "PASS" if passed_all else "FAIL"
                print(
                    f"{mark}  run{run_index}  {case['case_id']}  {case['title'][:42]:<42} "
                    f"({latency_ms:6.0f} ms, {row['llm_calls']} llm, {row['tool_calls']} tools)"
                )
                if result["problems"]:
                    print(f"        {result['problems']}")
            except Exception as exc:
                latency_ms = (time.perf_counter() - started) * 1000
                print(f"ERROR run{run_index} {case['case_id']}  {type(exc).__name__}: {exc}")
                row = {
                    "run": run_index,
                    "case_id": case["case_id"],
                    "request_id": case["request_id"],
                    "edge_case": case["edge_case"],
                    "architecture": architecture,
                    **{k: False for k in DIMENSIONS},
                    "under_escalated": True,
                    "resolved_action": "error",
                    "evidence_items": 0,
                    "latency_ms": round(latency_ms, 1),
                    "llm_calls": "",
                    "tool_calls": "",
                    "problems": f"ERROR: {type(exc).__name__}: {exc}",
                }
            rows.append(row)
    return rows


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def summarise(rows: list[dict], architecture: str) -> dict:
    mine = [r for r in rows if r["architecture"] == architecture]
    total = len(mine)
    numbers = lambda key: [float(r[key]) for r in mine if r[key] != ""]
    return {
        "architecture": architecture,
        "runs": total,
        "all_dimensions_passed": sum(1 for r in mine if all(r[k] for k in DIMENSIONS)),
        **{k: sum(1 for r in mine if r[k]) for k in DIMENSIONS},
        "under_escalated": sum(1 for r in mine if r["under_escalated"]),
        "avg_latency_ms": round(_mean(numbers("latency_ms")), 0),
        "p95_latency_ms": round(
            sorted(numbers("latency_ms"))[max(0, int(len(mine) * 0.95) - 1)] if mine else 0, 0
        ),
        "avg_llm_calls": round(_mean(numbers("llm_calls")), 2),
        "avg_tool_calls": round(_mean(numbers("tool_calls")), 2),
        "avg_evidence_items": round(_mean(numbers("evidence_items")), 2),
    }


def write_summary(summaries: list[dict], rows: list[dict], runs: int) -> Path:
    lines = [
        "# Architecture comparison results",
        "",
        f"Same {len(set(r['case_id'] for r in rows))} cases, {runs} run(s) each, "
        "same tools and same deterministic rules in both. Generated by "
        "`python evals/run_comparison.py`.",
        "",
        "| Metric | " + " | ".join(s["architecture"] for s in summaries) + " |",
        "|---|" + "---:|" * len(summaries),
    ]
    metrics = [
        ("Cases passing every quality check", "all_dimensions_passed"),
        ("Correct next action", "correct_next_action"),
        ("Evidence grounded in tool results", "grounded_evidence"),
        ("Policy and approvals correct", "policy_followed"),
        ("Human escalation correct", "human_escalation_correct"),
        ("Under-escalated (lower is better)", "under_escalated"),
        ("Avg latency (ms)", "avg_latency_ms"),
        ("p95 latency (ms)", "p95_latency_ms"),
        ("Avg LLM calls", "avg_llm_calls"),
        ("Avg tool calls", "avg_tool_calls"),
        ("Avg evidence items", "avg_evidence_items"),
    ]
    for label, key in metrics:
        counts = []
        for s in summaries:
            value = s[key]
            if key in {
                "all_dimensions_passed", "correct_next_action", "grounded_evidence",
                "policy_followed", "human_escalation_correct",
            }:
                counts.append(f"{value}/{s['runs']}")
            else:
                counts.append(f"{value:g}")
        lines.append(f"| {label} | " + " | ".join(counts) + " |")

    failures = [r for r in rows if r["problems"]]
    lines += ["", "## Problems found", ""]
    if not failures:
        lines.append("No case failed a quality check in this run.")
    else:
        for row in failures:
            lines.append(
                f"- **{row['architecture']} / {row['case_id']} (run {row['run']})**: {row['problems']}"
            )

    lines += ["", "## Coverage by edge case", ""]
    seen: dict[str, list[str]] = {}
    for row in rows:
        seen.setdefault(row["edge_case"], []).append(row["case_id"])
    for edge_case in sorted(seen):
        lines.append(f"- {edge_case}: {', '.join(sorted(set(seen[edge_case])))}")

    out = RESULTS_DIR / "SUMMARY.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--architecture", choices=["single", "staged", "both"], default="both")
    parser.add_argument("--runs", type=int, default=1, help="Repeat each case this many times")
    args = parser.parse_args()

    preflight.check()

    cases = json.loads((ROOT / "evals" / "quality_cases.json").read_text(encoding="utf-8"))
    architectures = ["single", "staged"] if args.architecture == "both" else [args.architecture]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    for architecture in architectures:
        rows = run_architecture(architecture, cases, args.runs)
        all_rows += rows
        out = RESULTS_DIR / f"comparison_{architecture}.csv"
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {out.relative_to(ROOT)}")

    summaries = [summarise(all_rows, a) for a in architectures]
    print("\n" + "=" * 70)
    for s in summaries:
        print(
            f"{s['architecture']:7} passed {s['all_dimensions_passed']}/{s['runs']}  "
            f"avg {s['avg_latency_ms']:.0f} ms  {s['avg_llm_calls']} llm  {s['avg_tool_calls']} tools"
        )
    summary_path = write_summary(summaries, all_rows, args.runs)
    print(f"Wrote {summary_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
