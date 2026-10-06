# Student submission checklist

Before submitting, confirm that:

- [x] `python verify_setup.py` passes in your project environment.
- [x] The product can process a request end-to-end.
- [x] Architecture A is a working single-agent baseline. (`src/agents/single.py`)
- [x] Architecture B is a lightweight staged / 2-agent variant. (`src/agents/staged.py`)
- [x] At least 3 tools are used; at least 1 tool/check is deterministic. (8 tools, 7 deterministic)
- [x] Recommendations are returned in the `ProcurementDecision` structure.
- [x] Important evidence is visible to the user. (evidence panel in the UI, every item names its tool)
- [x] Missing/conflicting/unavailable evidence is handled without fabrication.
- [x] Human approval is preserved for sensitive decisions. (`human_review_required` is always true, and a test enforces it)
- [x] Prompt injection inside business data does not override system behaviour. (`src/guard.py`, REQ-1006)
- [x] Date-based checks use the policy's data snapshot / reference date. (2026-09-30; a test fails if `src/` ever calls `date.today()`)
- [x] The same evaluation cases were run on both architectures. (`evals/run_comparison.py`)
- [x] Latency and LLM/tool-call counts are reported. (in the output telemetry and in `evals/results/`)
- [x] The decision memo is <= 500 words and supported by evaluation evidence. (`docs/DECISION_MEMO.md`)
- [x] Setup instructions work from a clean environment. (README, verified in a fresh venv)
- [x] Any LLM/provider SDK you added is present in `requirements.txt`. (`openai`)
- [x] `.env`, API keys, and other secrets are not committed. (`.env` is gitignored; `.env.example` has placeholders only)
