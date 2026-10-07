"""Settings for the procurement copilot.

Everything that could change between machines lives here so the rest of the
code does not read os.environ directly.
"""
from __future__ import annotations

import os
from datetime import date

# Reference date from data/procurement_policy.md - never use date.today() here.
SNAPSHOT_DATE = date(2026, 9, 30)

# A vendor security assessment stays current for this long (policy section 5).
SECURITY_REVIEW_VALID_DAYS = 365

DEFAULT_MODEL = "Qwen/Qwen2.5-72B-Instruct"
DEFAULT_BASE_URL = "https://router.huggingface.co/v1"

# Kept small on purpose - the rules do the heavy lifting, not the agent.
MAX_TOOL_STEPS = 8
LLM_TIMEOUT_SECONDS = 60.0
LLM_MAX_RETRIES = 2

VENDOR_API_TIMEOUT_SECONDS = 4.0
VENDOR_API_RETRIES = 2


class MissingApiKeyError(RuntimeError):
    """Raised when no Hugging Face token is configured."""


def get_api_key() -> str:
    """Return the Hugging Face token, or explain exactly how to set it."""
    key = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_KEY") or ""
    key = key.strip()
    if not key:
        raise MissingApiKeyError(
            "No Hugging Face token found.\n"
            "This project needs an API key to run.\n"
            "  1. Copy .env.example to .env\n"
            "  2. Get a free token at https://huggingface.co/settings/tokens\n"
            "  3. Put it in .env as HF_TOKEN=hf_...\n"
        )
    return key


def has_api_key() -> bool:
    try:
        get_api_key()
    except MissingApiKeyError:
        return False
    return True


def get_model_name() -> str:
    return (os.getenv("MODEL_NAME") or "").strip() or DEFAULT_MODEL


def get_base_url() -> str:
    return (os.getenv("LLM_BASE_URL") or "").strip() or DEFAULT_BASE_URL


def get_vendor_api_base_url() -> str:
    return (os.getenv("VENDOR_RISK_BASE_URL") or "http://127.0.0.1:8001").rstrip("/")
