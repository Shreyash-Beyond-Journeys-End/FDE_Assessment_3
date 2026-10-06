from __future__ import annotations

from src.config import get_api_key
from src.contracts import Architecture, ProcurementDecision


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    """Assessment adapter.

    Both architectures share the same tools and the same deterministic policy
    engine. Only the orchestration differs:

      single - one agent gathers evidence and writes the judgement itself.
      staged - an analyst agent gathers evidence into a structured pack, and a
               separate reviewer agent decides from that pack alone.
    """
    # Fail here, before any work, if the project is not configured. Without this
    # the run would degrade to a rules-only answer and look like a pass.
    get_api_key()

    if architecture == "staged":
        from src.agents import staged

        return staged.run(request_id)

    if architecture == "single":
        from src.agents import single

        return single.run(request_id)

    raise ValueError(f"Unknown architecture: {architecture!r}. Use 'single' or 'staged'.")
