"""Thin wrapper around the model provider.

Hugging Face exposes an OpenAI-compatible router, so the official openai SDK is
used and only the base_url changes. Keeping all of that behind one function
means the agents do not care which provider is configured.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from openai import OpenAI

from src.config import (
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_SECONDS,
    MissingApiKeyError,
    get_api_key,
    get_base_url,
    get_model_name,
)


class LlmUnavailableError(RuntimeError):
    """The model could not be reached after retrying."""


@dataclass
class LlmCounter:
    """Counts model calls so the architecture comparison has real numbers."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    durations_ms: list[float] = field(default_factory=list)

    def record(self, duration_ms: float, usage) -> None:
        self.calls += 1
        self.durations_ms.append(duration_ms)
        if usage is not None:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0


_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=get_base_url(),
            api_key=get_api_key(),
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=0,  # retries are handled below so they can be counted
        )
    return _client


def chat(
    messages: list[dict],
    counter: LlmCounter,
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1200,
):
    """One model call, with a couple of retries for flaky network errors."""
    kwargs: dict = {
        "model": get_model_name(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if response_format:
        kwargs["response_format"] = response_format

    last_error: Exception | None = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        started = time.perf_counter()
        try:
            response = get_client().chat.completions.create(**kwargs)
        except MissingApiKeyError:
            # A missing key is a setup mistake, not a flaky network. Retrying it
            # and then quietly falling back to the rules would hide the fact
            # that no model ran at all.
            raise
        except Exception as exc:
            last_error = exc
            if attempt < LLM_MAX_RETRIES:
                time.sleep(1.0 * (attempt + 1))
                continue
            break
        counter.record((time.perf_counter() - started) * 1000, getattr(response, "usage", None))
        return response.choices[0].message

    raise LlmUnavailableError(
        f"The model could not be reached after {LLM_MAX_RETRIES + 1} attempts: {last_error}"
    )


def parse_json(text: str | None) -> dict:
    """Parse model JSON, coping with the odd stray code fence."""
    if not text:
        raise ValueError("The model returned an empty response.")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Some models wrap the object in a sentence. Take the outermost braces.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"The model did not return JSON: {text[:200]}")
        return json.loads(cleaned[start : end + 1])
