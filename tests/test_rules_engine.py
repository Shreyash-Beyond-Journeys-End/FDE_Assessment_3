"""Tests for the deterministic policy engine.

None of these need an API key. The whole point of putting the policy in code is
that it can be tested without a model.
"""
from __future__ import annotations

import unittest

from src.data_access import load_requests
from src.evidence import build_context, ensure_minimum_evidence
from src.rules import apply_rules, approvals_for_amount
from src.tools import Toolbox
from tests import mock_server


def setUpModule() -> None:
    mock_server.start()


def tearDownModule() -> None:
    mock_server.stop()


def decide(request_id: str):
    request, _ = build_context(request_id)
    toolbox = Toolbox()
    ensure_minimum_evidence(request, toolbox)
    return apply_rules(request, toolbox), toolbox


class ThresholdTests(unittest.TestCase):
    def test_threshold_table_matches_policy_section_4(self):
        self.assertEqual(approvals_for_amount(0), ["Manager"])
        self.assertEqual(approvals_for_amount(1000), ["Manager"])
        self.assertEqual(approvals_for_amount(1000.01), ["Department Head", "Procurement"])
        self.assertEqual(approvals_for_amount(10000), ["Department Head", "Procurement"])
        self.assertEqual(
            approvals_for_amount(10000.01), ["Department Head", "Finance", "Procurement"]
        )
        self.assertEqual(approvals_for_amount(25000), ["Department Head", "Finance", "Procurement"])
        self.assertEqual(
            approvals_for_amount(25000.01),
            ["Department Head", "Finance", "CFO", "Procurement"],
        )


class PolicyBehaviourTests(unittest.TestCase):
    def test_low_value_approved_vendor_needs_only_a_manager(self):
        outcome, _ = decide("REQ-1001")
        self.assertEqual(outcome.sorted_approvals(), ["Manager"])
        self.assertNotIn("budget_insufficient", outcome.risk_flags)
        self.assertNotIn("security_review_required", outcome.risk_flags)
        self.assertEqual(outcome.missing_information, [])

    def test_source_code_access_needs_security_even_for_an_approved_vendor(self):
        outcome, _ = decide("REQ-1003")
        self.assertIn("security_review_required", outcome.risk_flags)
        self.assertIn("Security", outcome.sorted_approvals())

    def test_limited_use_ai_tool_does_not_cover_customer_pii(self):
        outcome, _ = decide("REQ-1004")
        self.assertIn("security_review_required", outcome.risk_flags)
        self.assertIn("privacy_review_required", outcome.risk_flags)

    def test_cost_over_available_budget_is_flagged(self):
        outcome, _ = decide("REQ-1005")
        self.assertIn("budget_insufficient", outcome.risk_flags)
        self.assertIn("Finance", outcome.sorted_approvals())

    def test_incomplete_request_lists_what_is_missing(self):
        outcome, _ = decide("REQ-1006")
        joined = " ".join(outcome.missing_information).lower()
        self.assertIn("cost", joined)
        self.assertIn("user", joined)
        self.assertIn("data access", joined)
        self.assertIn("missing_information", outcome.risk_flags)
        self.assertEqual(outcome.minimum_action, "request_clarification")

    def test_no_approval_tier_is_invented_without_a_cost(self):
        outcome, _ = decide("REQ-1006")
        # The $10k+ tiers must not appear when the amount is unknown.
        self.assertNotIn("CFO", outcome.sorted_approvals())
        self.assertNotIn("Finance", outcome.sorted_approvals())

    def test_prompt_injection_in_the_request_is_caught(self):
        outcome, _ = decide("REQ-1006")
        self.assertIn("prompt_injection_detected", outcome.risk_flags)

    def test_expired_review_and_source_conflict_are_both_surfaced(self):
        outcome, _ = decide("REQ-1007")
        self.assertIn("vendor_review_expired", outcome.risk_flags)
        self.assertIn("conflicting_vendor_evidence", outcome.risk_flags)

    def test_duplicate_of_a_company_wide_tool_is_surfaced(self):
        outcome, _ = decide("REQ-1008")
        self.assertIn("existing_tool_overlap", outcome.risk_flags)
        self.assertEqual(outcome.minimum_action, "review_existing_tool_first")

    def test_unavailable_risk_service_is_never_read_as_good_news(self):
        outcome, _ = decide("REQ-1009")
        self.assertIn("vendor_risk_unavailable", outcome.risk_flags)
        self.assertIn("security_review_required", outcome.risk_flags)
        text = " ".join(item.finding.lower() for item in outcome.evidence)
        self.assertIn("unverified", text)


class EvidenceTests(unittest.TestCase):
    def test_every_evidence_item_names_a_tool_that_actually_ran(self):
        for request in load_requests():
            with self.subTest(request_id=request["request_id"]):
                outcome, toolbox = decide(request["request_id"])
                ran = set(toolbox.tool_names)
                for item in outcome.evidence:
                    self.assertIn(item.source, ran)

    def test_every_request_produces_at_least_two_pieces_of_evidence(self):
        for request in load_requests():
            with self.subTest(request_id=request["request_id"]):
                outcome, _ = decide(request["request_id"])
                self.assertGreaterEqual(len(outcome.evidence), 2)


if __name__ == "__main__":
    unittest.main()


class DegradedEvidenceTests(unittest.TestCase):
    """What happens when a tool result is simply not there."""

    def test_a_missing_vendor_risk_result_is_treated_as_unverified(self):
        request, _ = build_context("REQ-1001")
        toolbox = Toolbox()
        ensure_minimum_evidence(request, toolbox)
        # Drop the result the way a crashed tool would.
        toolbox.latest.pop("vendor_risk_tool")
        outcome = apply_rules(request, toolbox)
        self.assertIn("vendor_risk_unavailable", outcome.risk_flags)
        self.assertIn("security_review_required", outcome.risk_flags)

    def test_a_missing_budget_result_is_not_read_as_affordable(self):
        request, _ = build_context("REQ-1001")
        toolbox = Toolbox()
        ensure_minimum_evidence(request, toolbox)
        toolbox.latest.pop("check_budget")
        outcome = apply_rules(request, toolbox)
        self.assertIn("budget_unverified", outcome.risk_flags)
        self.assertNotIn("budget_insufficient", outcome.risk_flags)
