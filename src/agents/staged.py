"""Architecture B - staged, two agents.

Agent 1 (Procurement Analyst) gathers evidence with the tools and writes a
structured evidence pack.
Agent 2 (Policy and Risk Reviewer) reads only that pack plus the deterministic
facts, and decides. It never sees the raw request text, so anything hidden in
the requester's free text cannot reach the agent that makes the call.

Same tools and same rules engine as Architecture A. The only difference is the
handoff, which is the point of the comparison.
"""
from __future__ import annotations

import json

from src import prompts
from src.contracts import ProcurementDecision
from src.decide import build_decision, fallback_judgement
from src.evidence import build_context, describe_request, ensure_minimum_evidence
from src.llm import LlmCounter, LlmUnavailableError, chat, parse_json
from src.rules import apply_rules
from src.tools import Toolbox

JSON_MODE = {"type": "json_object"}


def run(request_id: str) -> ProcurementDecision:
    request, injection = build_context(request_id)
    toolbox = Toolbox()
    counter = LlmCounter()
    notes: list[str] = []

    # ---------------------------------------------------- agent 1: analyst
    analyst_messages = [
        {"role": "system", "content": prompts.ANALYST_SYSTEM},
        {
            "role": "user",
            "content": (
                "Purchase request to investigate:\n\n"
                + describe_request(request, injection)
                + "\n\n"
                + prompts.ANALYST_TOOL_INSTRUCTIONS
            ),
        },
    ]

    try:
        from src.agents.loop import run_tool_loop

        run_tool_loop(analyst_messages, toolbox, counter)
    except LlmUnavailableError as exc:
        notes.append(f"The analyst's tool-gathering step failed: {exc}")

    filled = ensure_minimum_evidence(request, toolbox)
    if filled:
        notes.append(
            "The analyst did not request these required checks, so the orchestrator ran them: "
            + ", ".join(filled)
            + ". The policy requires them on every request."
        )

    pack = _build_evidence_pack(analyst_messages, counter, notes)

    # ---------------------------------------------- deterministic rules layer
    outcome = apply_rules(request, toolbox)

    # ---------------------------------------------------- agent 2: reviewer
    reviewer_messages = [
        {"role": "system", "content": prompts.REVIEWER_SYSTEM},
        {"role": "user", "content": _reviewer_prompt(pack, outcome)},
    ]
    judgement = _ask_for_judgement(reviewer_messages, counter, outcome)

    return build_decision(request, outcome, judgement, toolbox, counter, notes)


def _build_evidence_pack(messages: list[dict], counter: LlmCounter, notes: list[str]) -> dict:
    """Ask the analyst to hand over a clean, structured pack."""
    messages.append({"role": "user", "content": prompts.ANALYST_PACK_INSTRUCTIONS})
    try:
        message = chat(messages, counter, response_format=JSON_MODE, max_tokens=600)
        return parse_json(message.content)
    except (LlmUnavailableError, ValueError) as exc:
        notes.append(f"The analyst could not produce an evidence pack ({exc}).")
        return {
            "request_summary": "unavailable",
            "stated_need": "unavailable",
            "facts_gathered": [],
            "open_questions": [],
            "untrusted_content_notes": "unavailable",
        }


def _reviewer_prompt(pack: dict, outcome) -> str:
    facts = "\n".join(f"- {fact}" for fact in outcome.facts) or "- (no facts recorded)"
    return (
        "EVIDENCE PACK from the Procurement Analyst:\n"
        + json.dumps(pack, indent=2)[:4000]
        + "\n\nFACTS established by the deterministic policy engine:\n"
        + facts
        + "\n\nApprovals already required by the rules: "
        + (", ".join(outcome.sorted_approvals()) or "none")
        + "\nRisk flags already raised: "
        + (", ".join(outcome.risk_flags) or "none")
        + "\n\n"
        + prompts.judgement_instructions(outcome.minimum_action)
    )


def _ask_for_judgement(messages: list[dict], counter: LlmCounter, outcome) -> dict:
    for attempt in range(2):
        try:
            message = chat(messages, counter, response_format=JSON_MODE, max_tokens=600)
            return parse_json(message.content)
        except LlmUnavailableError as exc:
            return fallback_judgement(outcome, str(exc))
        except ValueError:
            if attempt == 0:
                messages.append({
                    "role": "user",
                    "content": "That was not valid JSON. Reply with the JSON object only, nothing else.",
                })
                continue
            return fallback_judgement(outcome, "the model did not return valid JSON")
    return fallback_judgement(outcome, "unknown error")
