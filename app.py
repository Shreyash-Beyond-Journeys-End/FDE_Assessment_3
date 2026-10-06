"""Streamlit UI for the procurement copilot.

Three things a reviewer needs on one screen: what was asked for, what the
copilot found, and what it recommends. Nothing here approves anything; the only
action is handing the package to the people who can.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from src.config import SNAPSHOT_DATE, get_model_name, has_api_key
from src.contracts import ProcurementDecision
from src.data_access import load_requests
from src.solution import handle_request
from src.tools import DETERMINISTIC_TOOLS

ROOT = Path(__file__).resolve().parent
QUEUE_FILE = ROOT / "runtime" / "review_queue.json"

REQUESTS = load_requests()
BY_ID = {r["request_id"]: r for r in REQUESTS}

# Flags that should stand out in red rather than amber.
SERIOUS_FLAGS = {
    "prompt_injection_detected",
    "budget_insufficient",
    "conflicting_vendor_evidence",
    "vendor_risk_unavailable",
    "vendor_review_expired",
}

st.set_page_config(page_title="Procurement Request Copilot", layout="wide")


# ------------------------------------------------------------------ helpers

def load_queue() -> list[dict]:
    if not QUEUE_FILE.is_file():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_to_queue(decision: ProcurementDecision, architecture: str) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    queue = load_queue()
    queue.append({
        "sent_at": datetime.now().isoformat(timespec="seconds"),
        "request_id": decision.request_id,
        "architecture": architecture,
        "status": "waiting for human review",
        "routed_to": decision.required_approvals,
        "recommendation": decision.recommendation,
        "next_step": decision.next_step,
        "risk_flags": decision.risk_flags,
    })
    QUEUE_FILE.write_text(json.dumps(queue, indent=2), encoding="utf-8")


def money(value) -> str:
    return "not given" if value is None else f"${float(value):,.0f}"


def show_request(request: dict) -> None:
    st.markdown(f"**{request['product_name']}** from {request['vendor_name']}")
    left, right = st.columns(2)
    with left:
        st.markdown(
            f"- Request id: `{request['request_id']}`\n"
            f"- Requester: `{request['requester_id']}`\n"
            f"- Category: {request.get('category')}\n"
            f"- Urgency: {request.get('urgency')}"
        )
    with right:
        integrations = request.get("requested_integrations") or []
        st.markdown(
            f"- Annual cost: {money(request.get('annual_cost_usd'))}\n"
            f"- Users: {request.get('user_count') if request.get('user_count') is not None else 'not given'}\n"
            f"- Data access: {request.get('data_access_level')}\n"
            f"- Integrations: {', '.join(map(str, integrations)) or 'none'}"
        )
    st.markdown("**Business justification** (written by the requester, treated as data)")
    st.info(request.get("business_justification") or "(empty)")


def show_decision(decision: ProcurementDecision, architecture: str) -> None:
    st.success(decision.recommendation)

    if decision.risk_flags:
        chips = []
        for flag in decision.risk_flags:
            mark = "!!" if flag in SERIOUS_FLAGS else "!"
            chips.append(f"`{mark} {flag}`")
        st.markdown("**Risk flags** &nbsp; " + " ".join(chips), unsafe_allow_html=True)
    else:
        st.markdown("**Risk flags** &nbsp; none")

    left, right = st.columns(2)
    with left:
        st.markdown("**Approvals required**")
        for approval in decision.required_approvals:
            st.checkbox(approval, value=False, key=f"approve_{decision.request_id}_{approval}",
                        help="A human ticks this, not the copilot.")
    with right:
        st.markdown("**Missing information**")
        if decision.missing_information:
            for item in decision.missing_information:
                st.markdown(f"- {item}")
        else:
            st.markdown("Nothing required is missing.")

    st.markdown("**Next step**")
    st.warning(decision.next_step)

    st.markdown("**Evidence** - every line below came from a tool that ran on this request")
    for item in decision.evidence:
        marker = "code" if item.source in DETERMINISTIC_TOOLS else "api"
        if item.source == "orchestrator":
            marker = "note"
        with st.container(border=True):
            st.markdown(f"`{item.source}` ({marker}) &nbsp; {item.finding}")
            if item.reference:
                st.caption(f"Reference: {item.reference}")

    telemetry = decision.telemetry
    if telemetry:
        st.caption(
            f"Architecture: {architecture} | LLM calls: {telemetry.llm_calls} | "
            f"Tool calls: {telemetry.tool_calls} | Tools used: {', '.join(dict.fromkeys(telemetry.tool_names))}"
        )

    st.divider()
    st.markdown("**Human handoff**")
    st.caption(
        "The copilot cannot approve, buy, or change a budget. The only action it offers "
        "is sending this package to the people who can decide."
    )
    if st.button("Send to human review", type="primary", key=f"send_{decision.request_id}"):
        save_to_queue(decision, architecture)
        st.success(f"Sent to {', '.join(decision.required_approvals)}. See the Review queue tab.")


# --------------------------------------------------------------------- page

st.title("AI Procurement Request Copilot")
st.caption(
    f"Recommendations only. A human approves. Policy reference date {SNAPSHOT_DATE}."
)

with st.sidebar:
    st.header("Run a review")
    request_id = st.selectbox(
        "Request",
        list(BY_ID),
        format_func=lambda rid: f"{rid} - {BY_ID[rid]['product_name']}",
    )
    architecture = st.radio(
        "Architecture",
        ["single", "staged"],
        help="single = one agent. staged = an analyst agent hands an evidence pack to a reviewer agent.",
    )
    run_clicked = st.button("Run analysis", type="primary", use_container_width=True)
    st.divider()
    if has_api_key():
        st.caption(f"Model: `{get_model_name()}`")
    else:
        st.error("No HF_TOKEN set. Copy .env.example to .env and add a Hugging Face token.")

tab_review, tab_compare, tab_queue = st.tabs(["Review a request", "Compare architectures", "Review queue"])

with tab_review:
    left, right = st.columns([0.9, 1.1], gap="large")
    with left:
        st.subheader("Purchase request")
        show_request(BY_ID[request_id])
    with right:
        st.subheader("Copilot recommendation")
        if run_clicked:
            if not has_api_key():
                st.error(
                    "This app needs a Hugging Face token to run.\n\n"
                    "1. Copy `.env.example` to `.env`\n"
                    "2. Get a free token at https://huggingface.co/settings/tokens\n"
                    "3. Add it as `HF_TOKEN=hf_...` and restart"
                )
            else:
                with st.spinner(f"Running the {architecture} architecture ..."):
                    started = time.perf_counter()
                    try:
                        decision = handle_request(request_id, architecture=architecture)
                    except Exception as exc:
                        st.exception(exc)
                        decision = None
                    elapsed = time.perf_counter() - started
                if decision is not None:
                    st.session_state[f"decision_{request_id}"] = (decision, architecture, elapsed)

        stored = st.session_state.get(f"decision_{request_id}")
        if stored:
            decision, used_architecture, elapsed = stored
            st.caption(f"Took {elapsed:.1f} seconds")
            show_decision(decision, used_architecture)
        elif not run_clicked:
            st.info("Pick a request in the sidebar and press Run analysis.")

with tab_compare:
    st.subheader("Run the same request through both architectures")
    st.caption(
        "Both use the same tools and the same deterministic rules. Only the "
        "orchestration differs, so any difference comes from that."
    )
    if st.button("Run both on " + request_id, key="run_both"):
        if not has_api_key():
            st.error("No HF_TOKEN set. Add one to .env first.")
        else:
            results = {}
            for name in ("single", "staged"):
                with st.spinner(f"Running {name} ..."):
                    started = time.perf_counter()
                    results[name] = (handle_request(request_id, architecture=name),
                                     time.perf_counter() - started)
            st.session_state["compare"] = (request_id, results)

    compared = st.session_state.get("compare")
    if compared and compared[0] == request_id:
        _, results = compared
        rows = []
        for name, (decision, elapsed) in results.items():
            telemetry = decision.telemetry
            rows.append({
                "architecture": name,
                "seconds": round(elapsed, 1),
                "llm calls": telemetry.llm_calls if telemetry else None,
                "tool calls": telemetry.tool_calls if telemetry else None,
                "approvals": ", ".join(decision.required_approvals),
                "risk flags": ", ".join(decision.risk_flags),
                "evidence items": len(decision.evidence),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
        for name, (decision, _) in results.items():
            with st.expander(f"{name} - recommendation"):
                st.write(decision.recommendation)
                st.write("**Next step:** " + decision.next_step)

with tab_queue:
    st.subheader("Waiting for a human")
    queue = load_queue()
    if not queue:
        st.info("Nothing has been sent for review yet.")
    else:
        for entry in reversed(queue):
            with st.container(border=True):
                st.markdown(
                    f"**{entry['request_id']}** - {entry['status']} - sent {entry['sent_at']} "
                    f"({entry['architecture']})"
                )
                st.caption("Routed to: " + ", ".join(entry["routed_to"]))
                st.write(entry["recommendation"])
        if st.button("Clear queue"):
            QUEUE_FILE.write_text("[]", encoding="utf-8")
            st.rerun()

st.divider()
st.caption(
    "Recommendations are advisory. The copilot never purchases, approves, changes a budget, "
    "or overrides a Security, Privacy or Legal review."
)
