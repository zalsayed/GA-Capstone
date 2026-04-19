"""Agent A — Requirement Analyst
Ingests requirements from any source and produces a structured Master Requirement Document.
"""

import streamlit as st
import json
import io
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.helpers import log, llm_call, parse_llm_json, get_provider_label
from utils.hitl_chat import render_hitl_chat
from utils.ui_helpers import empty_state, error_card, step_header
from core.database import save_requirements, db_log

SYSTEM_PROMPT = """You are a Senior Business Analyst and Requirements Engineer with 15+ years of experience.

Your task: Transform raw requirement text into a precise, structured Master Requirement Document (MRD) in JSON.

RULES:
- Extract EVERY requirement, even if implied
- Infer priorities: must/shall/required = Critical or High, should/recommended = Medium, may/optional = Low
- Assign unique IDs to all modules (MOD-01, MOD-02...) and requirements (REQ-001, REQ-002...)
- Extract all API endpoints, even if only mentioned in passing
- List all non-functional requirements (performance, security, accessibility, compliance)
- Do NOT invent requirements not present in the source

OUTPUT: Respond ONLY with valid JSON matching this exact schema:
{
  "document_id": "REQ-<YYYY>-<NNN>",
  "generated_at": "<ISO 8601 timestamp>",
  "source_type": "<PDF|Text|Image|Swagger>",
  "product": "<product name>",
  "version": "<version if mentioned, else 1.0.0>",
  "modules": [
    {
      "id": "MOD-01",
      "name": "<module name>",
      "description": "<1-2 sentence description>",
      "requirements": [
        {
          "id": "REQ-001",
          "text": "<exact or paraphrased requirement>",
          "priority": "Critical|High|Medium|Low",
          "acceptance_criteria": "<measurable condition for completion>"
        }
      ]
    }
  ],
  "api_endpoints": [
    {"method": "GET|POST|PUT|DELETE|PATCH", "path": "/api/...", "description": "..."}
  ],
  "non_functional": [
    {"category": "Performance|Security|Accessibility|Compliance|Reliability", "requirement": "<NFR text>"}
  ]
}"""


def _parse_pdf(file_bytes: bytes) -> str:
    try:
        import fitz

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text = "\n".join(p.get_text() for p in doc)
        log(f"PDF parsed via PyMuPDF — {len(text)} chars", "OK")
        return text
    except ImportError:
        pass
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        log(f"PDF parsed via pdfplumber — {len(text)} chars", "OK")
        return text
    except ImportError:
        log("No PDF library found. Install: pip install pymupdf", "WARN")
        return "Mock PDF content: user authentication, product catalog, checkout module requirements."


def _parse_image_ocr(file_bytes: bytes) -> str:
    """
    Extract text from an image of requirements.
    Strategy (in order):
    1. Send directly to the configured vision-capable LLM (best quality)
    2. EasyOCR (if installed)
    3. Tesseract (if installed)
    4. Return empty string so the caller shows a clear error
    """
    import base64

    cfg = st.session_state.get("config", {})
    provider = cfg.get("provider", "")

    # ── Strategy 1: Vision LLM ────────────────────────────────────
    vision_prompt = (
        "This image contains software requirements, user stories, or feature descriptions. "
        "Extract ALL text exactly as written, preserving structure and formatting. "
        "Return only the extracted text, nothing else."
    )

    if "Claude" in provider:
        try:
            import anthropic

            api_key = cfg.get("anthropic_key", "") or os.environ.get(
                "ANTHROPIC_API_KEY", ""
            )
            model = cfg.get("claude_model", "claude-sonnet-4-6")
            if api_key:
                client = anthropic.Anthropic(api_key=api_key)
                b64 = base64.standard_b64encode(file_bytes).decode()
                message = client.messages.create(
                    model=model,
                    max_tokens=2000,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": b64,
                                    },
                                },
                                {"type": "text", "text": vision_prompt},
                            ],
                        }
                    ],
                )
                text = message.content[0].text.strip()
                log(f"Image OCR via Claude vision — {len(text)} chars", "OK")
                return text
        except Exception as e:
            log(f"Claude vision failed: {e}", "WARN")

    if "Gemini" in provider:
        try:
            import google.generativeai as genai

            api_key = cfg.get("gemini_key", "") or os.environ.get("GEMINI_API_KEY", "")
            model = cfg.get("gemini_model", "gemini-2.0-flash")
            if api_key:
                genai.configure(api_key=api_key)
                from PIL import Image as PILImage

                img = PILImage.open(io.BytesIO(file_bytes))
                m = genai.GenerativeModel(model_name=model)
                response = m.generate_content([vision_prompt, img])
                text = response.text.strip()
                log(f"Image OCR via Gemini vision — {len(text)} chars", "OK")
                return text
        except Exception as e:
            log(f"Gemini vision failed: {e}", "WARN")

    if "OpenRouter" in provider:
        try:
            import requests

            api_key = cfg.get("openrouter_key", "") or os.environ.get(
                "OPENROUTER_API_KEY", ""
            )
            b64 = base64.standard_b64encode(file_bytes).decode()
            if api_key:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/req2defect",
                    "X-Title": "Req2Defect Pipeline",
                }
                payload = {
                    "model": "google/gemini-2.0-flash-exp:free",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{b64}"
                                    },
                                },
                                {"type": "text", "text": vision_prompt},
                            ],
                        }
                    ],
                    "max_tokens": 2000,
                }
                r = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
                text = r.json()["choices"][0]["message"]["content"].strip()
                log(f"Image OCR via OpenRouter vision — {len(text)} chars", "OK")
                return text
        except Exception as e:
            log(f"OpenRouter vision failed: {e}", "WARN")

    # ── Strategy 2: EasyOCR ───────────────────────────────────────
    try:
        import easyocr
        import numpy as np
        from PIL import Image as PILImage

        img = PILImage.open(io.BytesIO(file_bytes))
        reader = easyocr.Reader(["en"], gpu=False)
        text = "\n".join(reader.readtext(np.array(img), detail=0))
        log(f"Image OCR via EasyOCR — {len(text)} chars", "OK")
        return text
    except ImportError:
        pass
    except Exception as e:
        log(f"EasyOCR failed: {e}", "WARN")

    # ── Strategy 3: Tesseract ─────────────────────────────────────
    try:
        import pytesseract
        from PIL import Image as PILImage

        text = pytesseract.image_to_string(PILImage.open(io.BytesIO(file_bytes)))
        log(f"Image OCR via Tesseract — {len(text)} chars", "OK")
        return text
    except ImportError:
        pass
    except Exception as e:
        log(f"Tesseract failed: {e}", "WARN")

    # ── All strategies failed ─────────────────────────────────────
    log("All OCR strategies failed", "ERROR")
    st.error(
        "Could not extract text from the image. Options:\n"
        "1. Use a vision-capable provider (Claude, Gemini, or OpenRouter) — best quality\n"
        "2. Install EasyOCR: `pip install easyocr`\n"
        "3. Install Tesseract: `brew install tesseract && pip install pytesseract`\n"
        "4. Copy the text manually and use Plain Text mode instead."
    )
    return ""


def _parse_swagger(content: str) -> str:
    try:
        import yaml

        try:
            spec = json.loads(content)
        except json.JSONDecodeError:
            spec = yaml.safe_load(content)
        info = spec.get("info", {})
        lines = [
            f"API: {info.get('title', '')} v{info.get('version', '')}",
            f"Description: {info.get('description', '')}",
            "",
            "Endpoints:",
        ]
        for path, methods in spec.get("paths", {}).items():
            for method, details in methods.items():
                if method in ("get", "post", "put", "delete", "patch"):
                    summary = details.get("summary", details.get("description", ""))
                    lines.append(f"  [{method.upper()}] {path} — {summary}")
        log(f"Swagger parsed — {len(spec.get('paths', {}))} paths found", "OK")
        return "\n".join(lines)
    except Exception as e:
        log(f"Swagger parse error: {e}", "WARN")
        return content


def run_agent_a(source_type: str, raw_content: str, file_bytes=None):
    log("Agent A started — Requirement Analysis", "STEP")
    st.session_state.pipeline_stage = 1

    with st.spinner("📄 Extracting content from source..."):
        if source_type == "pdf" and file_bytes:
            raw_text = _parse_pdf(file_bytes)
        elif source_type == "image" and file_bytes:
            raw_text = _parse_image_ocr(file_bytes)
        elif source_type == "swagger":
            raw_text = _parse_swagger(raw_content)
        else:
            raw_text = raw_content or ""
        log(f"Extracted {len(raw_text)} characters from source", "OK")

    if not raw_text.strip():
        st.warning("No input provided. Please paste requirements or upload a file.")
        st.session_state.pipeline_stage = 0
        return None

    with st.spinner("🧠 Claude is structuring requirements..."):
        llm_output = llm_call(SYSTEM_PROMPT, raw_text, "", max_tokens=4096)

    master_req = parse_llm_json(llm_output, None)
    if master_req is None:
        log("LLM output could not be parsed as JSON", "WARN")
        st.error(
            "Could not parse the LLM response as JSON. Check the Logs tab and try again."
        )
        st.session_state.pipeline_stage = 0
        return None

    # Validate and patch missing acceptance_criteria
    for mod in master_req.get("modules", []):
        for req in mod.get("requirements", []):
            if "acceptance_criteria" not in req:
                req["acceptance_criteria"] = "Verified by functional test case."

    # Normalise non_functional to list of dicts if it came back as plain strings
    nfrs = master_req.get("non_functional", [])
    if nfrs and isinstance(nfrs[0], str):
        master_req["non_functional"] = [
            {"category": "General", "requirement": nfr} for nfr in nfrs
        ]

    st.session_state.master_req = master_req
    log(
        f"Master Requirement Document stored — {sum(len(m.get('requirements',[])) for m in master_req.get('modules',[]))} requirements",
        "OK",
    )
    run_id = st.session_state.get("run_id")
    if run_id:
        save_requirements(run_id, master_req)
        db_log(
            run_id,
            "OK",
            f"Requirements saved — {sum(len(m.get('requirements',[])) for m in master_req.get('modules',[]))} reqs",
        )
    return master_req


def render_agent_a_results():
    st.subheader("🔍 Agent A — Requirement Analyst")
    st.caption(
        "Ingest requirements from any format. Claude structures them into a validated Master Requirement Document."
    )

    if st.session_state.pipeline_stage < 2:
        source_type = st.radio(
            "Source type",
            [
                "Plain Text",
                "PDF Document",
                "Image (OCR)",
                "Swagger / OpenAPI",
                "📋 Load Demo",
            ],
            horizontal=True,
        )
        raw_text = ""
        file_bytes = None

        if source_type == "Plain Text":
            raw_text = st.text_area(
                "Paste requirements",
                height=220,
                placeholder="Paste your BRD, user stories, or feature descriptions here...\n\nTip: Include as much detail as possible — acceptance criteria, edge cases, performance expectations.",
            )
        elif source_type == "PDF Document":
            uploaded = st.file_uploader("Upload PDF", type=["pdf"])
            if uploaded:
                file_bytes = uploaded.read()
                st.success(f"✅ Loaded: {uploaded.name} ({len(file_bytes)//1024} KB)")
        elif source_type == "Image (OCR)":
            uploaded = st.file_uploader(
                "Upload image of requirements", type=["png", "jpg", "jpeg"]
            )
            if uploaded:
                file_bytes = uploaded.read()
                st.image(uploaded, width=500, caption="Source image for OCR")
        elif source_type == "Swagger / OpenAPI":
            swagger_url = st.text_input(
                "Swagger URL (optional)",
                placeholder="https://petstore.swagger.io/v2/swagger.json",
            )
            raw_text = st.text_area("Or paste JSON/YAML spec", height=200)
            if swagger_url and not raw_text:
                try:
                    import urllib.request

                    with urllib.request.urlopen(swagger_url, timeout=8) as r:
                        raw_text = r.read().decode()
                    st.success("Spec fetched from URL.")
                except Exception as e:
                    st.error(f"Could not fetch: {e}")
        else:  # Load Demo
            # Pre-populate with the actual demo text so the LLM processes real content
            _demo_text = """Product: E-Commerce Checkout System v2.1.0

Module: User Authentication
- Users shall register with email and password (Critical)
- System shall lock accounts after 5 consecutive failed login attempts (High)
- Users may enable 2FA via TOTP authenticator app (Medium)
- Password reset emails should be delivered within 60 seconds (High)
- Session tokens shall expire after 24 hours of inactivity (High)

Module: Product Catalog
- System shall display products with images, price, and stock status (Critical)
- Search results must appear within 2 seconds for queries up to 100 characters (High)
- Users should filter by category, price range, and star rating (Medium)
- Out-of-stock products shall show a clear Unavailable badge (Medium)

Module: Checkout & Payment
- System shall support Visa, Mastercard, Amex, and PayPal (Critical)
- Cart contents shall persist across sessions for authenticated users (High)
- Payment processing shall complete within 5 seconds under normal load (High)
- System shall send order confirmation email upon successful payment (Medium)
- Users shall be able to apply a single coupon code per order (Low)

Module: Order Management
- Users shall view full order history with status and tracking info (High)
- Users may cancel an order within 1 hour of placement if unpacked (Medium)
- System shall update order status in real-time via webhook (High)

Non-Functional Requirements:
- System shall handle 1000 concurrent users without degradation (Performance)
- All API responses shall be returned in under 500ms at p95 (Performance)
- Passwords shall be hashed with bcrypt, cost factor >= 12 (Security)
- All data in transit shall use TLS 1.3 (Security)
- System uptime shall be >= 99.9% monthly SLA (Reliability)
- WCAG 2.1 AA accessibility compliance on all public pages (Accessibility)
- GDPR compliance: user data deletion within 30 days of request (Compliance)"""
            raw_text = _demo_text
            st.info(
                "Demo requirements loaded — will be processed by your configured LLM."
            )
            with st.expander("Preview demo text"):
                st.text(raw_text)

        src_map = {
            "Plain Text": "text",
            "PDF Document": "pdf",
            "Image (OCR)": "image",
            "Swagger / OpenAPI": "swagger",
            "📋 Load Demo": "text",
        }

        col1, col2 = st.columns([2, 1])
        with col1:
            cfg_check = st.session_state.get("config", {})
            has_key = any(
                [
                    cfg_check.get("anthropic_key"),
                    cfg_check.get("gemini_key"),
                    cfg_check.get("groq_key"),
                    cfg_check.get("openrouter_key"),
                    cfg_check.get("provider", "") == "Ollama (local)",
                ]
            )
            if not has_key:
                st.warning(
                    "No API key configured. Open Configuration Settings in the sidebar and add your key first."
                )
            if st.button(
                "▶ Run Agent A",
                use_container_width=True,
                type="primary",
                disabled=not has_key,
            ):
                result = run_agent_a(src_map[source_type], raw_text, file_bytes)
                if result is not None:
                    st.session_state.pipeline_stage = 2
                st.rerun()
        with col2:
            st.caption(f"Provider: {get_provider_label()}")

    if st.session_state.pipeline_stage >= 2:
        if not st.session_state.master_req:
            st.error(
                "Agent A ran but did not produce a document. "
                "This usually means the LLM returned an empty or unparseable response. "
                "Check:\n"
                "1. Is your API key entered in Configuration Settings?\n"
                "2. Is Mock mode toggled off?\n"
                "3. Check the Logs tab for details."
            )
            if st.button("🔄 Try again", type="primary"):
                st.session_state.pipeline_stage = 0
                st.session_state.master_req = None
                st.rerun()
            st.stop()

        req = st.session_state.master_req
        all_reqs = [
            r for m in req.get("modules", []) for r in m.get("requirements", [])
        ]
        nfrs = req.get("non_functional", [])

        _provider = st.session_state.get("config", {}).get("provider", "LLM")
        st.success(
            f"Master Requirement Document generated for **{req.get('product','?')}** "
            f"v{req.get('version','')} — via {_provider}"
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("📦 Modules", len(req.get("modules", [])))
        c2.metric("📋 Requirements", len(all_reqs))
        c3.metric("🔌 API Endpoints", len(req.get("api_endpoints", [])))
        c4.metric("⚙️ NFRs", len(nfrs))

        st.divider()

        priority_counts = {}
        for r in all_reqs:
            p = r.get("priority", "Unknown")
            priority_counts[p] = priority_counts.get(p, 0) + 1

        cols = st.columns(len(priority_counts))
        colors = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}
        for i, (pri, cnt) in enumerate(priority_counts.items()):
            cols[i].metric(f"{colors.get(pri,'⚪')} {pri}", cnt)

        st.divider()

        for mod in req.get("modules", []):
            with st.expander(
                f"**{mod['id']}** — {mod['name']} ({len(mod.get('requirements',[]))} requirements)"
            ):
                st.caption(mod.get("description", ""))
                for r in mod.get("requirements", []):
                    col_a, col_b = st.columns([1, 5])
                    col_a.markdown(f"`{r.get('priority','')}`")
                    col_b.markdown(f"**{r['id']}**: {r['text']}")
                    if r.get("acceptance_criteria"):
                        col_b.caption(f"✓ {r['acceptance_criteria']}")

        if req.get("api_endpoints"):
            st.write("**API Endpoints**")
            st.caption("Extracted from your input document.")
            import pandas as pd

            ep_df = pd.DataFrame(req["api_endpoints"])
            st.dataframe(ep_df, use_container_width=True, hide_index=True)

        if nfrs:
            st.write("**Non-Functional Requirements**")
            for nfr in nfrs:
                if isinstance(nfr, dict):
                    st.write(
                        f"- `{nfr.get('category','')}` {nfr.get('requirement','')}"
                    )
                else:
                    st.write(f"- {nfr}")

        with st.expander("🛠 Edit raw JSON"):
            edited = st.text_area(
                "JSON",
                value=json.dumps(req, indent=2),
                height=400,
                label_visibility="collapsed",
            )
            if st.button("💾 Save JSON edits"):
                try:
                    st.session_state.master_req = json.loads(edited)
                    st.success("Saved.")
                    log("User edited Master Requirement Document", "OK")
                except json.JSONDecodeError as e:
                    st.error(f"Invalid JSON: {e}")

        st.divider()
        render_hitl_chat(
            agent_key="agent_a",
            context_label="Master Requirement Document",
            get_context=lambda: json.dumps(st.session_state.master_req or {}, indent=2),
            apply_patch=lambda patch: st.session_state.update({"master_req": patch}),
            system_hint=(
                "Focus on requirements completeness. When adding requirements, "
                "place them in the correct module and assign the next REQ-NNN id. "
                "When adding modules, assign the next MOD-NN id. "
                "Preserve all existing api_endpoints and non_functional entries."
            ),
        )
        st.divider()
        st.write("**Human Approval Gate — Level 1**")
        st.caption(
            "Review the requirements above carefully. Approve only when all modules, priorities, and acceptance criteria are correct."
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button(
                "✅ Approve & proceed to Test Design",
                use_container_width=True,
                type="primary",
            ):
                log("HUMAN APPROVAL L1: Requirements approved by user", "APPROVED")
                st.session_state.pipeline_stage = 3
                st.rerun()
        with c2:
            if st.button("🔄 Re-run Agent A", use_container_width=True):
                st.session_state.pipeline_stage = 0
                st.session_state.master_req = None
                st.rerun()
