"""
utils/hitl_chat.py — Human-in-the-Loop Chat Panel

A reusable chat widget that can be embedded above any approval gate.
The user can ask questions, flag gaps, or request additions; Claude
responds with knowledge of the current pipeline document and applies
any structural changes the user requests directly to session state.

Usage:
    from utils.hitl_chat import render_hitl_chat
    render_hitl_chat(
        agent_key="agent_a",
        context_label="Master Requirement Document",
        get_context=lambda: json.dumps(st.session_state.master_req, indent=2),
        apply_patch=lambda patch: st.session_state.update({"master_req": patch}),
        system_hint="You are reviewing a Master Requirement Document ...",
    )
"""

import streamlit as st
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.helpers import log


# ── System prompt template ────────────────────────────────────────

_BASE_SYSTEM = """You are a senior QA/BA consultant embedded inside the Req2Defect AI pipeline.
The user is reviewing the output of {agent_label} and may:
  • Ask questions about the document
  • Point out missing items, ambiguities, or errors
  • Request additions or changes (new requirements, test cases, defects, etc.)

{agent_hint}

CURRENT DOCUMENT (JSON):
{context}

RESPONSE RULES:
1. Answer questions clearly and concisely.
2. If the user requests a change or addition, apply it and return the FULL updated
   document as a JSON block fenced with ```json ... ```.
   — For requirements: add to the correct module, assign the next REQ-NNN id,
     infer priority and acceptance_criteria.
   — For test cases: append to the array, assign the next TC-NNN id.
   — For defects: append to the array, assign the next DEF-NNN id.
3. If you return an updated document, also write a short human-readable summary of
   exactly what you changed, AFTER the JSON block.
4. If no document change is needed, just reply normally — no JSON block.
5. Never truncate the document. Always return the complete structure."""


# ── Chat state helpers ────────────────────────────────────────────

def _chat_key(agent_key: str) -> str:
    return f"hitl_chat_{agent_key}"


def _get_history(agent_key: str) -> list[dict]:
    return st.session_state.get(_chat_key(agent_key), [])


def _add_message(agent_key: str, role: str, content: str):
    key = _chat_key(agent_key)
    if key not in st.session_state:
        st.session_state[key] = []
    st.session_state[key].append({"role": role, "content": content})


# ── LLM call (direct — bypasses mock_mode so HITL is always live) ─

def _hitl_llm(system_prompt: str, messages: list[dict], cfg: dict) -> str:
    """Call Claude directly for HITL chat. Falls back gracefully if no key."""
    api_key = cfg.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    model = cfg.get("claude_model", "claude-sonnet-4-6")

    if not api_key:
        return (
            "⚠️ No API key configured — HITL chat requires a live Claude connection. "
            "Enable **Mock OFF** and enter your Anthropic API key in the sidebar."
        )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text
    except ImportError:
        return "⚠️ `anthropic` package not installed. Run: `pip install anthropic`"
    except Exception as e:
        return f"⚠️ Claude API error: {e}"


# ── JSON patch extraction & application ──────────────────────────

def _extract_json_patch(reply: str):
    """
    Pull the first ```json ... ``` block from the reply.
    Returns parsed object or None if no block found.
    """
    import re
    match = re.search(r"```json\s*\n?(.*?)```", reply, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None


def _strip_json_block(reply: str) -> str:
    """Return the reply with the JSON block removed — just the human summary."""
    import re
    return re.sub(r"```json.*?```", "", reply, flags=re.DOTALL).strip()


# ── Main render function ──────────────────────────────────────────

def render_hitl_chat(
    agent_key: str,
    context_label: str,
    get_context,          # callable → str (current document as JSON string)
    apply_patch,          # callable(parsed_obj) → None  (updates session state)
    system_hint: str = "",
):
    """
    Render an inline chat panel for human-in-the-loop review.

    Parameters
    ----------
    agent_key      : unique key per agent, e.g. "agent_a"
    context_label  : human label for the document, e.g. "Master Requirement Document"
    get_context    : zero-arg callable that returns the current doc as a JSON string
    apply_patch    : one-arg callable that receives a parsed JSON object and writes
                     it back to session state
    system_hint    : agent-specific instruction appended to the base system prompt
    """
    agent_labels = {
        "agent_a": "Agent A — Requirement Analyst",
        "agent_b": "Agent B — QA Strategist",
        "agent_c": "Agent C — Execution Orchestrator",
        "agent_d": "Agent D — Defect Manager",
    }
    agent_label = agent_labels.get(agent_key, agent_key)

    with st.expander("💬 Ask Claude — Request changes or additions before approving", expanded=False):
        st.caption(
            f"Chat with Claude about the **{context_label}**. "
            "You can ask questions, flag missing items, or say things like "
            "*'Add a requirement for password complexity'* and Claude will update the document."
        )

        history = _get_history(agent_key)
        cfg = st.session_state.get("config", {})

        # Render existing messages
        for msg in history:
            with st.chat_message(msg["role"]):
                # If the assistant message contains a JSON patch, show the
                # summary only — the raw JSON would be noisy in the chat view
                if msg["role"] == "assistant":
                    display = _strip_json_block(msg["content"])
                    st.markdown(display if display else "_Document updated — see changes above._")
                else:
                    st.markdown(msg["content"])

        # Chat input
        user_input = st.chat_input(
            f"Ask about or request changes to the {context_label}…",
            key=f"hitl_input_{agent_key}",
        )

        if user_input:
            # Show user message immediately
            with st.chat_message("user"):
                st.markdown(user_input)
            _add_message(agent_key, "user", user_input)
            log(f"HITL [{agent_key}] user: {user_input[:80]}", "HITL")

            # Build system prompt with fresh context snapshot
            try:
                context_snapshot = get_context()
            except Exception:
                context_snapshot = "{}"

            system_prompt = _BASE_SYSTEM.format(
                agent_label=agent_label,
                agent_hint=system_hint,
                context=context_snapshot,
            )

            # Build messages list for API (exclude system; passed separately)
            api_messages = [
                {"role": m["role"], "content": m["content"]}
                for m in _get_history(agent_key)
            ]

            # Call Claude
            with st.chat_message("assistant"):
                with st.spinner("Claude is thinking…"):
                    reply = _hitl_llm(system_prompt, api_messages, cfg)

                # Check for a document patch
                patch = _extract_json_patch(reply)
                if patch is not None:
                    try:
                        apply_patch(patch)
                        log(f"HITL [{agent_key}] applied document patch", "HITL")
                        summary = _strip_json_block(reply)
                        st.markdown(summary if summary else "_Document updated._")
                        st.success("✅ Document updated — review the changes above, then approve.")
                    except Exception as e:
                        st.error(f"Could not apply patch: {e}")
                        st.markdown(reply)
                else:
                    st.markdown(reply)

            _add_message(agent_key, "assistant", reply)
            log(f"HITL [{agent_key}] assistant replied ({len(reply)} chars)", "HITL")
            st.rerun()

        # Clear chat button
        if history:
            if st.button("🗑 Clear chat history", key=f"hitl_clear_{agent_key}"):
                st.session_state[_chat_key(agent_key)] = []
                st.rerun()
