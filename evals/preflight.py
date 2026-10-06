"""Shared checks the evaluation runners do before they start.

Without these, a forgotten `python run_local.py` shows up as a wrong answer
rather than a setup problem: every vendor lookup would fail and every case would
pick up a vendor_risk_unavailable flag.
"""
from __future__ import annotations

import sys

import requests

from src.config import get_vendor_api_base_url, has_api_key


def check(stop_on_failure: bool = True) -> bool:
    problems: list[str] = []

    if not has_api_key():
        problems.append(
            "No Hugging Face token found. Copy .env.example to .env and set HF_TOKEN.\n"
            "    Get a free token at https://huggingface.co/settings/tokens"
        )

    base_url = get_vendor_api_base_url()
    try:
        response = requests.get(f"{base_url}/health", timeout=2)
        if not response.ok:
            problems.append(f"The vendor-risk API at {base_url} answered {response.status_code}.")
    except requests.RequestException:
        problems.append(
            f"The vendor-risk API is not reachable at {base_url}.\n"
            "    Start it in another terminal with: python run_local.py\n"
            "    Without it every vendor check fails and the results are meaningless."
        )

    if not problems:
        return True

    print("\nCannot run the evaluation yet:\n")
    for problem in problems:
        print(f"  - {problem}")
    print()
    if stop_on_failure:
        sys.exit(1)
    return False
