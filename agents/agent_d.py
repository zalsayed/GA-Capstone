"""Agent D — Defect Manager
Analyses execution failures with Claude, produces actionable defect reports with root-cause and fix proposals.
"""

import streamlit as st
import pandas as pd
import json
import datetime
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.helpers import log, llm_call, parse_llm_json, get_provider_label
from utils.hitl_chat import render_hitl_chat
from utils.ui_helpers import empty_state, error_card, step_header
from core.database import save_defects, db_log

SYSTEM_PROMPT = """You are a Senior QA Defect Analyst and Software Engineer.

Given a list of failed test cases (with expected vs actual results), produce a professional defect report.

For EACH failed test case, you must:
1. Write a clear, technical defect description (not just a restatement of the failure)
2. Identify the most likely root cause category from: [Backend Logic, Frontend Rendering, API Contract, Auth/AuthZ, Database, Configuration, Third-Party Integration, Race Condition, Missing Validation]
3. Assign severity based on user/business impact (not just test importance)
4. Propose a SPECIFIC, actionable fix — include code snippets where relevant
5. Suggest a JIRA-style ticket title

OUTPUT: Respond ONLY with a JSON array. Each element:
{
  "Defect_No": "DEF-001",
  "TC_ID": "<from failed test>",
  "Module": "<module>",
  "Severity": "Critical|High|Medium|Low",
  "Summary": "<JIRA-style: [Module] Short defect title>",
  "Description": "<technical description of the defect, 2-3 sentences>",
  "Expected": "<expected result>",
  "Actual": "<actual result>",
  "Root_Cause": "<root cause category: one of the list above>",
  "Root_Cause_Analysis": "<1-2 sentence technical explanation of WHY this likely happened>",
  "Proposed_Fix": "<specific, actionable fix — include code snippet if relevant>",
  "Screenshot": "screenshots/<tc_id>_fail.png",
  "JIRA_Ticket": "PROJ-<number>",
  "Affected_Environments": ["dev", "staging"],
  "Status": "Open"
}"""


def run_agent_d(execution_results: pd.DataFrame):
    log("Agent D started — Defect Analysis", "STEP")

    failures = execution_results[execution_results["Status"] == "FAIL"].copy()
    log(f"Analysing {len(failures)} failures from {len(execution_results)} executed tests", "INFO")

    empty_cols = ["Defect_No","TC_ID","Module","Severity","Summary","Description",
                  "Expected","Actual","Root_Cause","Root_Cause_Analysis","Proposed_Fix",
                  "Screenshot","JIRA_Ticket","Affected_Environments","Status"]

    if failures.empty:
        log("No failures detected — all tests passed! 🎉", "OK")
        empty_df = pd.DataFrame(columns=empty_cols)
        st.session_state.defects_df = empty_df
        run_id = st.session_state.get("run_id")
        if run_id:
            save_defects(run_id, empty_df)
            db_log(run_id, "OK", "No defects — all tests passed")
        return st.session_state.defects_df

    with st.spinner("Analysing failures and generating defect report..."):
        llm_output = llm_call(
            SYSTEM_PROMPT,
            failures.to_json(orient="records", indent=2),
            "",
            max_tokens=8096,
        )

    rows = parse_llm_json(llm_output, None)
    if rows is None:
        log("LLM output could not be parsed as JSON", "WARN")
        st.error("Could not parse the LLM defect response. Check the Logs tab.")
        return None

    if isinstance(rows, list):
        df = pd.DataFrame(rows)
    else:
        st.error("Unexpected response format from LLM.")
        return None

    for col in empty_cols:
        if col not in df.columns:
            df[col] = ""

    # Normalise any list/dict columns the LLM may have returned
    def _to_str(val):
        if isinstance(val, list):
            return "\n".join(str(v) for v in val)
        if isinstance(val, dict):
            return str(val)
        return val

    for col in empty_cols:
        if col in df.columns:
            df[col] = df[col].apply(_to_str).astype(str)

    st.session_state.defects_df = df
    log(f"Defect report complete — {len(df)} defects identified", "OK")
    return df


def render_agent_d():
    st.subheader("🐛 Agent D — Defect Manager")
    st.caption("Claude analyses every failure, identifies root causes, and proposes specific fixes with code snippets.")

    if st.session_state.pipeline_stage < 6:
        empty_state(
            "Waiting for Execution",
            "Agent C needs to run the test suite before defects can be analysed.",
            "Go to the Execution tab and run the test suite."
        )
        return

    results_df = st.session_state.get("execution_results")
    if results_df is None:
        st.warning("No execution results found.")
        return

    if st.session_state.pipeline_stage == 6 and st.session_state.defects_df is None:
        fail_count = len(results_df[results_df["Status"] == "FAIL"])
        st.info(f"Found **{fail_count}** failures to analyse out of {len(results_df)} executed tests.")
        c1, c2 = st.columns([2, 1])
        with c1:
            if st.button("▶ Run Agent D — Analyse Defects", use_container_width=True, type="primary"):
                run_agent_d(results_df)
                st.session_state.pipeline_stage = 8
                st.rerun()
        with c2:
            st.caption(f"Provider: {get_provider_label()}")
        return

    defects = st.session_state.defects_df
    if defects is None:
        return

    # Summary metrics
    total = len(results_df)
    pass_c = len(results_df[results_df["Status"] == "PASS"])
    fail_c = len(results_df[results_df["Status"] == "FAIL"])
    pass_rate = (pass_c / total * 100) if total > 0 else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("🧪 Executed", total)
    c2.metric("✅ Passed", pass_c)
    c3.metric("❌ Failed", fail_c)
    c4.metric("🐛 Defects", len(defects))
    c5.metric("📈 Pass Rate", f"{pass_rate:.1f}%")

    st.divider()

    if defects.empty:
        st.success("🎉 Zero defects found. All tests passed!")
        _render_exports(results_df, defects, pass_c, fail_c, total, pass_rate)
        return

    # Defect severity breakdown
    if "Severity" in defects.columns:
        sev_counts = defects["Severity"].value_counts()
        scols = st.columns(min(4, len(sev_counts)))
        icons = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}
        for i, (sev, cnt) in enumerate(sev_counts.items()):
            scols[i % len(scols)].metric(f"{icons.get(sev,'⚪')} {sev}", cnt)

    st.divider()

    # Root cause distribution
    if "Root_Cause" in defects.columns and defects["Root_Cause"].any():
        with st.expander("📊 Root Cause Distribution"):
            rc_counts = defects["Root_Cause"].value_counts()
            rc_df = rc_counts.reset_index()
            rc_df.columns = ["Root Cause", "Count"]
            st.dataframe(rc_df, use_container_width=True, hide_index=True)

    st.divider()

    # Editable defect log
    st.write("**Defect Log** — edit status and proposed fixes inline:")
    display_cols = ["Defect_No","TC_ID","Module","Severity","Summary",
                    "Root_Cause","JIRA_Ticket","Status"]
    display_cols = [c for c in display_cols if c in defects.columns]

    edited = st.data_editor(
        defects[display_cols],
        use_container_width=True, num_rows="dynamic", hide_index=True,
        key="defect_editor",
        column_config={
            "Defect_No":  st.column_config.TextColumn("Defect #", width="small"),
            "TC_ID":      st.column_config.TextColumn("TC ID", width="small"),
            "Module":     st.column_config.TextColumn("Module", width="medium"),
            "Severity":   st.column_config.SelectboxColumn("Severity", options=["Critical","High","Medium","Low"]),
            "Summary":    st.column_config.TextColumn("Summary", width="large"),
            "Root_Cause": st.column_config.SelectboxColumn("Root Cause", options=[
                "Backend Logic","Frontend Rendering","API Contract","Auth/AuthZ",
                "Database","Configuration","Third-Party Integration","Race Condition","Missing Validation"
            ]),
            "JIRA_Ticket":st.column_config.TextColumn("JIRA", width="small"),
            "Status":     st.column_config.SelectboxColumn("Status", options=["Open","In Progress","Fixed","Won't Fix","Duplicate","Needs Info"]),
        },
    )

    if st.button("💾 Save defect edits"):
        st.session_state.defects_df = edited
        log("User saved defect log edits", "OK")
        st.success("Saved.")

    st.divider()

    # Detail cards (Claude's full analysis)
    st.write("**Full Defect Analysis Cards**")
    for _, row in defects.iterrows():
        sev = row.get("Severity", "")
        icon = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}.get(sev, "⚪")
        with st.expander(f"{icon} {row.get('Defect_No','')} — {row.get('Summary', row.get('Description',''))[:70]}"):
            col_meta1, col_meta2, col_meta3 = st.columns(3)
            col_meta1.write(f"**TC:** {row.get('TC_ID','')} | **Module:** {row.get('Module','')}")
            col_meta2.write(f"**Severity:** {sev}")
            col_meta3.write(f"**JIRA:** {row.get('JIRA_Ticket','')} | **Status:** {row.get('Status','Open')}")

            if row.get("Description"):
                st.write("**Description:**")
                st.write(row.get("Description"))

            col_e, col_a = st.columns(2)
            with col_e:
                st.write("**Expected:**")
                st.code(row.get("Expected", ""), language=None)
            with col_a:
                st.write("**Actual:**")
                st.code(row.get("Actual", ""), language=None)

            if row.get("Root_Cause_Analysis"):
                st.write("**Root Cause Analysis:**")
                st.warning(f"**{row.get('Root_Cause','')}**: {row.get('Root_Cause_Analysis','')}")

            if row.get("Proposed_Fix"):
                st.write("**Proposed Fix:**")
                st.info(row.get("Proposed_Fix",""))

            if row.get("Screenshot"):
                if os.path.exists(row.get("Screenshot", "")):
                    st.image(row["Screenshot"], caption=row["Screenshot"], width=600)
                else:
                    st.caption(f"Screenshot path: `{row.get('Screenshot')}` — available when connected to a live app running Playwright")

    st.divider()

    render_hitl_chat(
        agent_key="agent_d",
        context_label="Defect Report",
        get_context=lambda: (
            st.session_state.defects_df.to_json(orient="records", indent=2)
            if st.session_state.defects_df is not None else "[]"
        ),
        apply_patch=lambda patch: st.session_state.update({
            "defects_df": pd.DataFrame(patch) if isinstance(patch, list) else st.session_state.defects_df
        }),
        system_hint=(
            "The user is reviewing the defect report. They may: ask about specific defects, "
            "request additional defects be added for issues they spotted manually, "
            "change severity, update proposed fixes, or update root cause categories. "
            "When returning updated defects, return the FULL array. "
            "Assign the next DEF-NNN id for new defects."
        ),
    )

    st.divider()

    _render_exports(results_df, defects, pass_c, fail_c, total, pass_rate)
    st.success("🏁 Pipeline complete.")


def _render_exports(results_df, defects, pass_c, fail_c, total, pass_rate):
    st.write("**Export Reports**")
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.download_button(
            "⬇ Defects CSV",
            defects.to_csv(index=False).encode(),
            "defects.csv", "text/csv",
            use_container_width=True,
        )

    with col2:
        report = {
            "generated_at": datetime.datetime.now().isoformat(),
            "pipeline": "Req2Defect v2",
            "summary": {"total": total, "passed": pass_c, "failed": fail_c,
                        "pass_rate": f"{pass_rate:.1f}%", "defect_count": len(defects)},
            "defects": defects.to_dict(orient="records"),
            "results": results_df.to_dict(orient="records"),
        }
        st.download_button(
            "⬇ Full Report JSON",
            json.dumps(report, indent=2, default=str).encode(),
            "req2defect_report.json", "application/json",
            use_container_width=True,
        )

    with col3:
        md = _build_markdown_report(defects, results_df, pass_c, fail_c, total, pass_rate)
        st.download_button(
            "⬇ Report Markdown",
            md.encode(),
            "req2defect_report.md", "text/markdown",
            use_container_width=True,
        )

    with col4:
        jira_csv = _build_jira_import(defects)
        st.download_button(
            "⬇ JIRA Import CSV",
            jira_csv.encode(),
            "jira_import.csv", "text/csv",
            use_container_width=True,
        )

    with col5:
        from core.config import get_config
        cfg = get_config()
        if cfg.jira_url and cfg.jira_api_token:
            already_pushed = st.session_state.get("jira_pushed", False)
            if already_pushed:
                st.success("✅ Pushed to Jira")
            else:
                if st.button("🔗 Push to Jira", use_container_width=True, type="primary"):
                    from core.jira_integration import get_jira_client
                    from core.database import save_defects
                    with st.spinner("Pushing defects to Jira..."):
                        ok, msg = get_jira_client().test_connection()
                        if not ok:
                            st.error(f"Jira connection failed: {msg}")
                        else:
                            updated = get_jira_client().push_defects(defects)
                            st.session_state.defects_df = updated
                            run_id = st.session_state.get("run_id")
                            if run_id:
                                save_defects(run_id, updated)
                            st.session_state.jira_pushed = True
                            tickets = [t for t in updated.get("JIRA_Ticket", pd.Series()).tolist() if t]
                            log(f"Jira push complete — {len(tickets)} tickets created", "OK")
                            st.success(f"Created {len(tickets)} ticket(s): {', '.join(tickets)}")
                            st.rerun()
        else:
            st.caption("Configure JIRA_URL and JIRA_API_TOKEN to enable push")


def _build_markdown_report(defects, results_df, pass_c, fail_c, total, pass_rate) -> str:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Req2Defect Pipeline — Test Execution Report",
        f"**Generated:** {ts}  |  **Pipeline:** Req2Defect v2",
        "",
        "## Executive Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Tests Executed | {total} |",
        f"| Passed | {pass_c} |",
        f"| Failed | {fail_c} |",
        f"| Pass Rate | {pass_rate:.1f}% |",
        f"| Defects Identified | {len(defects)} |",
        "",
        "## Defect Report",
        "",
    ]
    if defects.empty:
        lines.append("✅ **No defects found.** All tests passed.")
    else:
        for _, row in defects.iterrows():
            lines += [
                f"### {row.get('Defect_No','')} — {row.get('Summary', row.get('Description',''))[:80]}",
                "",
                f"- **Severity:** {row.get('Severity','')}",
                f"- **Module:** {row.get('Module','')}",
                f"- **TC ID:** {row.get('TC_ID','')}",
                f"- **JIRA:** {row.get('JIRA_Ticket','')}",
                f"- **Status:** {row.get('Status','Open')}",
                f"- **Root Cause:** {row.get('Root_Cause','')}",
                "",
                f"**Description:** {row.get('Description','')}",
                "",
                f"**Expected:** {row.get('Expected','')}",
                "",
                f"**Actual:** {row.get('Actual','')}",
                "",
                f"**Root Cause Analysis:** {row.get('Root_Cause_Analysis','')}",
                "",
                f"**Proposed Fix:** {row.get('Proposed_Fix','')}",
                "",
                "---",
                "",
            ]
    return "\n".join(lines)


def _build_jira_import(defects: pd.DataFrame) -> str:
    """Generate JIRA-compatible CSV import."""
    import io
    jira_cols = {
        "Summary": "Summary",
        "Description": "Description",
        "Severity": "Priority",
        "Module": "Component",
        "Status": "Status",
        "TC_ID": "Labels",
    }
    if defects.empty:
        return "Summary,Description,Priority,Component,Status,Labels\n"
    jira_df = pd.DataFrame()
    for src, dst in jira_cols.items():
        if src in defects.columns:
            jira_df[dst] = defects[src]
        else:
            jira_df[dst] = ""
    jira_df["Issue Type"] = "Bug"
    return jira_df.to_csv(index=False)
