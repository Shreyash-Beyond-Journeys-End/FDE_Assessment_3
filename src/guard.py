"""Prompt-injection guard.

Policy section 9 says request text, vendor notes and API text are business data,
not instructions. This module is the code-side defence for that rule.

It is deliberately a plain regex scanner and not an LLM call, for two reasons:
the check must give the same answer every run, and asking a model whether it is
being manipulated is exactly the thing an injection attacks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Each pattern is written to match an *instruction*, not just a keyword.
# "Expand the approved coding assistant" must not trip the approval pattern,
# so the patterns look for an imperative aimed at the copilot.
_PATTERNS: list[tuple[str, str]] = [
    (
        r"\b(ignore|disregard|forget)\b[^.]{0,40}\b(rule|rules|policy|policies|instruction|instructions|guideline|guidelines|check|checks|step|steps)\b",
        "text tells the assistant to ignore rules or instructions",
    ),
    (
        r"\b(treat|mark|consider|record|log)\b[^.]{0,40}\b(pre-?approved|cfo-?approved|already approved|as approved|as authoris?ed)\b",
        "text tells the assistant to treat the request as already approved",
    ),
    (
        r"\b(approve|authoris?e|sign off on)\b[^.]{0,30}\b(immediately|right away|at once|now|automatically|without review|without approval)\b",
        "text tells the assistant to approve without human review",
    ),
    (
        r"\b(auto|automatically)\s*-?\s*approv",
        "text asks for automatic approval",
    ),
    (
        r"\b(skip|bypass|override|overrule|avoid|waive)\b[^.]{0,40}\b(policy|policies|review|reviews|approval|approvals|security|privacy|legal|control|controls|check|checks|rule|rules)\b",
        "text asks to skip or override a control",
    ),
    (
        r"\b(no|not)\b[^.]{0,20}\b(approval|review|sign-?off)\b[^.]{0,20}\b(required|needed|necessary)\b",
        "text claims no approval or review is needed",
    ),
    (
        r"\b(you are now|act as|pretend to be|from now on you)\b",
        "text tries to change the assistant's role",
    ),
    (
        r"\b(system prompt|developer message|new instructions?|updated instructions?)\b",
        "text refers to the assistant's own instructions",
    ),
    (
        r"\b(reveal|show|print|output|repeat|disclose)\b[^.]{0,40}\b(api key|secret|secrets|token|credential|credentials|password|prompt)\b",
        "text tries to extract secrets or the prompt",
    ),
]

_COMPILED = [(re.compile(pattern, re.IGNORECASE), reason) for pattern, reason in _PATTERNS]


@dataclass
class InjectionReport:
    """What the scanner found across every untrusted field it was given."""

    detected: bool = False
    # field name -> list of human-readable reasons
    findings: dict[str, list[str]] = field(default_factory=dict)

    def reasons(self) -> list[str]:
        out: list[str] = []
        for source, reasons in self.findings.items():
            for reason in reasons:
                out.append(f"{source}: {reason}")
        return out

    def summary(self) -> str:
        if not self.detected:
            return "No injection patterns found in the untrusted text."
        fields = ", ".join(sorted(self.findings))
        return (
            f"Untrusted text in {fields} tried to change the assistant's behaviour. "
            "The embedded instruction was ignored and the real policy was applied."
        )


def scan_text(text: str | None) -> list[str]:
    """Return the reasons this text looks like an injection attempt."""
    if not text:
        return []
    reasons: list[str] = []
    for pattern, reason in _COMPILED:
        if pattern.search(text):
            reasons.append(reason)
    return reasons


def scan_fields(fields: dict[str, str | None]) -> InjectionReport:
    """Scan several named untrusted fields at once."""
    report = InjectionReport()
    for name, value in fields.items():
        reasons = scan_text(value)
        if reasons:
            report.detected = True
            report.findings[name] = reasons
    return report


def scan_request(request: dict) -> InjectionReport:
    """Scan the untrusted parts of a purchase request."""
    return scan_fields(
        {
            "business_justification": request.get("business_justification"),
            "product_name": request.get("product_name"),
            "vendor_name": request.get("vendor_name"),
        }
    )


def fence(label: str, text: str | None) -> str:
    """Wrap untrusted text so the model can see where it starts and stops.

    The model is told in its system prompt that anything inside these markers is
    data to read, never an instruction to follow.
    """
    body = (text or "").strip() or "(empty)"
    return f"<untrusted_{label}>\n{body}\n</untrusted_{label}>"
