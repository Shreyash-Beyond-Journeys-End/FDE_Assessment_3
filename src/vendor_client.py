"""Client for the mock vendor-risk API.

The starter version called requests.get and let any error escape. Policy section
10 says a tool failure must be recorded, not guessed around, so this version
retries briefly and then returns a clear "unavailable" result instead of raising.
"""
from __future__ import annotations

import time
from urllib.parse import quote

import requests

from src.config import (
    VENDOR_API_RETRIES,
    VENDOR_API_TIMEOUT_SECONDS,
    get_vendor_api_base_url,
)


def get_vendor_risk(vendor_name: str, timeout_seconds: float = VENDOR_API_TIMEOUT_SECONDS) -> dict:
    """Low-level API client. Raises on failure; callers usually want fetch_vendor_risk."""
    url = f"{get_vendor_api_base_url()}/vendor-risk/{quote(vendor_name, safe='')}"
    response = requests.get(url, timeout=timeout_seconds)
    response.raise_for_status()
    return response.json()


def fetch_vendor_risk(vendor_name: str) -> dict:
    """Safe wrapper used by the agent tool.

    Always returns a dict with an "available" key so the caller never has to
    guess whether the data is real.
    """
    last_error = "unknown error"
    # a 503 is a deliberate outage in the mock service, so that one isn't retried below
    for attempt in range(VENDOR_API_RETRIES + 1):
        try:
            data = get_vendor_risk(vendor_name)
            return {"available": True, **data}
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status == 404:
                return {
                    "available": False,
                    "vendor_name": vendor_name,
                    "reason": "not_found",
                    "detail": f"The vendor-risk service has no record for '{vendor_name}'.",
                }
            if status == 503:
                detail = "Vendor-risk service is temporarily unavailable."
                try:
                    detail = exc.response.json().get("detail", detail)
                except Exception:
                    pass
                return {
                    "available": False,
                    "vendor_name": vendor_name,
                    "reason": "service_unavailable",
                    "detail": detail,
                }
            last_error = f"HTTP {status}"
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < VENDOR_API_RETRIES:
            time.sleep(0.3 * (attempt + 1))

    return {
        "available": False,
        "vendor_name": vendor_name,
        "reason": "unreachable",
        "detail": (
            f"Could not reach the vendor-risk service ({last_error}). "
            "Is it running? Start it with: python run_local.py"
        ),
    }
