"""
utils/ui_helpers.py — Shared UI components for production-ready experience.
Empty states, error cards, loading messages, onboarding.
"""
import streamlit as st


def empty_state(title: str, body: str, action: str = ""):
    st.markdown(
        f"""
        <div style="
            text-align:center; padding:3rem 2rem;
            border:1px dashed #d0d7de; border-radius:8px;
            background:#fafafa; margin:1rem 0;
        ">
            <p style="font-size:1.1rem;font-weight:600;color:#24292f;margin-bottom:0.5rem;">{title}</p>
            <p style="color:#57606a;font-size:0.9rem;margin-bottom:{'1rem' if action else '0'};">{body}</p>
            {"<p style='font-size:0.85rem;color:#0969da;'>" + action + "</p>" if action else ""}
        </div>
        """,
        unsafe_allow_html=True,
    )


def error_card(message: str, detail: str = "", fix: str = ""):
    with st.container(border=True):
        st.error(f"**{message}**")
        if detail:
            st.caption(detail)
        if fix:
            st.info(f"**How to fix:** {fix}")


def success_card(message: str, detail: str = ""):
    with st.container(border=True):
        st.success(f"**{message}**")
        if detail:
            st.caption(detail)


def step_header(number: int, title: str, subtitle: str = ""):
    st.markdown(
        f"""<div style="display:flex;align-items:center;gap:12px;margin-bottom:0.5rem;">
            <div style="background:#0969da;color:white;width:28px;height:28px;border-radius:50%;
                display:flex;align-items:center;justify-content:center;font-size:13px;
                font-weight:600;flex-shrink:0;">{number}</div>
            <div>
                <p style="margin:0;font-weight:600;font-size:1rem;">{title}</p>
                {"<p style='margin:0;font-size:0.8rem;color:#57606a;'>" + subtitle + "</p>" if subtitle else ""}
            </div>
        </div>""",
        unsafe_allow_html=True,
    )


def show_onboarding():
    """First-run welcome screen shown when no run_id exists."""
    st.markdown(
        """
        <div style="text-align:center;padding:2rem 0 1rem;">
            <h2 style="font-size:1.8rem;font-weight:700;color:#24292f;">Welcome to Req2Defect</h2>
            <p style="color:#57606a;font-size:1rem;max-width:500px;margin:0 auto;">
                Transform raw requirements into a complete test suite and defect report — in minutes.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    for col, num, title, desc in [
        (c1, "1", "Requirements", "Paste or upload your BRD, user stories, or API spec"),
        (c2, "2", "Test Suite", "AI generates positive, negative, security & performance tests"),
        (c3, "3", "Execute", "Simulate or run real Playwright tests against your app"),
        (c4, "4", "Defects", "AI analyses failures and proposes actionable fixes"),
    ]:
        with col:
            st.markdown(
                f"""<div style="text-align:center;padding:1rem;border:1px solid #e1e4e8;
                    border-radius:8px;background:#fff;height:140px;">
                    <div style="background:#0969da;color:white;width:32px;height:32px;
                        border-radius:50%;display:flex;align-items:center;justify-content:center;
                        font-weight:700;margin:0 auto 8px;">{num}</div>
                    <p style="font-weight:600;margin:0 0 4px;">{title}</p>
                    <p style="font-size:0.78rem;color:#57606a;margin:0;">{desc}</p>
                </div>""",
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # Setup checker
    with st.expander("Setup checklist — complete before starting", expanded=True):
        cfg_check = st.session_state.get("config", {})
        sk = st.session_state

        has_key = any([
            sk.get("sk_anthropic"), sk.get("sk_gemini"),
            sk.get("sk_groq"), sk.get("sk_openrouter"),
            cfg_check.get("provider","") == "Ollama (local)",
        ])
        provider = cfg_check.get("provider", "Not selected")

        if has_key:
            st.success(f"API key configured — {provider}")
        else:
            st.error("No API key — open **Configuration Settings** in the sidebar and add your key")
            with st.expander("Quick setup — save key to .env file (permanent)"):
                key_input = st.text_input("Paste your API key here", type="password",
                                          placeholder="gsk_... or AIza... or sk-ant-...", key="onboard_key")
                prov = st.selectbox("Provider", ["Groq (free)", "Gemini", "OpenRouter (free)", "Claude (Anthropic)"],
                                    key="onboard_prov")
                if st.button("Save to .env and activate", type="primary") and key_input:
                    env_map = {
                        "Groq (free)":        ("GROQ_API_KEY",       "sk_groq"),
                        "Gemini":             ("GEMINI_API_KEY",     "sk_gemini"),
                        "OpenRouter (free)":  ("OPENROUTER_API_KEY", "sk_openrouter"),
                        "Claude (Anthropic)": ("ANTHROPIC_API_KEY",  "sk_anthropic"),
                    }
                    env_var, ss_key = env_map[prov]
                    import os
                    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
                    lines = []
                    if os.path.exists(env_path):
                        lines = [l for l in open(env_path).readlines()
                                 if not l.startswith(env_var) and not l.startswith("LLM_PROVIDER")]
                    lines += [f"{env_var}={key_input}\n", f"LLM_PROVIDER={prov}\n"]
                    open(env_path, "w").writelines(lines)
                    st.session_state[ss_key] = key_input
                    st.session_state.config = {**st.session_state.get("config", {}),
                                               "provider": prov,
                                               ss_key.replace("sk_", "") + "_key": key_input}
                    st.success(f"Saved! Key stored in .env and active now. Click New Run to start.")

        st.divider()
        st.markdown("**Ready to start?** Click **New Run** in the sidebar.")
