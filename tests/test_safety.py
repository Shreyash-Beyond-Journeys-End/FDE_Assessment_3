"""Tests for the rules the assessment says must never break.

These are the ones worth failing a build over: no autonomous approval, no
hardcoded answers, no dependence on today's date, and no way for the model to
soften a decision the policy already made.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from src import rules
from src.data_access import load_requests
from src.decide import resolve_action
from src.evidence import build_context, ensure_minimum_evidence
from src.rules import ACTIONS, apply_rules
from src.tools import DETERMINISTIC_TOOLS, TOOL_SCHEMAS, Toolbox
from tests import mock_server

SRC = Path(__file__).resolve().parents[1] / "src"


def code_only(path: Path) -> str:
    """Return the file's real code, with comments and docstrings stripped out.

    Without this the checks below trip over their own documentation: config.py
    has a comment saying never to call date.today(), and tools.py names a
    product in a comment explaining the matching rule. Neither is a hardcode.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body.pop(0)
    # ast.unparse drops comments for us.
    return ast.unparse(tree)


def setUpModule() -> None:
    mock_server.start()


def tearDownModule() -> None:
    mock_server.stop()


class HumanAuthorityTests(unittest.TestCase):
    def test_human_review_is_required_on_every_request(self):
        for request in load_requests():
            with self.subTest(request_id=request["request_id"]):
                req, _ = build_context(request["request_id"])
                toolbox = Toolbox()
                ensure_minimum_evidence(req, toolbox)
                outcome = apply_rules(req, toolbox)
                self.assertTrue(outcome.human_review_required)

    def test_nothing_in_the_source_ever_sets_human_review_to_false(self):
        for path in SRC.rglob("*.py"):
            text = code_only(path).replace(" ", "")
            self.assertNotIn("human_review_required=False", text, f"in {path.name}")

    def test_every_request_ends_up_with_at_least_one_named_approver(self):
        for request in load_requests():
            with self.subTest(request_id=request["request_id"]):
                req, _ = build_context(request["request_id"])
                toolbox = Toolbox()
                ensure_minimum_evidence(req, toolbox)
                outcome = apply_rules(req, toolbox)
                self.assertTrue(outcome.sorted_approvals())


class NoHardcodingTests(unittest.TestCase):
    def test_no_request_id_appears_in_the_implementation(self):
        # hidden cases use different ids - branching on one here would be cheating
        pattern = re.compile(r"REQ-\d{3,}")
        for path in SRC.rglob("*.py"):
            found = pattern.findall(code_only(path))
            self.assertEqual(found, [], f"{path.name} refers to specific request ids: {found}")

    def test_no_vendor_or_product_name_is_branched_on(self):
        names = ["BrandBoard", "GrowthForge", "NimbusAI", "SignalWatch", "CodeMate",
                 "NeuralDesk", "TaskFlow", "SignFlow", "PixelCraft"]
        for path in SRC.rglob("*.py"):
            text = code_only(path)
            for name in names:
                self.assertNotIn(name, text, f"{path.name} mentions {name}")


class SnapshotDateTests(unittest.TestCase):
    def test_the_implementation_never_reads_the_computer_clock(self):
        banned = ["date.today()", "datetime.now()", "datetime.today()", "time.localtime()"]
        for path in SRC.rglob("*.py"):
            text = code_only(path)
            for call in banned:
                self.assertNotIn(call, text, f"{path.name} uses {call}")

    def test_expiry_is_measured_from_the_snapshot_date(self):
        toolbox = Toolbox()
        # SignalWatch's review date is 2025-07-01, 456 days before the snapshot
        record = toolbox.run("lookup_vendor_registry", {"vendor_name": "SignalWatch"})
        self.assertEqual(record["security_review_age_days"], 456)
        self.assertEqual(record["security_review_state"], "expired")


class ToolRequirementTests(unittest.TestCase):
    def test_at_least_three_tools_exist(self):
        self.assertGreaterEqual(len(TOOL_SCHEMAS), 3)

    def test_at_least_one_tool_is_deterministic(self):
        self.assertGreaterEqual(len(DETERMINISTIC_TOOLS), 1)

    def test_every_schema_has_a_matching_implementation(self):
        toolbox = Toolbox()
        from src.tools import _IMPLEMENTATIONS

        for schema in TOOL_SCHEMAS:
            name = schema["function"]["name"]
            self.assertIn(name, _IMPLEMENTATIONS)


class ModelCannotWeakenTheDecisionTests(unittest.TestCase):
    def test_a_weaker_action_from_the_model_is_ignored(self):
        outcome = rules.RuleOutcome(minimum_action="route_for_reviews")
        self.assertEqual(
            resolve_action(outcome, {"recommended_action": "proceed_to_approval"}),
            "route_for_reviews",
        )

    def test_a_stricter_action_from_the_model_is_accepted(self):
        outcome = rules.RuleOutcome(minimum_action="route_for_reviews")
        self.assertEqual(
            resolve_action(outcome, {"recommended_action": "hold_do_not_proceed"}),
            "hold_do_not_proceed",
        )

    def test_nonsense_from_the_model_falls_back_to_the_rules(self):
        outcome = rules.RuleOutcome(minimum_action="request_clarification")
        for bad in [{}, {"recommended_action": ""}, {"recommended_action": "approve_now"}]:
            self.assertEqual(resolve_action(outcome, bad), "request_clarification")

    def test_the_action_list_is_ordered_weakest_to_strictest(self):
        self.assertEqual(ACTIONS[0], "proceed_to_approval")
        self.assertEqual(ACTIONS[-1], "hold_do_not_proceed")


if __name__ == "__main__":
    unittest.main()


class ApiKeyTests(unittest.TestCase):
    """A missing key must stop the run, not quietly turn into a rules-only answer."""

    def test_handle_request_fails_fast_without_a_key(self):
        import os
        from unittest import mock

        from src.config import MissingApiKeyError
        from src.solution import handle_request

        cleared = {k: v for k, v in os.environ.items()
                   if k not in {"HF_TOKEN", "HUGGINGFACE_API_KEY"}}
        with mock.patch.dict(os.environ, cleared, clear=True):
            with self.assertRaises(MissingApiKeyError) as caught:
                handle_request("REQ-1001", "single")
        message = str(caught.exception)
        self.assertIn(".env", message)
        self.assertIn("huggingface.co", message)

    def test_the_error_arrives_before_any_model_call_is_attempted(self):
        import os
        from unittest import mock

        from src.config import MissingApiKeyError
        from src.llm import LlmCounter, chat

        counter = LlmCounter()
        cleared = {k: v for k, v in os.environ.items()
                   if k not in {"HF_TOKEN", "HUGGINGFACE_API_KEY"}}
        with mock.patch.dict(os.environ, cleared, clear=True):
            # Reset the cached client so it has to read the key again.
            import src.llm

            src.llm._client = None
            with self.assertRaises(MissingApiKeyError):
                chat([{"role": "user", "content": "hi"}], counter)
        self.assertEqual(counter.calls, 0)


class DegradedRunTests(unittest.TestCase):
    """If the model step fails, the output has to say so."""

    def test_a_model_failure_is_visible_as_a_risk_flag(self):
        from src.decide import build_decision, fallback_judgement
        from src.llm import LlmCounter
        from src.rules import RuleOutcome

        outcome = RuleOutcome(
            minimum_action="route_for_reviews", required_approvals=["Procurement"]
        )
        decision = build_decision(
            {"request_id": "TEST-1"},
            outcome,
            fallback_judgement(outcome, "network down"),
            Toolbox(),
            LlmCounter(),
        )
        self.assertIn("llm_unavailable", decision.risk_flags)
        self.assertTrue(decision.human_review_required)

    def test_a_model_failure_never_weakens_the_action(self):
        from src.decide import fallback_judgement, resolve_action
        from src.rules import RuleOutcome

        outcome = RuleOutcome(minimum_action="request_clarification")
        judgement = fallback_judgement(outcome, "timeout")
        self.assertEqual(resolve_action(outcome, judgement), "request_clarification")
