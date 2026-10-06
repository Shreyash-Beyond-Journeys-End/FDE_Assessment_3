# Architecture and workflow

## 1. The workflow

```
  Employee request
         |
         v
  [1] Understand the request
      - load the request record
      - scan the free text for instructions aimed at the assistant
         |
         v
  [2] Gather evidence  (the agent calls tools)
         |
         +--> check_budget .............. department budget vs annual cost
         +--> search_software_catalog ... is this already owned?
         +--> lookup_vendor_registry .... internal vendor record + review age
         +--> vendor_risk_tool .......... external risk service (HTTP)
         +--> lookup_requester .......... who is asking, which department
         +--> get_purchase_history ...... what was bought before
         +--> lookup_policy ............. read the real policy section
         |
         v
  [2b] Minimum evidence check   <-- code, not the model
      Six checks are required for every request. If the agent skipped one,
      the orchestrator runs it anyway and says so in the evidence.
         |
         v
  [3] Deterministic policy engine  (src/rules.py)
      budget | thresholds | overlap | security | privacy | legal
      expiry | source conflict | missing fields | injection
         |
         v
  [4] Model judgement
      Is the overlap a duplicate or a real gap? What should a human do next?
      The model may make the action stricter. It can never weaken it.
         |
         v
  [5] ProcurementDecision  ->  UI  ->  Human review queue
      recommendation | evidence | approvals | missing info | risk flags | next step
```

## 2. Who decides what

The brief splits the problem three ways, and so does the code.

| Layer | What it owns | Where it lives |
|---|---|---|
| **CODE** | Budget maths, the approval threshold table, the 365-day expiry, required-field checks, registry vs API conflict, injection scanning | `src/rules.py`, `src/guard.py` |
| **AI** | Reading the justification, judging whether an overlap is a duplicate or a real gap, writing the recommendation and the next step, raising extra risks | `src/agents/`, `src/prompts.py` |
| **HUMAN** | Every approval, every exception, the final call | `human_review_required` is always true; the UI only hands over a package |

The single most important rule in the whole project:

> The deterministic engine sets a floor. The model can raise the action and add
> a risk flag, but it can never remove an approval, remove a flag, or recommend
> something softer than the policy requires.

This is enforced in `src/decide.py` and tested in `tests/test_safety.py`.

## 3. Architecture A - single agent (the baseline)

```
   UI
    |
    v
  Procurement Agent  ---- tool-calling loop ----+
    |   (one model, all 8 tools)                |
    |                                           v
    |                            budget | catalog | vendor registry
    |                            risk API | policy | history | scan
    |                                           |
    |<------------------------------------------+
    v
  Deterministic rules engine  (authoritative)
    |
    v
  Same agent writes the judgement, still holding the full transcript
    |
    v
  ProcurementDecision
```

Typical run: **3 LLM calls**. One to choose tools, one short confirmation that it
is done, one to write the judgement.

## 4. Architecture B - staged, two agents

```
   UI
    |
    v
  Agent 1: Procurement Analyst ---- tool-calling loop ----+
    |   gathers facts only, decides nothing               |
    |                                                     v
    |                                      the same 8 tools
    |<----------------------------------------------------+
    v
  Structured evidence pack
   { request_summary, stated_need, facts_gathered,
     open_questions, untrusted_content_notes }
    |
    |   <-- the raw request text stops here. It does not cross this line.
    v
  Agent 2: Policy / Risk Reviewer
    |   reads the pack plus the deterministic FACTS, nothing else
    v
  Deterministic rules engine  (authoritative, same engine as A)
    |
    v
  ProcurementDecision
```

Typical run: **4 LLM calls**. The extra one is the handoff.

Both architectures use the same tools, the same prompts' ground rules and the
same rules engine. That is on purpose: if their results differ, the difference
comes from the orchestration and nothing else.

## 5. The tools

Eight tools. Seven never leave the process.

| Tool | Type | Reads | Why it exists |
|---|---|---|---|
| `check_budget` | deterministic | `department_budgets.csv` | Policy 2. Money maths should never be a guess. |
| `search_software_catalog` | deterministic | `software_catalog.csv` | Policy 3. Finds same product, vendor or category. |
| `lookup_vendor_registry` | deterministic | `vendors.csv` | Policy 5. Also works out the review age from the snapshot date. |
| `vendor_risk_tool` | **external HTTP** | mock risk service | The only network call. Retries, then degrades honestly. |
| `lookup_policy` | deterministic | `procurement_policy.md` | Quotes the real policy text, so references are real. |
| `lookup_requester` | deterministic | `employees.csv` | Who is asking and which budget applies. |
| `get_purchase_history` | deterministic | `purchase_history.csv` | Spots renewals and existing spend. |
| `scan_request_text` | deterministic | the request | Policy 9. Regex, not a model, on purpose. |

The injection scanner is a regex and not an LLM call for a simple reason: asking
a model whether it is being manipulated is exactly the thing an injection
attacks. A regex gives the same answer every time.

## 6. How failures are handled

| What goes wrong | What happens |
|---|---|
| Vendor risk API returns 503 | Retry is skipped (a 503 is deliberate), `vendor_risk_unavailable` is raised, evidence says "unverified", request routes to Security. Never assumed good. |
| Vendor risk API unreachable | Two retries with backoff, then the same honest "unverified" path. |
| Registry and risk service disagree | `conflicting_vendor_evidence`. The copilot does not pick a side. |
| Model returns invalid JSON | One repair attempt, then the deterministic result is returned with `llm_unavailable`. |
| Model unreachable | Same fallback. The run degrades to rules-only instead of crashing. |
| Agent skips a required tool | The orchestrator runs it and records a note in the evidence. |
| Model invents a risk flag | Dropped. Added flags must come from a fixed vocabulary. |
| Model claims a complete request is incomplete | Dropped. Only the deterministic section 1 check can declare a request incomplete. |
| Request text contains instructions | Fenced as untrusted, scanned, flagged, ignored. |

## 7. Assumptions

1. **The snapshot date is 2026-09-30.** Every date check uses it. A test fails if
   any file in `src/` calls `date.today()`.
2. **Approvals are a policy mapping, not a judgement**, so the model cannot add
   or remove one. It changes the action and the wording instead.
3. **Overlap is never an automatic rejection.** Policy 3 says to surface it, so
   even a clear duplicate goes to a human with the existing tool shown.
4. **A vendor attribute is not a request attribute.** A vendor that processes
   personal data does not trigger Privacy review on its own; the request has to
   actually involve PII, or the vendor has to store data outside the region.
5. **An empty integrations list means "none needed"**, not "missing". Only an
   absent field counts as missing, otherwise every simple request would be
   marked incomplete.
6. **"Unknown" data access is missing information**, not a safe default.
7. **Evidence is rendered from tool results by code**, not written by the model.
   That is why every evidence line can name the tool it came from.

## 8. What I did not build, and why

- **No vector store or RAG.** The policy is one short markdown file. Splitting it
  into sections and reading them directly is more accurate and has no index to
  go stale.
- **No third agent.** The brief caps it at two and says agent count earns no
  marks. B already costs more than A; a third would cost more again.
- **No user authentication or real approval workflow.** The review queue is a
  JSON file. Building real auth would not test anything the brief asks about.
- **No caching between runs.** It would make the latency comparison dishonest.
- **No fine-tuning or few-shot examples.** The deterministic layer carries the
  accuracy, so the model does not need them.
