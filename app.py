"""app.py — Req2Defect"""

import streamlit as st
import pandas as pd
import json
import uuid
import datetime
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from core.config import get_config
from core.database import (
    init_db,
    create_run,
    update_run,
    get_run,
    list_runs,
    delete_run,
    load_requirements,
    load_test_cases,
    load_execution_results,
    load_defects,
    db_log,
    get_logs,
    get_token_summary,
)
from core.auth import require_auth, logout
from utils.ui_helpers import show_onboarding, empty_state

cfg = get_config()

st.set_page_config(
    page_title="Req2Defect",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    .stApp { background-color: #f8f9fa; }
    h1 { font-size: 2rem !important; margin-bottom: 0.5rem !important; }
    h2, h3 { font-size: 1.5rem !important; font-weight: 600 !important; }
    [data-testid="stSidebar"] {
        background-color: #ffffff;
        border-right: 1px solid #e1e4e8;
    }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 20px;
        font-weight: 500;
        border-radius: 4px 4px 0 0;
    }
    div[data-testid="metric-container"] {
        background: #ffffff;
        padding: 15px;
        border-radius: 6px;
        border: 1px solid #e1e4e8;
    }
</style>""",
    unsafe_allow_html=True,
)

if not require_auth():
    st.stop()

init_db()

for w in cfg.validate():
    st.warning(w)

DEFAULTS = {
    "run_id": None,
    "pipeline_stage": 0,
    "master_req": None,
    "test_cases_df": None,
    "exec_plan": None,
    "defects_df": None,
    "execution_results": None,
    "agent_logs": [],
    "config": {},
    "jira_pushed": False,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

for _k in ["sk_anthropic", "sk_gemini", "sk_groq", "sk_openrouter"]:
    if _k not in st.session_state:
        st.session_state[_k] = ""

if not st.session_state["sk_anthropic"] and cfg.anthropic_api_key:
    st.session_state["sk_anthropic"] = cfg.anthropic_api_key
if not st.session_state["sk_gemini"] and cfg.gemini_api_key:
    st.session_state["sk_gemini"] = cfg.gemini_api_key
if not st.session_state["sk_groq"] and cfg.groq_api_key:
    st.session_state["sk_groq"] = cfg.groq_api_key
if not st.session_state["sk_openrouter"] and cfg.openrouter_api_key:
    st.session_state["sk_openrouter"] = cfg.openrouter_api_key


with st.sidebar:
    st.title("Req2Defect")
    st.caption("Multi-Agent AI QA Pipeline")

    if cfg.auth_enabled:
        if st.button("Logout", use_container_width=True):
            logout()

    st.divider()

    c1, c2 = st.columns(2)
    with c1:
        if st.button("New Run", use_container_width=True, type="primary"):
            for k, v in DEFAULTS.items():
                st.session_state[k] = v
            run_id = f"RUN-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
            st.session_state.run_id = run_id
            active_cfg = st.session_state.get("config", {})
            create_run(
                run_id,
                active_cfg.get("provider", ""),
                active_cfg.get("model", ""),
                active_cfg.get("platform", "Web"),
                False,
            )
            db_log(run_id, "STEP", f"Run created: {run_id}")
            st.rerun()
    with c2:
        if st.button("Reset", use_container_width=True):
            for k, v in DEFAULTS.items():
                st.session_state[k] = v
            st.rerun()

    with st.expander("Configuration Settings", expanded=False):
        st.subheader("LLM Provider")
        provider_options = [
            "Claude (Anthropic)",
            "Gemini",
            "Groq (free)",
            "OpenRouter (free)",
            "Ollama (local)",
        ]
        provider = st.radio("Provider", provider_options, label_visibility="collapsed")

        claude_model = cfg.claude_model
        gemini_model = "gemini-2.0-flash"
        groq_model = cfg.groq_model
        openrouter_model = cfg.openrouter_model
        ollama_url = cfg.ollama_url
        ollama_model = cfg.ollama_model

        if provider == "Claude (Anthropic)":
            st.session_state["sk_anthropic"] = st.text_input(
                "API Key",
                type="password",
                placeholder="sk-ant-...",
                value=st.session_state["sk_anthropic"],
                key="wi_anthropic",
            )
            claude_model = st.selectbox(
                "Model",
                ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"],
            )

        elif provider == "Gemini":
            st.session_state["sk_gemini"] = st.text_input(
                "API Key",
                type="password",
                placeholder="AIza...",
                value=st.session_state["sk_gemini"],
                key="wi_gemini",
            )
            gemini_model = st.selectbox(
                "Model", ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
            )

        elif provider == "Groq (free)":
            st.caption("Free — 14,400 req/day — console.groq.com")
            st.session_state["sk_groq"] = st.text_input(
                "API Key",
                type="password",
                placeholder="gsk_...",
                value=st.session_state["sk_groq"],
                key="wi_groq",
            )
            groq_model = st.selectbox(
                "Model",
                [
                    "llama-3.3-70b-versatile",
                    "llama-3.1-8b-instant",
                    "mixtral-8x7b-32768",
                    "gemma2-9b-it",
                ],
            )

        elif provider == "OpenRouter (free)":
            st.caption("Free models available — openrouter.ai")
            st.session_state["sk_openrouter"] = st.text_input(
                "API Key",
                type="password",
                placeholder="sk-or-...",
                value=st.session_state["sk_openrouter"],
                key="wi_openrouter",
            )
            openrouter_model = st.selectbox(
                "Model",
                [
                    "meta-llama/llama-3.3-70b-instruct:free",
                    "meta-llama/llama-3.1-8b-instruct:free",
                    "mistralai/mistral-7b-instruct:free",
                    "google/gemma-3-27b-it:free",
                    "qwen/qwen-2.5-72b-instruct:free",
                    "deepseek/deepseek-chat:free",
                ],
            )

        elif provider == "Ollama (local)":
            st.caption("Local — no internet needed — ollama.com")
            ollama_url = st.text_input("Ollama URL", value=cfg.ollama_url)
            ollama_model = st.text_input(
                "Model", value=cfg.ollama_model, placeholder="llama3, mistral ..."
            )

        st.divider()
        st.subheader("Target Platform")
        platform = st.selectbox(
            "Platform",
            ["Web (Browser)", "Android", "iOS", "Desktop (Windows)", "Desktop (macOS)"],
            label_visibility="collapsed",
        )
        browser = st.selectbox(
            "Browser", ["Chromium", "Firefox", "WebKit"], label_visibility="collapsed"
        )
        test_base_url = st.text_input("App URL", value=cfg.test_base_url)

    st.session_state.config = {
        "provider": provider,
        "anthropic_key": st.session_state["sk_anthropic"],
        "claude_model": claude_model,
        "gemini_key": st.session_state["sk_gemini"],
        "gemini_model": gemini_model,
        "groq_key": st.session_state["sk_groq"],
        "groq_model": groq_model,
        "openrouter_key": st.session_state["sk_openrouter"],
        "openrouter_model": openrouter_model,
        "ollama_url": ollama_url,
        "ollama_model": ollama_model,
        "platform": platform,
        "browser": browser,
        "test_base_url": test_base_url,
        "model": claude_model,
    }

    _active_key = {
        "Claude (Anthropic)": st.session_state["sk_anthropic"],
        "Gemini": st.session_state["sk_gemini"],
        "Groq (free)": st.session_state["sk_groq"],
        "OpenRouter (free)": st.session_state["sk_openrouter"],
        "Ollama (local)": "local",
    }.get(provider, "")

    if _active_key:
        st.success(f"Ready — {provider}")
    else:
        st.error(f"No API key — open Configuration Settings and paste your key")

    if st.session_state.run_id:
        usage = get_token_summary(st.session_state.run_id)
        if usage["calls"] > 0:
            st.divider()
            st.caption(
                f"Tokens: {usage['input']:,} in / {usage['output']:,} out / {usage['calls']} calls"
            )

    if cfg.jira_url:
        st.divider()
        st.markdown("### Integration")
        st.caption(f"Project: {cfg.jira_project_key}")


hc1, hc2 = st.columns([4, 2])
with hc1:
    st.title("Req2Defect")
    st.caption("Automated QA Pipeline")
with hc2:
    if st.session_state.run_id:
        st.success(f"Active run: `{st.session_state.run_id}`")

if not st.session_state.run_id:
    show_onboarding()
    st.stop()


stage = st.session_state.pipeline_stage
st.markdown("### Pipeline Status")
cols = st.columns(4)
labels = ["Requirements", "Test Design", "Execution", "Defect Analysis"]

for i, label in enumerate(labels):
    threshold = (i + 1) * 2
    status = (
        "Active"
        if stage == threshold - 1
        else ("Done" if stage >= threshold else "Pending")
    )
    with cols[i]:
        st.markdown(
            f"""<div style="text-align:center;padding:10px;border-radius:4px;
            border:1px solid {'#2e7d32' if stage>=threshold else '#1976d2' if stage==threshold-1 else '#cfd8dc'};">
            <div style="font-size:1rem;font-weight:bold;">{label}</div>
            <div style="font-size:0.7rem;">{status}</div></div>""",
            unsafe_allow_html=True,
        )

st.divider()


tabs = st.tabs(
    [
        "Requirements",
        "Test Suite",
        "Execution",
        "Defects",
        "Analytics",
        "History",
        "Logs",
    ]
)

with tabs[0]:
    from agents.agent_a import render_agent_a_results

    render_agent_a_results()

with tabs[1]:
    from agents.agent_b import render_agent_b

    render_agent_b()

with tabs[2]:
    from agents.agent_c import render_agent_c

    render_agent_c()

with tabs[3]:
    from agents.agent_d import render_agent_d

    render_agent_d()

with tabs[4]:
    with st.container(border=True):
        st.subheader("Analytics")
        results_df = st.session_state.execution_results
        defects_df = st.session_state.defects_df
        test_cases = st.session_state.test_cases_df

        if test_cases is None and results_df is None:
            st.info("Run the pipeline to see analytics.")
        else:
            if test_cases is not None:
                st.markdown("**Test Suite Composition**")
                tc1, tc2, tc3 = st.columns(3)
                for col, fld, title in [
                    (tc1, "Severity", "By Severity"),
                    (tc2, "Type", "By Type"),
                    (tc3, "Module", "By Module"),
                ]:
                    with col:
                        st.caption(title)
                        if fld in test_cases.columns:
                            for k, v in test_cases[fld].value_counts().items():
                                st.write(
                                    f"`{str(k):<15}` {'█'*max(1,int(v/len(test_cases)*18))} {v}"
                                )
            if results_df is not None:
                st.divider()
                st.markdown("**Execution Results**")
                total = len(results_df)
                pass_c = (results_df["Status"] == "PASS").sum()
                fail_c = total - pass_c
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Total", total)
                m2.metric("Passed", pass_c)
                m3.metric("Failed", fail_c)
                m4.metric("Pass Rate", f"{pass_c/total*100:.1f}%" if total else "0%")

with tabs[5]:
    st.subheader("Run History")
    runs = list_runs()
    if not runs:
        st.info("No runs yet.")
    else:
        hist_df = pd.DataFrame(runs)
        st.dataframe(hist_df, use_container_width=True)
        selected = st.selectbox("Select run to load", [r["run_id"] for r in runs])
        if st.button("Load Selection"):
            st.rerun()

with tabs[6]:
    st.subheader("System Logs")
    logs = st.session_state.agent_logs
    if not logs:
        st.caption("No events yet.")
    else:
        st.text_area("", value="\n".join(logs), height=400)
