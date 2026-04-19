"""Agent B — QA Strategist
Generates a comprehensive, coverage-optimal test suite from the MRD.
"""

import streamlit as st
import json
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.helpers import log, llm_call, parse_llm_json, get_provider_label
from utils.hitl_chat import render_hitl_chat
from utils.ui_helpers import empty_state, error_card, step_header
from core.database import save_test_cases, db_log

SYSTEM_PROMPT = """You are a Principal QA Engineer and Test Architect with 15+ years of enterprise testing experience across web, mobile, API, and embedded systems.

Your task: Given a Master Requirement Document (MRD) JSON, produce an enterprise-grade, risk-prioritised test suite that goes well beyond happy-path coverage.

════════════════════════════════════════════════════════════
PART 1 — PER-REQUIREMENT BASELINE COVERAGE
════════════════════════════════════════════════════════════
For EVERY requirement in every module, generate ALL of the following that apply:

1. POSITIVE (happy path)
   — The canonical success scenario with valid inputs and a clean system state.

2. NEGATIVE
   — Invalid inputs, missing required fields, wrong data types, rejected operations.
   — Include the exact error message or HTTP status code expected.

3. BOUNDARY VALUE ANALYSIS
   — Test at min, min+1, max-1, max for every numeric/length/date constraint mentioned.
   — Zero, empty string, null/None, whitespace-only where relevant.

4. PRECONDITIONS — every test case must have an explicit precondition block:
   — State the exact system state required (e.g. "User account exists with role=admin and email verified").
   — State any seed data needed (e.g. "3 completed orders exist for user ID test-001").
   — Never leave preconditions blank or generic ("system is running" is not a precondition).

════════════════════════════════════════════════════════════
PART 2 — CROSS-CUTTING ENTERPRISE TEST CATEGORIES
════════════════════════════════════════════════════════════
Beyond per-requirement tests, you MUST generate tests from ALL of the following
categories that are relevant to the system described in the MRD. These apply
universally — do not skip a category just because it isn't explicitly called out
in a requirement.

A. SECURITY
   — Injection: SQLi, NoSQLi, command injection, LDAP injection in every free-text input.
   — XSS: stored and reflected, in name/description/address fields.
   — IDOR: access resource belonging to another user by guessing/manipulating IDs.
   — Auth bypass: missing/invalid/expired tokens on every protected endpoint.
   — Privilege escalation: low-privilege user performing high-privilege action.
   — Replay attack: re-submitting a captured valid request (especially for payments/state changes).
   — Mass assignment: sending extra fields in POST/PUT that should not be writable.
   — Sensitive data exposure: ensure tokens, passwords, PII are not returned in responses or logs.

B. PERFORMANCE & LOAD
   — Response time SLA: verify each stated timing requirement at the stated load level.
   — Throughput ceiling: what happens when traffic exceeds the stated concurrency limit.
   — Degradation under load: p95/p99 latency at 50%, 100%, and 150% of stated capacity.
   — Database query performance: slow queries under realistic data volumes.

C. STATE TRANSITION & WORKFLOW
   — All valid state transitions (e.g. draft→submitted→approved→closed).
   — All INVALID transitions (e.g. attempting to cancel an already-shipped order).
   — Re-entrant operations: triggering the same workflow step twice.
   — Interrupted workflow: what happens if the user abandons mid-flow and resumes.
   — Rollback / compensating transaction: partial failures that must be undone atomically.

D. CONCURRENCY & RACE CONDITIONS
   — Double-submit: user submits the same form/action twice in rapid succession
     (idempotency check — only one side effect must occur).
   — Concurrent resource contention: two users acting on the same scarce resource
     simultaneously (e.g. last item in inventory, same appointment slot, same coupon code).
   — Optimistic locking conflict: user A reads record, user B modifies it, user A saves —
     stale write must be rejected.
   — Session collision: same account logged in from two devices simultaneously.

E. DATA INTEGRITY & CONSISTENCY
   — Referential integrity: deleting a parent record that has active child records.
   — Orphaned records: what happens to related data when an entity is deleted/deactivated.
   — Audit trail completeness: all write operations produce a correct audit/log entry.
   — Idempotency of retries: network retry of a POST must not create duplicate records.

F. USABILITY & ACCESSIBILITY
   — Required field validation: clear, specific error messages per field (not "form invalid").
   — Inline vs. submit-time validation: errors appear at the right moment.
   — Keyboard navigation: all interactive elements reachable and operable without a mouse.
   — Screen reader labels: form inputs have correct ARIA labels.
   — Colour contrast / WCAG compliance where stated in NFRs.

G. INTERNATIONALISATION & LOCALISATION (if MRD mentions global use or multiple locales)
   — Currency formats with no decimal places (e.g. JPY, KWD) do not break rounding logic.
   — Non-Latin character sets in all free-text fields (Arabic RTL, CJK, emoji).
   — Date/time formats across locales (MM/DD/YYYY vs DD/MM/YYYY vs ISO 8601).
   — Address validation for non-US formats (UK postcodes, Irish Eircodes, EU formats).
   — Translated UI strings that are significantly longer than the English original.

H. INTEGRATION & CONTRACT
   — Third-party service unavailable: timeout, 503, malformed response — system degrades gracefully.
   — Webhook / callback delivery failure: retry logic, idempotency, dead-letter handling.
   — API contract validation: response schema matches documented contract (extra/missing fields).
   — Pagination edge cases: empty page, last page, page number beyond total pages.

I. RECOVERY & RESILIENCE
   — Mid-operation network drop: what happens if the connection is lost during a write.
   — Service restart mid-workflow: session and transaction state survives a restart.
   — Database failover: read replica promotion does not serve stale data.
   — Rate limiting & throttling: correct 429 response with Retry-After header.

════════════════════════════════════════════════════════════
PART 3 — TEST QUALITY STANDARDS
════════════════════════════════════════════════════════════
Every test case MUST meet all of the following standards:

STEPS:
— Numbered, specific, and executable with no ambiguity.
— Include the exact URL, endpoint, payload, or UI element where applicable.
— A junior tester who has never seen the system must be able to run this test without asking questions.
— BAD: "Navigate to the orders page and test cancellation."
— GOOD: "1. Login as user@test.com / P@ssw0rd123  2. GET /api/orders returns order ID ord-001 with status=processing  3. Send DELETE /api/orders/ord-001 within 30 minutes of order creation  4. Verify response is 200 {status: cancelled}  5. Verify GET /api/orders/ord-001 now returns status=cancelled  6. Verify refund event is emitted to the payment service."

EXPECTED_RESULT:
— Measurable and unambiguous.
— Include: HTTP status code, response body fields, UI state, timing threshold, side effects (emails sent, events fired, DB state).
— BAD: "Order is cancelled successfully."
— GOOD: "HTTP 200; body.status = 'cancelled'; cancellation_at timestamp populated; refund initiated event in audit log; user receives cancellation email within 60s."

PRECONDITIONS:
— Never generic. Always state exact data setup required.
— BAD: "User is logged in."
— GOOD: "Authenticated user session for role=customer, account age > 24h, email verified, no active suspension. Order ord-001 exists with status=processing, created 10 minutes ago, containing 2 line items totalling $89.99."

TEST_DATA:
— List exact values: email addresses, passwords, IDs, amounts, strings, file names.
— Include both valid and invalid variants as appropriate.
— For security tests: list the exact payloads (e.g. ' OR 1=1--, <script>alert(1)</script>).

SEVERITY:
— Critical: data loss, security breach, financial error, complete feature outage.
— High: core user journey broken, significant data corruption, auth failure.
— Medium: degraded experience, non-critical feature broken, performance below SLA.
— Low: cosmetic, minor UX issue, informational.

════════════════════════════════════════════════════════════
OUTPUT FORMAT
════════════════════════════════════════════════════════════
Respond ONLY with a valid JSON array. No preamble, no explanation, no markdown outside the array.
Each element must exactly match this schema:
{
  "TC_ID": "TC-001",
  "Module": "<module name from MRD>",
  "Req_ID": "<REQ-NNN this test primarily covers, or CROSS-CUT for category tests>",
  "Type": "Functional|Security|Performance|Boundary|State Transition|Concurrency|Data Integrity|Accessibility|Internationalisation|Integration|Resilience|Negative|API|UI",
  "Category": "Per-Requirement|Security|Performance|State Transition|Concurrency|Data Integrity|Accessibility|Internationalisation|Integration|Resilience",
  "Scenario": "<specific, unambiguous test scenario title — max 100 chars>",
  "Test_Kind": "Positive|Negative",
  "Severity": "Critical|High|Medium|Low",
  "Preconditions": "<exact system state and data setup required — never generic>",
  "Steps": "<numbered steps, one per line, fully executable>",
  "Expected_Result": "<measurable outcome: HTTP codes, field values, timing, side effects>",
  "Test_Data": "<exact values, payloads, IDs, credentials needed>",
  "Status": "Pending"
}"""


def run_agent_b(master_req: dict):
    log("Agent B started — Test Suite Design", "STEP")

    with st.spinner("Generating test suite..."):
        llm_output = llm_call(
            SYSTEM_PROMPT,
            json.dumps(master_req, indent=2),
            "",
            max_tokens=16000,
        )

    rows = parse_llm_json(llm_output, None)
    if rows is None:
        log("LLM output could not be parsed as JSON", "WARN")
        st.error("Could not parse the LLM response. Check the Logs tab and try again.")
        return None

    if isinstance(rows, list):
        df = pd.DataFrame(rows)
    else:
        st.error("Unexpected response format from LLM.")
        return None

    # Ensure all expected columns exist
    required_cols = ["TC_ID", "Module", "Req_ID", "Type", "Category", "Scenario", "Test_Kind",
                     "Severity", "Preconditions", "Steps", "Expected_Result", "Test_Data", "Status"]
    for col in required_cols:
        if col not in df.columns:
            df[col] = ""

    # Normalise any columns that the LLM returned as lists/dicts into plain strings.
    # Streamlit's data_editor cannot edit list-typed columns as text.
    def _to_str(val):
        if isinstance(val, list):
            return "\n".join(str(v) for v in val)
        if isinstance(val, dict):
            return str(val)
        return val

    str_cols = ["TC_ID", "Module", "Req_ID", "Type", "Category", "Scenario", "Test_Kind",
                "Severity", "Preconditions", "Steps", "Expected_Result", "Test_Data",
                "Status", "Preconditions"]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].apply(_to_str).astype(str)

    log(f"Generated {len(df)} test cases across {df['Module'].nunique()} modules", "OK")
    st.session_state.test_cases_df = df
    run_id = st.session_state.get("run_id")
    if run_id:
        save_test_cases(run_id, df)
        db_log(run_id, "OK", f"Test cases saved — {len(df)} cases")
    return df


def render_agent_b():
    st.subheader("🧪 Agent B — QA Strategist")
    st.caption("Generates an enterprise-grade, risk-prioritised test suite: per-requirement baseline + cross-cutting security, concurrency, state transition, internationalisation, integration, and resilience coverage.")

    if st.session_state.pipeline_stage < 3:
        empty_state(
            "Waiting for Requirements",
            "Agent A needs to analyse and approve the requirements before test cases can be generated.",
            "Go to the Requirements tab, run Agent A, and approve the output."
        )
        return

    if st.session_state.pipeline_stage == 3 and st.session_state.test_cases_df is None:
        req = st.session_state.master_req
        if not req:
            st.error("No requirements found.")
            return
        req_count = sum(len(m.get("requirements", [])) for m in req.get("modules", []))
        st.info(f"Ready to generate test cases for **{req.get('product','?')}** — {req_count} requirements across {len(req.get('modules',[]))} modules.")
        st.caption(
            "Coverage includes: positive/negative/boundary per requirement, plus security (SQLi/XSS/IDOR/replay), "
            "concurrency & race conditions, state transitions, data integrity, accessibility, "
            "internationalisation, integration contracts, and resilience."
        )

        c1, c2 = st.columns([2, 1])
        with c1:
            if st.button("▶ Run Agent B — Design Test Suite", use_container_width=True, type="primary"):
                run_agent_b(req)
                st.rerun()
        with c2:
            st.caption(f"Provider: {get_provider_label()}")
        return

    if st.session_state.pipeline_stage >= 3 and st.session_state.test_cases_df is not None:
        df = st.session_state.test_cases_df

        # ── Metrics bar ───────────────────────────────────────────
        c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
        c1.metric("📋 Total",       len(df))
        c2.metric("🔴 Critical",    len(df[df["Severity"] == "Critical"])   if "Severity"  in df.columns else 0)
        c3.metric("✅ Positive",    len(df[df["Test_Kind"] == "Positive"])  if "Test_Kind" in df.columns else 0)
        c4.metric("❌ Negative",    len(df[df["Test_Kind"] == "Negative"])  if "Test_Kind" in df.columns else 0)
        c5.metric("🔒 Security",    len(df[df["Category"] == "Security"])   if "Category"  in df.columns else
                                    len(df[df["Type"]     == "Security"])   if "Type"      in df.columns else 0)
        c6.metric("⚡ Concurrency", len(df[df["Category"] == "Concurrency"])if "Category"  in df.columns else 0)
        c7.metric("🔄 State Trans.", len(df[df["Category"] == "State Transition"]) if "Category" in df.columns else 0)
        c8.metric("📦 Modules",     df["Module"].nunique() if "Module" in df.columns else 0)

        st.divider()

        # ── Coverage matrices ─────────────────────────────────────
        tab_req, tab_cat, tab_mod = st.tabs(["📊 Requirements × Types", "🏷 Category Breakdown", "📦 Module Breakdown"])

        with tab_req:
            if "Req_ID" in df.columns and "Type" in df.columns:
                pivot = df.pivot_table(index="Req_ID", columns="Type", aggfunc="size", fill_value=0)
                st.dataframe(pivot, use_container_width=True)
            else:
                st.info("Coverage matrix requires Req_ID and Type columns.")

        with tab_cat:
            if "Category" in df.columns:
                cat_counts = df["Category"].value_counts().reset_index()
                cat_counts.columns = ["Category", "Count"]
                sev_by_cat = df.groupby(["Category", "Severity"]).size().unstack(fill_value=0)
                st.dataframe(sev_by_cat, use_container_width=True)
            else:
                st.info("Category column not present — re-run Agent B with live LLM to populate.")

        with tab_mod:
            if "Module" in df.columns and "Category" in df.columns:
                mod_cat = df.pivot_table(index="Module", columns="Category", aggfunc="size", fill_value=0)
                st.dataframe(mod_cat, use_container_width=True)
            elif "Module" in df.columns and "Type" in df.columns:
                mod_type = df.pivot_table(index="Module", columns="Type", aggfunc="size", fill_value=0)
                st.dataframe(mod_type, use_container_width=True)

        # ── Filters ───────────────────────────────────────────────
        with st.expander("🔍 Filter test cases"):
            fc1, fc2, fc3, fc4, fc5 = st.columns(5)
            sev_filter  = fc1.multiselect("Severity",  df["Severity"].unique().tolist()  if "Severity"  in df.columns else [])
            mod_filter  = fc2.multiselect("Module",    df["Module"].unique().tolist()    if "Module"    in df.columns else [])
            type_filter = fc3.multiselect("Type",      df["Type"].unique().tolist()      if "Type"      in df.columns else [])
            cat_filter  = fc4.multiselect("Category",  df["Category"].unique().tolist()  if "Category"  in df.columns else [])
            kind_filter = fc5.multiselect("Kind",      df["Test_Kind"].unique().tolist() if "Test_Kind" in df.columns else [])

        fdf = df.copy()
        if sev_filter:  fdf = fdf[fdf["Severity"].isin(sev_filter)]
        if mod_filter:  fdf = fdf[fdf["Module"].isin(mod_filter)]
        if type_filter: fdf = fdf[fdf["Type"].isin(type_filter)]
        if cat_filter:  fdf = fdf[fdf["Category"].isin(cat_filter)]
        if kind_filter: fdf = fdf[fdf["Test_Kind"].isin(kind_filter)]

        st.write(f"Showing **{len(fdf)}** of **{len(df)}** test cases. Edit below:")

        # Defensive: ensure every text column is a plain string before data_editor.
        # LLMs sometimes return steps/preconditions as lists; Streamlit can't edit those.
        _text_cols = ["TC_ID","Module","Req_ID","Type","Category","Scenario","Test_Kind",
                      "Severity","Preconditions","Steps","Expected_Result","Test_Data","Status"]
        for _c in _text_cols:
            if _c in fdf.columns:
                fdf[_c] = fdf[_c].apply(
                    lambda v: "\n".join(str(x) for x in v) if isinstance(v, list)
                    else (str(v) if not isinstance(v, str) else v)
                )

        display_cols = ["TC_ID", "Module", "Category", "Type", "Scenario", "Test_Kind", "Severity",
                        "Preconditions", "Steps", "Expected_Result", "Test_Data", "Status"]
        display_cols = [c for c in display_cols if c in fdf.columns]

        edited_df = st.data_editor(
            fdf[display_cols],
            use_container_width=True,
            num_rows="dynamic",
            hide_index=True,
            key="tc_editor",
            column_config={
                "TC_ID":           st.column_config.TextColumn("TC ID", width="small"),
                "Module":          st.column_config.TextColumn("Module", width="medium"),
                "Category":        st.column_config.SelectboxColumn("Category", options=[
                    "Per-Requirement","Security","Performance","State Transition","Concurrency",
                    "Data Integrity","Accessibility","Internationalisation","Integration","Resilience",
                ]),
                "Type":            st.column_config.SelectboxColumn("Type", options=[
                    "Functional","Security","Performance","Boundary","State Transition","Concurrency",
                    "Data Integrity","Accessibility","Internationalisation","Integration","Resilience",
                    "Negative","API","UI",
                ]),
                "Scenario":        st.column_config.TextColumn("Scenario", width="large"),
                "Test_Kind":       st.column_config.SelectboxColumn("Kind",     options=["Positive","Negative"]),
                "Severity":        st.column_config.SelectboxColumn("Severity", options=["Critical","High","Medium","Low"]),
                "Preconditions":   st.column_config.TextColumn("Preconditions", width="large"),
                "Steps":           st.column_config.TextColumn("Steps",         width="large"),
                "Expected_Result": st.column_config.TextColumn("Expected",      width="large"),
                "Test_Data":       st.column_config.TextColumn("Test Data",     width="medium"),
                "Status":          st.column_config.SelectboxColumn("Status",   options=["Pending","Approved","Skip","Blocked"]),
            },
        )

        col_s, col_a, col_dl = st.columns(3)
        with col_s:
            if st.button("💾 Save edits"):
                st.session_state.test_cases_df = edited_df
                log("User saved test case table edits", "OK")
                st.success("Saved.")
        with col_a:
            if st.button("➕ Add row"):
                import pandas as pd
                new_row = pd.DataFrame([{
                    "TC_ID": f"TC-{len(df)+1:03d}", "Module": "", "Req_ID": "",
                    "Category": "Per-Requirement",
                    "Type": "Functional", "Scenario": "New test case",
                    "Test_Kind": "Positive", "Severity": "Medium",
                    "Preconditions": "", "Steps": "1. ",
                    "Expected_Result": "", "Test_Data": "", "Status": "Pending",
                }])
                st.session_state.test_cases_df = pd.concat(
                    [st.session_state.test_cases_df, new_row], ignore_index=True
                )
                st.rerun()
        with col_dl:
            csv = st.session_state.test_cases_df.to_csv(index=False).encode()
            st.download_button("⬇ Download CSV", csv, "test_cases.csv", "text/csv", use_container_width=True)

        st.divider()

        if st.session_state.pipeline_stage == 3:
            render_hitl_chat(
                agent_key="agent_b",
                context_label="Test Suite",
                get_context=lambda: (
                    st.session_state.test_cases_df.to_json(orient="records", indent=2)
                    if st.session_state.test_cases_df is not None else "[]"
                ),
                apply_patch=lambda patch: st.session_state.update({
                    "test_cases_df": pd.DataFrame(patch) if isinstance(patch, list) else st.session_state.test_cases_df
                }),
                system_hint=(
                    "Focus on test coverage gaps. When adding test cases, "
                    "return the FULL updated array (not just the new entries). "
                    "Assign the next TC-NNN id. Keep all existing test cases intact."
                ),
            )
            st.divider()
            st.write("**Human Approval Gate — Level 2**")
            st.caption("Review test coverage above. Ensure all critical requirements have test cases before approving.")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Approve test suite & proceed to Execution", use_container_width=True, type="primary"):
                    st.session_state.test_cases_df = edited_df
                    log("HUMAN APPROVAL L2: Test suite approved by user", "APPROVED")
                    st.session_state.pipeline_stage = 4
                    st.rerun()
            with c2:
                if st.button("🔄 Regenerate test suite", use_container_width=True):
                    st.session_state.test_cases_df = None
                    st.session_state.pipeline_stage = 3
                    st.rerun()
        else:
            st.success("✅ Test suite approved. Proceed to Agent C.")
