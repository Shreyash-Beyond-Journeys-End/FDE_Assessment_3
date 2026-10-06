"""Architecture A - single-agent baseline.

One agent holds the whole job. It calls tools in a loop, then writes the
judgement from the same conversation, so it still has the raw request text and
every tool reply in front of it when it decides.
"""
from __future__ import annotations

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

    messages = [
        {"role": "system", "content": prompts.SINGLE_SYSTEM},
        {
            "role": "user",
            "content": (
                "Purchase request to review:\n\n"
                + describe_request(request, injection)
                + "\n\n"
                + prompts.ANALYST_TOOL_INSTRUCTIONS
            ),
        },
    ]

    try:
        from src.agents.loop import run_tool_loop

        run_tool_loop(messages, toolbox, counter)
    except LlmUnavailableError as exc:
        notes.append(f"The agent's tool-gathering step failed: {exc}")

    # Whatever the agent did or did not call, the mandatory checks still run.
    filled = ensure_minimum_evidence(request, toolbox)
    if filled:
        notes.append(
            "The agent did not request these required checks, so the orchestrator ran them: "
            + ", ".join(filled)
            + ". The policy requires them on every request."
        )

    outcome = apply_rules(request, toolbox)

    messages.append({"role": "user", "content": _judgement_prompt(outcome)})
    judgement = _ask_for_judgement(messages, counter, outcome)

    return build_decision(request, outcome, judgement, toolbox, counter, notes)


def _judgement_prompt(outcome) -> str:
    facts = "\n".join(f"- {fact}" for fact in outcome.facts) or "- (no facts recorded)"
    return (
        "FACTS established by the deterministic policy engine:\n"
        + facts
        + "\n\nApprovals already required by the rules: "
        + (", ".join(outcome.sorted_approvals()) or "none")
        + "\nRisk flags already raised: "
        + (", ".join(outcome.risk_flags) or "none")
        + "\n\n"
        + prompts.judgement_instructions(outcome.minimum_action)
    )


def _ask_for_judgement(messages: list[dict], counter: LlmCounter, outcome) -> dict:
    """One attempt, then one repair attempt, then fall back to the rules alone."""
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
