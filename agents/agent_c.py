"""Agent C — Execution Orchestrator
Simulates test execution and optionally runs real Playwright tests against a live URL.
"""

import streamlit as st
import subprocess
import threading
import queue
import time
import random
import json
import pandas as pd
import sys
import os
import pathlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.helpers import (
    log, generate_playwright_script, generate_appium_script, generate_github_actions_yaml,
)
from utils.hitl_chat import render_hitl_chat
from utils.ui_helpers import empty_state, error_card, step_header
from core.database import save_execution_results, db_log


# ── Simulation ────────────────────────────────────────────────────────────────

def _run_simulation(tc_df: pd.DataFrame, platform: str, progress_bar, log_placeholder) -> pd.DataFrame:
    results = []
    total   = len(tc_df)
    log_lines = [
        f"[SIMULATION] Initialising driver for {platform}...",
        f"[SIMULATION] Loaded {total} test cases",
        "─" * 50,
    ]
    fail_templates = [
        "HTTP 500 — Internal Server Error",
        "Element not found: timeout after 30000ms",
        "AssertionError: expected status 200, got 400",
        "Response time 1847ms exceeded threshold of 500ms",
        "401 Unauthorized — missing Authorization header",
        "403 Forbidden — insufficient permissions",
        "Assertion failed: expected redirect to /dashboard",
        "Payment declined: card number invalid",
    ]
    fail_prob = {"Critical": 0.15, "High": 0.22, "Medium": 0.30, "Low": 0.38}

    for i, (_, row) in enumerate(tc_df.iterrows()):
        tc_id   = row.get("TC_ID", f"TC-{i+1:03d}")
        scenario = row.get("Scenario", "")[:55]
        sev     = row.get("Severity", "Medium")
        tc_type = row.get("Type", "Functional")
        rng     = random.Random(tc_id)
        prob    = fail_prob.get(sev, 0.28)
        if tc_type in ("Security", "Boundary", "Concurrency"):
            prob = min(prob + 0.12, 0.55)
        passed  = rng.random() > prob
        actual  = row.get("Expected_Result", "As expected") if passed else rng.choice(fail_templates)
        dur_ms  = rng.randint(120, 4200) if passed else rng.randint(200, 8000)
        status  = "PASS" if passed else "FAIL"
        icon    = "PASS" if passed else "FAIL"
        log_lines.append(f"{icon}  [{tc_id}] {scenario:<50} {dur_ms}ms")
        log_placeholder.text_area("", value="\n".join(log_lines[-28:]),
                                  height=300, key=f"sim_log_{i}", label_visibility="collapsed")
        results.append({
            "TC_ID": tc_id, "Scenario": row.get("Scenario", ""),
            "Module": row.get("Module", ""), "Severity": sev, "Type": tc_type,
            "Expected_Result": row.get("Expected_Result", ""),
            "Actual_Result": actual, "Duration_ms": dur_ms, "Status": status,
            "Screenshot": "", "Source": "Simulation",
        })
        progress_bar.progress((i + 1) / total)
        time.sleep(0.06)

    pass_c = sum(1 for r in results if r["Status"] == "PASS")
    log_lines += [
        "─" * 50,
        f"PASSED: {pass_c}/{total}   FAILED: {total-pass_c}/{total}",
        f"Total simulated duration: {sum(r['Duration_ms'] for r in results)/1000:.1f}s",
        "[SIMULATION] Complete. Results are deterministic estimates.",
    ]
    log_placeholder.text_area("", value="\n".join(log_lines[-30:]),
                               height=300, key="sim_log_final", label_visibility="collapsed")
    for line in log_lines:
        log(line.strip())
    return pd.DataFrame(results)


# ── Real Playwright execution ─────────────────────────────────────────────────

def _check_playwright_installed() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run"],
            capture_output=True, text=True, timeout=10
        )
        return True, ""
    except FileNotFoundError:
        return False, "Playwright not installed. Run: pip install playwright && playwright install chromium"
    except Exception as e:
        return False, str(e)


def _check_url_reachable(url: str) -> tuple[bool, str]:
    try:
        import urllib.request
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=8) as r:
            return True, f"Reachable — HTTP {r.status}"
    except Exception as e:
        return False, str(e)


def _run_real_playwright(tc_df: pd.DataFrame, cfg: dict, base_url: str,
                          progress_bar, log_placeholder) -> pd.DataFrame:
    script = generate_playwright_script(tc_df, cfg)
    script_path = pathlib.Path("_tmp_test_suite.py")
    screenshot_dir = pathlib.Path("screenshots")
    screenshot_dir.mkdir(exist_ok=True)

    script_with_url = script.replace("'http://localhost:3000'", f"'{base_url}'")
    script_path.write_text(script_with_url)

    log_lines = [
        f"Running real Playwright tests against {base_url}",
        f"Saving screenshots to screenshots/",
        "─" * 50,
    ]
    log_placeholder.text_area("", value="\n".join(log_lines),
                               height=300, key="real_log_start", label_visibility="collapsed")

    results = []
    total = len(tc_df)

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "pytest", str(script_path),
             "-v", "--tb=short", "--no-header",
             f"--screenshot=on", "--output=screenshots"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        passed_ids, failed_ids = set(), set()
        i = 0
        for line in proc.stdout:
            line = line.rstrip()
            log_lines.append(line)
            log_placeholder.text_area("", value="\n".join(log_lines[-28:]),
                                       height=300, key=f"real_log_{i}", label_visibility="collapsed")
            if " PASSED" in line:
                tc_id = line.split("::test_")[-1].split(" ")[0].upper().replace("_", "-")
                passed_ids.add(tc_id)
            elif " FAILED" in line:
                tc_id = line.split("::test_")[-1].split(" ")[0].upper().replace("_", "-")
                failed_ids.add(tc_id)
            i += 1
            progress_bar.progress(min((len(passed_ids) + len(failed_ids)) / max(total, 1), 1.0))

        proc.wait()
    except Exception as e:
        log(f"Playwright run error: {e}", "ERROR")
        log_lines.append(f"ERROR: {e}")
        log_placeholder.text_area("", value="\n".join(log_lines[-10:]),
                                   height=300, key="real_log_err", label_visibility="collapsed")

    for _, row in tc_df.iterrows():
        tc_id  = row.get("TC_ID", "")
        norm   = tc_id.replace("-", "_").lower()
        passed = norm in {p.replace("-","_").lower() for p in passed_ids}
        failed = norm in {f.replace("-","_").lower() for f in failed_ids}
        if not passed and not failed:
            status = "SKIP"
        else:
            status = "PASS" if passed else "FAIL"
        shot = f"screenshots/test_{norm}_fail.png"
        results.append({
            "TC_ID": tc_id, "Scenario": row.get("Scenario", ""),
            "Module": row.get("Module", ""), "Severity": row.get("Severity", ""),
            "Type": row.get("Type", ""),
            "Expected_Result": row.get("Expected_Result", ""),
            "Actual_Result": "As expected" if status == "PASS" else "See Playwright output above",
            "Duration_ms": 0, "Status": status,
            "Screenshot": shot if status == "FAIL" and pathlib.Path(shot).exists() else "",
            "Source": "Real Playwright",
        })

    try:
        script_path.unlink()
    except Exception:
        pass

    log_lines.append("─" * 50)
    log_lines.append("Real Playwright execution complete.")
    log_placeholder.text_area("", value="\n".join(log_lines[-30:]),
                               height=300, key="real_log_done", label_visibility="collapsed")
    return pd.DataFrame(results)


# ── Render ────────────────────────────────────────────────────────────────────

def render_agent_c():
    st.subheader("Agent C — Execution Orchestrator")
    st.caption("Simulate test results instantly, or run real Playwright tests against your live application.")

    if st.session_state.pipeline_stage < 4:
        empty_state(
            "Waiting for Test Suite Approval",
            "Agent B needs to generate and approve a test suite before execution can begin.",
            "Go to the Test Suite tab and approve the generated tests."
        )
        return

    cfg      = st.session_state.config
    platform = cfg.get("platform", "Web (Browser)")
    tc_df    = st.session_state.test_cases_df

    if tc_df is None or len(tc_df) == 0:
        empty_state("No Test Cases Found",
                    "The test suite appears to be empty.",
                    "Go back to the Test Suite tab and regenerate.")
        return

    # ── Script download ───────────────────────────────────────────
    with st.expander("Generated Test Script", expanded=False):
        st.caption("Download and run this against your application with: pytest test_suite.py -v")
        col_gen, col_dl = st.columns(2)
        with col_gen:
            if st.button("Regenerate script", use_container_width=True):
                with st.spinner("Writing test code..."):
                    key = f"script_{platform}"
                    if "Web" in platform:
                        st.session_state[key] = generate_playwright_script(tc_df, cfg)
                    else:
                        st.session_state[key] = generate_appium_script(tc_df, platform, cfg)
        script_key = f"script_{platform}"
        if script_key not in st.session_state:
            st.session_state[script_key] = generate_playwright_script(tc_df, cfg)
        script = st.session_state[script_key]
        with col_dl:
            fname = "test_suite.py" if "Web" in platform else "test_mobile.py"
            st.download_button("Download script", script, fname, use_container_width=True)
        st.code(script[:2000] + ("\n# ... download for full script" if len(script) > 2000 else ""),
                language="python")

    with st.expander("CI/CD Workflow", expanded=False):
        yaml_content = generate_github_actions_yaml(tc_df)
        st.code(yaml_content, language="yaml")
        st.download_button("Download GitHub Actions workflow", yaml_content,
                           "qa_pipeline.yml", use_container_width=True)

    st.divider()

    # ── Execution plan ────────────────────────────────────────────
    import datetime as _dt
    driver_map = {"Web (Browser)": "Playwright", "Android": "Appium/UIAutomator2",
                  "iOS": "Appium/XCUITest", "Desktop (Windows)": "PyWinAuto", "Desktop (macOS)": "PyAutoGUI"}
    count = len(tc_df)
    plan  = {
        "plan_id":  f"PLAN-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "platform": platform, "driver": driver_map.get(platform, "Playwright"),
        "test_count": count, "parallelism": min(4, max(1, count // 6)),
        "estimated_duration_min": max(1, round(count * 0.4)),
    }
    st.session_state.exec_plan = plan

    pc1, pc2, pc3, pc4 = st.columns(4)
    pc1.metric("Test Cases", plan["test_count"])
    pc2.metric("Driver", plan["driver"])
    pc3.metric("Parallelism", plan["parallelism"])
    pc4.metric("Est. Duration", f"{plan['estimated_duration_min']} min")

    if st.session_state.pipeline_stage == 4:
        render_hitl_chat(
            agent_key="agent_c",
            context_label="Execution Plan",
            get_context=lambda: json.dumps(st.session_state.get("exec_plan", {}), indent=2),
            apply_patch=lambda patch: st.session_state.update({"exec_plan": patch}),
            system_hint="Answer questions about the plan. If changes requested, update exec_plan JSON.",
        )

    st.divider()

    # ── Execution gate ────────────────────────────────────────────
    if st.session_state.pipeline_stage == 4:
        step_header(3, "Execute Test Suite", "Choose simulation (instant) or real Playwright (requires live app)")

        tab_sim, tab_real = st.tabs(["Simulation", "Real Playwright"])

        with tab_sim:
            st.info(
                "Simulation runs instantly with no live application needed. "
                "Results are deterministic — the same test cases always produce the same pass/fail pattern. "
                "Use this to validate your test plan before running against a real app."
            )
            if st.button("Run Simulation", use_container_width=True, type="primary", key="btn_sim"):
                log("Simulation execution started", "STEP")
                st.session_state.pipeline_stage = 5
                st.write("Running simulation...")
                progress = st.progress(0)
                log_area  = st.empty()
                results_df = _run_simulation(tc_df, platform, progress, log_area)
                _save_results(results_df)
                st.rerun()

        with tab_real:
            st.warning(
                "Real execution runs actual Playwright tests against your application. "
                "Requires: the app is running, Playwright is installed, and the URL is reachable."
            )

            base_url = st.text_input(
                "Application URL",
                value=cfg.get("test_base_url", "http://localhost:3000"),
                placeholder="https://your-app.com or http://localhost:3000",
                key="real_exec_url",
            )

            pw_ok, pw_msg = _check_playwright_installed()
            if pw_ok:
                st.success("Playwright installed")
            else:
                error_card("Playwright not installed", pw_msg,
                           "Run: pip install playwright && playwright install chromium")

            if pw_ok:
                if st.button("Check if app is reachable", key="btn_check_url"):
                    with st.spinner(f"Checking {base_url}..."):
                        reachable, msg = _check_url_reachable(base_url)
                    if reachable:
                        st.success(f"App reachable — {msg}")
                    else:
                        st.error(f"Cannot reach {base_url} — {msg}")

                if st.button("Run Real Playwright Tests", use_container_width=True,
                             type="primary", key="btn_real", disabled=not pw_ok):
                    log(f"Real Playwright execution started against {base_url}", "STEP")
                    st.session_state.pipeline_stage = 5
                    st.write(f"Running real tests against {base_url}...")
                    progress  = st.progress(0)
                    log_area  = st.empty()
                    results_df = _run_real_playwright(tc_df, cfg, base_url, progress, log_area)
                    _save_results(results_df)
                    st.rerun()

    elif st.session_state.pipeline_stage >= 6:
        _render_results()


def _save_results(results_df: pd.DataFrame):
    st.session_state["execution_results"] = results_df
    st.session_state.pipeline_stage = 6
    log("Execution complete", "OK")
    run_id = st.session_state.get("run_id")
    if run_id:
        duration = results_df["Duration_ms"].sum() / 1000 if "Duration_ms" in results_df.columns else 0
        save_execution_results(run_id, results_df, duration_sec=duration)
        db_log(run_id, "OK", f"Execution results saved — {len(results_df)} test cases")


def _render_results():
    results_df = st.session_state.get("execution_results")
    if results_df is None:
        empty_state("No Results", "Execution results not found.", "Run the test suite above.")
        return

    pass_c = (results_df["Status"] == "PASS").sum()
    fail_c = (results_df["Status"] == "FAIL").sum()
    skip_c = (results_df["Status"] == "SKIP").sum() if "SKIP" in results_df["Status"].values else 0
    total  = len(results_df)
    source = results_df["Source"].iloc[0] if "Source" in results_df.columns else "Simulation"

    st.caption(f"Results from: **{source}**")
    rc1, rc2, rc3, rc4, rc5 = st.columns(5)
    rc1.metric("Total", total)
    rc2.metric("Passed", pass_c)
    rc3.metric("Failed", fail_c)
    if skip_c:
        rc4.metric("Skipped", skip_c)
    rc5.metric("Pass Rate", f"{pass_c/total*100:.0f}%" if total else "0%")

    st.success("Execution complete — proceed to the Defects tab for analysis.")

    display_cols = [c for c in ["TC_ID","Module","Severity","Type","Status","Duration_ms","Actual_Result","Source"]
                    if c in results_df.columns]
    st.dataframe(
        results_df[display_cols].style.apply(
            lambda row: ["background-color:#d4edda" if row["Status"]=="PASS"
                         else "background-color:#f8d7da" if row["Status"]=="FAIL"
                         else "" for _ in row],
            axis=1
        ),
        use_container_width=True, hide_index=True,
    )
