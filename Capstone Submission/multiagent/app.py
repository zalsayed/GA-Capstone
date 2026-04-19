import streamlit as st
import sys
import os
import tempfile
import threading
import queue
import time
import csv
import io
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

st.set_page_config(
    page_title="QA Auditor",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
  .block-container { max-width: 900px; padding-top: 2rem; }
  h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 0.2rem; }
  h3 { font-size: 1rem; font-weight: 600; margin-top: 1.5rem; }
  .stAlert { font-size: 0.85rem; }
  div[data-testid="stMetricValue"] { font-size: 1.8rem; }
  .stDataFrame { font-size: 0.82rem; }
  footer { visibility: hidden; }
</style>
""",
    unsafe_allow_html=True,
)


def _init_state():
    defaults = {
        "running": False,
        "results": None,
        "log_lines": [],
        "error": "",
        "reviewing": False,
        "review_index": 0,
        "reviewed": {},
        "added_issues": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()


def _parse_results_csv(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    return list(reader)


def _run_audit(config: dict, log_q: queue.Queue, result_q: queue.Queue):
    """Run the auditor in a background thread, streaming log lines."""
    try:
        import importlib.util

        def _force_load(name):
            for folder in [str(_HERE)]:
                path = os.path.join(folder, f"{name}.py")
                if os.path.exists(path):
                    spec = importlib.util.spec_from_file_location(name, path)
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[name] = mod
                    spec.loader.exec_module(mod)
                    return mod
            raise FileNotFoundError(f"{name}.py not found")

        _force_load("scraper")
        _force_load("ai")
        _force_load("rules")
        _force_load("output")
        _force_load("screenshot")

        import scraper as S
        from pipeline import run_pipeline
        from cache import AuditCache

        import builtins

        original_print = builtins.print

        def _capture_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            log_q.put(msg)
            original_print(*args, **kwargs)

        builtins.print = _capture_print

        jobs = []
        mode = config.get("mode", "full")
        gemini_key = config.get("gemini_key", "")
        groq_key = config.get("groq_key", "")
        openrouter_key = config.get("openrouter_key", "")
        entity = config.get("entity", "")

        if config.get("html_path"):
            _reports_dir = str(_HERE / "reports")
            os.makedirs(_reports_dir, exist_ok=True)
            raw = S.extract_services_from_html(
                config["html_path"], os.path.join(_reports_dir, "services.csv")
            )
            for s in raw:
                jobs.append(
                    dict(
                        psid=s["psid"],
                        name=s["name"],
                        url=s["url"],
                        is_eservice=s.get("is_eservice", False),
                    )
                )

        elif config.get("psids"):
            for pid in config["psids"]:
                jobs.append(
                    dict(
                        psid=pid,
                        name="",
                        url=S.BASE_URL.format(lang="en", psid=pid),
                        is_eservice=False,
                    )
                )

        elif config.get("esids"):
            for eid in config["esids"]:
                jobs.append(
                    dict(
                        psid=eid,
                        name="",
                        url=S.ESERVICE_BASE_URL.format(lang="en", esid=eid),
                        is_eservice=True,
                    )
                )

        elif config.get("csv_path"):
            raw = S.load_services_csv(config["csv_path"])
            for s in raw:
                jobs.append(
                    dict(
                        psid=s["psid"],
                        name=s["name"],
                        url=s["url"],
                        is_eservice=s.get("is_eservice", False),
                    )
                )

        if not jobs:
            result_q.put({"error": "No services found."})
            return

        log_q.put(f"Found {len(jobs)} service(s) to audit.")

        drive_svc = None
        upload_fn = None
        drive_folder = config.get("drive_folder_id", "")
        drive_key_path = config.get("drive_key_path", "")

        if drive_key_path and drive_folder:
            try:
                import drive as D

                drive_svc = D.init(drive_key_path)
                upload_fn = D.upload
                log_q.put("  Google Drive connected")
            except Exception as e:
                log_q.put(f"  Drive connection failed: {e}")

        state = run_pipeline(
            {
                "pending_scrape": jobs,
                "audit_mode": mode,
                "gemini_key": gemini_key,
                "groq_key": groq_key,
                "openrouter_key": openrouter_key,
                "take_screenshots": config.get("take_screenshots", False),
                "screenshots_dir": config.get("screenshots_dir", "screenshots"),
                "drive_service": drive_svc,
                "drive_folder": drive_folder,
                "upload_fn": upload_fn,
                "entity_name": entity,
                "max_workers": config.get("workers", 2),
                "run_reviewer": False,
            }
        )

        builtins.print = original_print

        all_issues = [
            i for r in state.get("completed", []) for i in r.get("issues", [])
        ]
        result_q.put(
            {
                "issues": all_issues,
                "completed": len(state.get("completed", [])),
                "failed": len(state.get("failed", [])),
                "total": len(all_issues),
            }
        )

    except Exception as e:
        import traceback

        log_q.put(f"ERROR: {e}")
        log_q.put(traceback.format_exc())
        result_q.put({"error": str(e)})
    finally:
        try:
            import builtins

            builtins.print = original_print
        except Exception:
            pass


def _apply_review(issues: list, reviewed: dict, added: list) -> list:
    result = []
    for iss in issues:
        idx = str(iss.get("id", ""))
        rev = reviewed.get(idx, {})
        if rev.get("status") == "rejected":
            continue
        out = dict(iss)
        if rev.get("status") == "edited":
            out["issue_description"] = rev.get(
                "description", iss.get("issue_description", "")
            )
            out["proposed_solution"] = rev.get(
                "solution", iss.get("proposed_solution", "")
            )
        result.append(out)
    for i, extra in enumerate(added, start=len(result) + 1):
        extra = dict(extra)
        extra["id"] = i
        result.append(extra)
    return result


def _save_corrections_to_gt(
    reviewed: dict, issues: list, added: list, entity: str
) -> str:
    import csv as _csv, os as _os

    gt_path = "ground_truth.csv"
    cols = [
        "psid",
        "entity",
        "service",
        "section",
        "language",
        "issue_type",
        "issue_summary",
        "is_valid",
        "severity",
        "found_by_rules",
        "found_by_ai",
        "found_by_hybrid",
        "source_file",
        "notes",
    ]
    rows = []
    iss_by_id = {str(i.get("id", "")): i for i in issues}
    for idx, rev in reviewed.items():
        iss = iss_by_id.get(idx, {})
        psid = ""
        page = iss.get("service", "") or ""
        import re as _re

        m = _re.search(r"[?&]psID=(\d+)", page, _re.IGNORECASE)
        if m:
            psid = m.group(1)
        rows.append(
            {
                "psid": psid,
                "entity": entity,
                "service": iss.get("service", ""),
                "section": iss.get("section", ""),
                "language": iss.get("language", ""),
                "issue_type": iss.get("rule_id", "ai_detected"),
                "issue_summary": (
                    rev.get("description") or iss.get("issue_description", "")
                )[:80],
                "is_valid": 0 if rev.get("status") == "rejected" else 1,
                "severity": rev.get("severity", 2),
                "found_by_rules": 1 if iss.get("rule_id") else 0,
                "found_by_ai": 0 if iss.get("rule_id") else 1,
                "found_by_hybrid": 1,
                "source_file": "streamlit_review",
                "notes": rev.get("notes", ""),
            }
        )
    for extra in added:
        rows.append(
            {
                "psid": "",
                "entity": entity,
                "service": extra.get("service", ""),
                "section": extra.get("section", ""),
                "language": extra.get("language", ""),
                "issue_type": "manually_added",
                "issue_summary": extra.get("issue_description", "")[:80],
                "is_valid": 1,
                "severity": extra.get("severity", 2),
                "found_by_rules": 0,
                "found_by_ai": 0,
                "found_by_hybrid": 0,
                "source_file": "streamlit_hitl",
                "notes": "Added by reviewer",
            }
        )
    if not rows:
        return ""
    exists = _os.path.exists(gt_path)
    with open(gt_path, "a", newline="", encoding="utf-8-sig") as f:
        w = _csv.DictWriter(f, fieldnames=cols)
        if not exists:
            w.writeheader()
        w.writerows(rows)
    return gt_path


def _render_review(issues: list, entity: str):
    reviewed = st.session_state.reviewed
    added = st.session_state.added_issues
    idx = st.session_state.review_index
    total = len(issues)

    accepted = sum(1 for v in reviewed.values() if v["status"] == "accepted")
    rejected = sum(1 for v in reviewed.values() if v["status"] == "rejected")
    edited = sum(1 for v in reviewed.values() if v["status"] == "edited")
    remaining = total - len(reviewed)

    st.markdown("**Review issues**")
    p1, p2, p3, p4, p5 = st.columns(5)
    p1.metric("Total", total)
    p2.metric("Accepted", accepted)
    p3.metric("Edited", edited)
    p4.metric("Rejected", rejected)
    p5.metric("Remaining", remaining)

    st.progress(len(reviewed) / total if total else 1.0)

    if idx < total:
        iss = issues[idx]
        iss_id = str(iss.get("id", ""))
        rev = reviewed.get(iss_id, {})

        st.markdown(
            f"**Issue {idx + 1} of {total}** — {iss.get('section','')} · {iss.get('language','')} · {iss.get('rule_id','AI')}"
        )
        st.caption(f"Placement: {iss.get('issue_placement','')}")

        new_desc = st.text_area(
            "Description",
            value=rev.get("description", iss.get("issue_description", "")),
            height=80,
            key=f"desc_{iss_id}",
        )
        new_sol = st.text_area(
            "Solution",
            value=rev.get("solution", iss.get("proposed_solution", "")),
            height=80,
            key=f"sol_{iss_id}",
        )
        sev = st.select_slider(
            "Severity",
            options=[1, 2, 3],
            value=rev.get("severity", 2),
            format_func=lambda x: {1: "Minor", 2: "Moderate", 3: "Critical"}[x],
            key=f"sev_{iss_id}",
        )
        notes = st.text_input(
            "Notes (optional)", value=rev.get("notes", ""), key=f"notes_{iss_id}"
        )

        c1, c2, c3, c4 = st.columns(4)

        if c1.button("Accept", key=f"acc_{iss_id}"):
            reviewed[iss_id] = {
                "status": "accepted",
                "severity": sev,
                "notes": notes,
                "description": new_desc,
                "solution": new_sol,
            }
            st.session_state.review_index = idx + 1
            st.rerun()

        if c2.button("Accept with edits", key=f"edit_{iss_id}"):
            reviewed[iss_id] = {
                "status": "edited",
                "severity": sev,
                "notes": notes,
                "description": new_desc,
                "solution": new_sol,
            }
            st.session_state.review_index = idx + 1
            st.rerun()

        if c3.button("Reject (false positive)", key=f"rej_{iss_id}"):
            reviewed[iss_id] = {
                "status": "rejected",
                "severity": sev,
                "notes": notes,
                "description": new_desc,
                "solution": new_sol,
            }
            st.session_state.review_index = idx + 1
            st.rerun()

        if c4.button("Skip", key=f"skip_{iss_id}"):
            st.session_state.review_index = idx + 1
            st.rerun()

    st.markdown("---")
    st.markdown("**Add a missed issue**")
    with st.expander("Add issue the tool missed"):
        a_section = st.selectbox(
            "Section",
            [
                "Service Name",
                "Service Description",
                "Required Attachments",
                "Legal Regulations",
                "Fees",
                "Process Time",
                "Service Provider",
                "Service Processes",
                "Service Conditions",
                "Formatting",
                "Wrong Information",
                "Incomplete Process",
                "User Clarity",
            ],
            key="add_section",
        )
        a_lang = st.selectbox("Language", ["EN", "AR", "Both"], key="add_lang")
        a_place = st.text_input("Placement (quote from page)", key="add_place")
        a_desc = st.text_area("Description", height=60, key="add_desc")
        a_sol = st.text_area("Solution", height=60, key="add_sol")
        a_sev = st.select_slider(
            "Severity",
            options=[1, 2, 3],
            format_func=lambda x: {1: "Minor", 2: "Moderate", 3: "Critical"}[x],
            key="add_sev",
        )
        if st.button("Add issue", key="add_btn"):
            if a_desc.strip():
                added.append(
                    {
                        "section": a_section,
                        "language": a_lang,
                        "issue_placement": a_place,
                        "issue_description": a_desc,
                        "proposed_solution": a_sol,
                        "severity": a_sev,
                        "rule_id": "HUMAN",
                    }
                )
                st.success("Issue added.")
                st.rerun()

    st.markdown("---")
    fin1, fin2 = st.columns(2)

    with fin1:
        if st.button("Finalize & export", type="primary"):
            final = _apply_review(issues, reviewed, added)
            csv_data = _issues_to_csv(final)
            gt_path = _save_corrections_to_gt(reviewed, issues, added, entity)
            st.session_state.reviewing = False
            st.session_state.results["_final_csv"] = csv_data
            if gt_path:
                st.success(f"Corrections saved to {gt_path} for capstone evaluation.")
            st.rerun()

    with fin2:
        if st.button("Exit review without saving"):
            st.session_state.reviewing = False
            st.rerun()

    if st.session_state.results.get("_final_csv"):
        st.download_button(
            label="Download reviewed CSV",
            data=st.session_state.results["_final_csv"].encode("utf-8-sig"),
            file_name=f"audit_reviewed_{entity.replace(' ','_') or 'results'}.csv",
            mime="text/csv",
        )


def _issues_to_csv(issues: list) -> str:
    if not issues:
        return ""
    cols = [
        "#",
        "Entity",
        "Service",
        "Section",
        "Language",
        "Placement",
        "Description",
        "Solution",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    for i, iss in enumerate(issues, 1):
        writer.writerow(
            {
                "#": i,
                "Entity": iss.get("entity", ""),
                "Service": iss.get("service", ""),
                "Section": iss.get("section", ""),
                "Language": iss.get("language", ""),
                "Placement": iss.get("issue_placement", ""),
                "Description": iss.get("issue_description", ""),
                "Solution": iss.get("proposed_solution", ""),
            }
        )
    return buf.getvalue()


st.title("Bahrain.bh QA Auditor")
st.caption("Bilingual content quality checker for government service pages.")

st.markdown("---")

st.markdown("### Configuration")

cfg_col1, cfg_col2, cfg_col3 = st.columns(3)

with cfg_col1:
    entity = st.text_input(
        "Entity name",
        placeholder="e.g. National Health Regulatory Authority",
    )
    mode = st.selectbox(
        "Audit mode",
        options=["full", "rules", "ai"],
        format_func=lambda x: {
            "full": "Full — rules + AI (recommended)",
            "rules": "Rules only — instant, no API calls",
            "ai": "AI only — LLM checks only",
        }[x],
    )
    workers = st.slider("Parallel workers", min_value=1, max_value=6, value=2)

with cfg_col2:
    if mode != "rules":
        st.markdown("**API providers**")
        use_gemini = st.checkbox("Gemini", value=True)
        use_groq = st.checkbox("Groq", value=False)
        use_openrouter = st.checkbox("OpenRouter", value=False)
    else:
        use_gemini = use_groq = use_openrouter = False
        st.caption("Rules-only mode — no API keys needed.")

with cfg_col3:
    st.markdown("**Screenshots & Drive**")
    take_screenshots = st.checkbox("Take screenshots", value=False)
    screenshots_dir = "screenshots"
    if take_screenshots:
        screenshots_dir = st.text_input(
            "Screenshots folder",
            value="screenshots",
            label_visibility="collapsed",
            placeholder="screenshots",
        )
    use_drive = st.checkbox(
        "Upload to Google Drive", value=False, disabled=not take_screenshots
    )
    drive_key_path = ""
    drive_folder_id = ""
    if use_drive and take_screenshots:
        uploaded_drive_key = st.file_uploader(
            "client_secret.json",
            type=["json"],
            label_visibility="collapsed",
        )
        if uploaded_drive_key:
            tmp_key = tempfile.NamedTemporaryFile(
                delete=False, suffix=".json", mode="wb"
            )
            tmp_key.write(uploaded_drive_key.read())
            tmp_key.flush()
            drive_key_path = tmp_key.name
            st.success("Credentials loaded")
        drive_folder_id = st.text_input(
            "Drive folder ID",
            placeholder="1sefehErPbq3V0Q5...",
            label_visibility="collapsed",
            help="From the Drive folder URL: .../folders/THIS_PART",
        )

gemini_key = groq_key = openrouter_key = ""

if mode != "rules":
    key_cols = [
        p
        for p in ["Gemini", "Groq", "OpenRouter"]
        if {"Gemini": use_gemini, "Groq": use_groq, "OpenRouter": use_openrouter}[p]
    ]
    if key_cols:
        st.markdown("**API keys**")
        key_inputs = st.columns(len(key_cols))
        for i, provider in enumerate(key_cols):
            with key_inputs[i]:
                if provider == "Gemini":
                    gemini_key = st.text_input(
                        "Gemini key",
                        type="password",
                        placeholder="AIza...",
                        key="gemini_key_input",
                    )
                elif provider == "Groq":
                    groq_key = st.text_input(
                        "Groq key",
                        type="password",
                        placeholder="gsk_...",
                        key="groq_key_input",
                    )
                elif provider == "OpenRouter":
                    openrouter_key = st.text_input(
                        "OpenRouter key",
                        type="password",
                        placeholder="sk-or-...",
                        key="openrouter_key_input",
                    )
        provider_order = " → ".join(key_cols)
        st.caption(f"Provider chain: {provider_order}")

st.markdown("### Input")

input_tab, psid_tab, esid_tab, csv_tab = st.tabs(
    ["Upload HTML page", "Enter PSIDs", "Enter ESIDs", "Upload services CSV"]
)

html_path = None
psids = []
esids = []
csv_path = None

with input_tab:
    uploaded_html = st.file_uploader("Upload entity HTML page", type=["html", "htm"])
    if uploaded_html:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
        tmp.write(uploaded_html.read())
        tmp.flush()
        html_path = tmp.name
        st.success(f"Loaded: {uploaded_html.name}")

with psid_tab:
    psid_input = st.text_area(
        "Service IDs (one per line or space-separated)",
        placeholder="1634\n1635\n1636",
        height=100,
    )
    if psid_input.strip():
        psids = [p.strip() for p in psid_input.replace(",", " ").split() if p.strip()]
        st.caption(f"{len(psids)} PSID(s) entered")

with esid_tab:
    esid_input = st.text_area(
        "eService IDs (one per line or space-separated)",
        placeholder="230\n456",
        height=100,
    )
    if esid_input.strip():
        esids = [e.strip() for e in esid_input.replace(",", " ").split() if e.strip()]
        st.caption(f"{len(esids)} ESID(s) entered")

with csv_tab:
    st.caption(
        "Upload a services CSV (same format as reports/services.csv) with columns: "
        "PSID, Service Name, URL, Is_eService"
    )
    uploaded_csv = st.file_uploader("Upload services CSV", type=["csv"])
    if uploaded_csv:
        tmp_csv = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        tmp_csv.write(uploaded_csv.read())
        tmp_csv.flush()
        csv_path = tmp_csv.name
        try:
            import csv as _csv

            with open(csv_path, encoding="utf-8-sig") as f:
                rows = list(_csv.DictReader(f))
            st.success(f"Loaded: {uploaded_csv.name} — {len(rows)} service(s)")
        except Exception:
            st.success(f"Loaded: {uploaded_csv.name}")

st.markdown("---")

has_input = bool(html_path or psids or esids or csv_path)
has_key = bool(gemini_key or groq_key or openrouter_key or mode == "rules")

run_disabled = st.session_state.running or not has_input or not has_key

if not has_input:
    st.caption(
        "Provide an HTML file, PSIDs, ESIDs, or a services CSV above to enable the audit."
    )
elif not has_key and mode != "rules":
    st.caption(
        "Select at least one provider and enter its API key to enable AI checks."
    )

run_clicked = st.button(
    "Run audit" if not st.session_state.running else "Running...",
    disabled=run_disabled,
    type="primary",
)

if run_clicked and not st.session_state.running:
    st.session_state.running = True
    st.session_state.results = None
    st.session_state.log_lines = []
    st.session_state.error = ""

    config = {
        "html_path": html_path,
        "psids": psids,
        "esids": esids,
        "csv_path": csv_path,
        "mode": mode,
        "gemini_key": gemini_key,
        "groq_key": groq_key,
        "openrouter_key": openrouter_key,
        "entity": entity,
        "workers": workers,
        "take_screenshots": take_screenshots,
        "screenshots_dir": screenshots_dir,
        "drive_key_path": drive_key_path if use_drive else "",
        "drive_folder_id": drive_folder_id if use_drive else "",
    }

    log_q = queue.Queue()
    result_q = queue.Queue()

    thread = threading.Thread(
        target=_run_audit,
        args=(config, log_q, result_q),
        daemon=True,
    )
    thread.start()

    log_box = st.empty()
    progress_text = st.empty()

    while thread.is_alive() or not log_q.empty():
        while not log_q.empty():
            line = log_q.get_nowait()
            clean = line.strip()
            if clean:
                st.session_state.log_lines.append(clean)
        log_display = "\n".join(st.session_state.log_lines[-30:])
        log_box.code(log_display, language=None)
        time.sleep(0.3)

    if not result_q.empty():
        result = result_q.get_nowait()
        if "error" in result:
            st.session_state.error = result["error"]
        else:
            st.session_state.results = result

    st.session_state.running = False
    st.rerun()

if st.session_state.error:
    st.error(f"Audit failed: {st.session_state.error}")

if st.session_state.results:
    res = st.session_state.results
    issues = res.get("issues", [])

    st.markdown("---")
    st.markdown("### Results")

    ss_count = sum(1 for i in issues if i.get("screenshot"))
    rules_count = sum(1 for i in issues if i.get("rule_id"))
    rejected = sum(
        1 for v in st.session_state.reviewed.values() if v["status"] == "rejected"
    )
    accepted = sum(
        1 for v in st.session_state.reviewed.values() if v["status"] == "accepted"
    )

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Services audited", res.get("completed", 0))
    m2.metric("Services failed", res.get("failed", 0))
    m3.metric("Total issues", len(issues) + len(st.session_state.added_issues))
    m4.metric("From rules", rules_count)
    m5.metric("Screenshots", ss_count)

    if issues:
        section_counts = {}
        for iss in issues:
            s = iss.get("section", "Other")
            section_counts[s] = section_counts.get(s, 0) + 1

        st.markdown("**Issues by section**")
        cols = st.columns(min(4, len(section_counts)))
        for i, (section, count) in enumerate(
            sorted(section_counts.items(), key=lambda x: -x[1])
        ):
            cols[i % len(cols)].metric(section, count)

        if not st.session_state.reviewing:
            st.markdown("**All issues**")

            fcol1, fcol2, fcol3 = st.columns(3)
            with fcol1:
                sections = ["All"] + sorted(set(i.get("section", "") for i in issues))
                sel_section = st.selectbox("Filter by section", sections)
            with fcol2:
                langs = ["All", "EN", "AR", "Both"]
                sel_lang = st.selectbox("Filter by language", langs)
            with fcol3:
                sources = ["All", "Rules", "AI"]
                sel_source = st.selectbox("Filter by source", sources)

            filtered = issues
            if sel_section != "All":
                filtered = [i for i in filtered if i.get("section") == sel_section]
            if sel_lang != "All":
                filtered = [i for i in filtered if i.get("language") == sel_lang]
            if sel_source == "Rules":
                filtered = [i for i in filtered if i.get("rule_id")]
            elif sel_source == "AI":
                filtered = [i for i in filtered if not i.get("rule_id")]

            st.caption(f"Showing {len(filtered)} of {len(issues)} issues")

            table_data = []
            for iss in filtered:
                idx = iss.get("id", "")
                rev = st.session_state.reviewed.get(str(idx), {})
                status_icon = {"accepted": "✓", "rejected": "✗", "edited": "✎"}.get(
                    rev.get("status", ""), ""
                )
                table_data.append(
                    {
                        "": status_icon,
                        "Section": iss.get("section", ""),
                        "Language": iss.get("language", ""),
                        "Source": iss.get("rule_id", "AI"),
                        "Description": iss.get("issue_description", "")[:120],
                        "Solution": iss.get("proposed_solution", "")[:120],
                    }
                )

            st.dataframe(
                table_data,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "": st.column_config.TextColumn(width="small"),
                    "Section": st.column_config.TextColumn(width="small"),
                    "Language": st.column_config.TextColumn(width="small"),
                    "Source": st.column_config.TextColumn(width="small"),
                    "Description": st.column_config.TextColumn(width="large"),
                    "Solution": st.column_config.TextColumn(width="large"),
                },
            )

            st.markdown("---")
            dl_col, rev_col = st.columns([2, 1])

            with dl_col:
                final_issues = _apply_review(
                    issues, st.session_state.reviewed, st.session_state.added_issues
                )
                csv_data = _issues_to_csv(final_issues)
                st.download_button(
                    label="Download CSV",
                    data=csv_data.encode("utf-8-sig"),
                    file_name=f"audit_{entity.replace(' ','_') or 'results'}.csv",
                    mime="text/csv",
                )

            with rev_col:
                if st.button("Review issues before export"):
                    st.session_state.reviewing = True
                    st.session_state.review_index = 0
                    st.rerun()

        else:
            _render_review(issues, entity)

    else:
        st.info("Audit completed — no issues found.")

if st.session_state.log_lines:
    with st.expander("Audit log", expanded=False):
        st.code("\n".join(st.session_state.log_lines), language=None)
