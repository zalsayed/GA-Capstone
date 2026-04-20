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
    page_title="Bahrain.bh QA Auditor",
    layout="wide",
    initial_sidebar_state="expanded",
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


def _run_audit(config: dict, log_q: queue.Queue, result_q: queue.Queue):
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
                log_q.put("Google Drive connected")
            except Exception as e:
                log_q.put(f"Drive connection failed: {e}")

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


def _apply_review(issues, reviewed, added):
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


def _save_corrections_to_gt(reviewed, issues, added, entity):
    import csv as _csv, os as _os, re as _re

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
        m = _re.search(r"[?&]psID=(\d+)", iss.get("service", "") or "", _re.IGNORECASE)
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


def _issues_to_csv(issues):
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


def _render_review(issues, entity):
    reviewed = st.session_state.reviewed
    added = st.session_state.added_issues
    idx = st.session_state.review_index
    total = len(issues)

    accepted = sum(1 for v in reviewed.values() if v["status"] == "accepted")
    rejected = sum(1 for v in reviewed.values() if v["status"] == "rejected")
    edited = sum(1 for v in reviewed.values() if v["status"] == "edited")
    remaining = total - len(reviewed)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total", total)
    c2.metric("Accepted", accepted)
    c3.metric("Edited", edited)
    c4.metric("Rejected", rejected)
    c5.metric("Remaining", remaining)
    st.progress(len(reviewed) / total if total else 1.0)

    if idx < total:
        iss = issues[idx]
        iss_id = str(iss.get("id", ""))
        rev = reviewed.get(iss_id, {})

        st.info(
            f"**Issue {idx+1} of {total}**  \n"
            f"Section: **{iss.get('section','')}** · "
            f"Language: **{iss.get('language','')}** · "
            f"Source: **{iss.get('rule_id','AI')}**  \n"
            f"*{iss.get('issue_placement','')}*"
        )

        new_desc = st.text_area(
            "Description",
            height=80,
            value=rev.get("description", iss.get("issue_description", "")),
            key=f"desc_{iss_id}",
        )
        new_sol = st.text_area(
            "Proposed solution",
            height=80,
            value=rev.get("solution", iss.get("proposed_solution", "")),
            key=f"sol_{iss_id}",
        )

        sev_col, _ = st.columns([1, 2])
        with sev_col:
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

        b1, b2, b3, b4 = st.columns(4)
        if b1.button("Accept", key=f"acc_{iss_id}", use_container_width=True):
            reviewed[iss_id] = {
                "status": "accepted",
                "severity": sev,
                "notes": notes,
                "description": new_desc,
                "solution": new_sol,
            }
            st.session_state.review_index = idx + 1
            st.rerun()
        if b2.button(
            "Accept with edits", key=f"edit_{iss_id}", use_container_width=True
        ):
            reviewed[iss_id] = {
                "status": "edited",
                "severity": sev,
                "notes": notes,
                "description": new_desc,
                "solution": new_sol,
            }
            st.session_state.review_index = idx + 1
            st.rerun()
        if b3.button("Reject", key=f"rej_{iss_id}", use_container_width=True):
            reviewed[iss_id] = {
                "status": "rejected",
                "severity": sev,
                "notes": notes,
                "description": new_desc,
                "solution": new_sol,
            }
            st.session_state.review_index = idx + 1
            st.rerun()
        if b4.button("Skip", key=f"skip_{iss_id}", use_container_width=True):
            st.session_state.review_index = idx + 1
            st.rerun()
    else:
        st.success("All issues reviewed.")

    st.divider()
    with st.expander("Add an issue the tool missed"):
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

    st.divider()
    fin1, fin2 = st.columns([2, 1])
    with fin1:
        if st.button("Finalize & export", type="primary", use_container_width=True):
            final = _apply_review(issues, reviewed, added)
            csv_data = _issues_to_csv(final)
            gt_path = _save_corrections_to_gt(reviewed, issues, added, entity)
            st.session_state.reviewing = False
            st.session_state.results["_final_csv"] = csv_data
            if gt_path:
                st.success(f"Corrections saved to {gt_path}")
            st.rerun()
    with fin2:
        if st.button("Exit without saving", use_container_width=True):
            st.session_state.reviewing = False
            st.rerun()

    if st.session_state.results.get("_final_csv"):
        st.download_button(
            label="Download reviewed CSV",
            data=st.session_state.results["_final_csv"].encode("utf-8-sig"),
            file_name=f"audit_reviewed_{entity.replace(' ','_') or 'results'}.csv",
            mime="text/csv",
        )


# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## Configuration")
    st.divider()

    entity = st.text_input(
        "Entity name",
        placeholder="e.g. National Health Regulatory Authority",
        help="The government entity you are auditing",
    )

    mode = st.selectbox(
        "Audit mode",
        options=["full", "rules", "ai"],
        format_func=lambda x: {
            "full": "Full — rules + AI",
            "rules": "Rules only — no API",
            "ai": "AI only",
        }[x],
        help="Full mode gives the best results. Rules only is instant and free.",
    )

    workers = st.slider(
        "Parallel workers",
        min_value=1,
        max_value=6,
        value=2,
        help="How many services to audit simultaneously",
    )

    st.divider()
    st.markdown("**API Providers**")

    if mode == "rules":
        st.caption("No API keys needed for rules-only mode.")
        use_gemini = use_groq = use_openrouter = False
        gemini_key = groq_key = openrouter_key = ""
    else:
        use_gemini = st.checkbox("Gemini", value=True)
        use_groq = st.checkbox("Groq (fallback)")
        use_openrouter = st.checkbox("OpenRouter (fallback)")

        gemini_key = groq_key = openrouter_key = ""
        if use_gemini:
            gemini_key = st.text_input(
                "Gemini key", type="password", placeholder="AIza...", key="gkey"
            )
        if use_groq:
            groq_key = st.text_input(
                "Groq key", type="password", placeholder="gsk_...", key="grkey"
            )
        if use_openrouter:
            openrouter_key = st.text_input(
                "OpenRouter key", type="password", placeholder="sk-or-...", key="orkey"
            )

        active = [
            p
            for p, u in [
                ("Gemini", use_gemini),
                ("Groq", use_groq),
                ("OpenRouter", use_openrouter),
            ]
            if u
        ]
        if active:
            st.caption("Chain: " + " → ".join(active))

    st.divider()
    st.markdown("**Screenshots**")
    take_screenshots = st.checkbox("Capture screenshots")
    screenshots_dir = "screenshots"
    if take_screenshots:
        screenshots_dir = st.text_input("Save to folder", value="screenshots")

    use_drive = st.checkbox(
        "Upload to Google Drive",
        disabled=not take_screenshots,
        help="Requires screenshots to be enabled",
    )
    drive_key_path = ""
    drive_folder_id = ""
    if use_drive and take_screenshots:
        uploaded_drive_key = st.file_uploader("client_secret.json", type=["json"])
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
            help="The ID at the end of your Drive folder URL",
        )

    st.divider()
    st.caption("bahrain.bh ContentAuditor · Bilingual audit tool")


# ── MAIN AREA ─────────────────────────────────────────────────────────────────
st.title("Bahrain.bh ContentAuditor")
st.caption(
    "Automated quality checking for government service pages in Arabic and English."
)
st.divider()

# ── INPUT ─────────────────────────────────────────────────────────────────────
st.subheader("What would you like to audit?")

input_tab, psid_tab, esid_tab, csv_tab = st.tabs(
    [
        "Upload HTML page",
        "Enter Service IDs (PSIDs)",
        "Enter eService IDs (ESIDs)",
        "Upload services CSV",
    ]
)

html_path = psids = esids = csv_path = None
psids = []
esids = []

with input_tab:
    st.caption("Download the entity page from bahrain.bh (Ctrl+S) and upload it here.")
    uploaded_html = st.file_uploader(
        "Entity HTML page", type=["html", "htm"], label_visibility="collapsed"
    )
    if uploaded_html:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
        tmp.write(uploaded_html.read())
        tmp.flush()
        html_path = tmp.name
        st.success(f"Loaded {uploaded_html.name}")

with psid_tab:
    st.caption("Find the psID in the service page URL: ...?psID=1634")
    psid_input = st.text_area(
        "Service IDs — one per line or space-separated",
        placeholder="1634\n1635\n1636",
        height=120,
        label_visibility="collapsed",
    )
    if psid_input.strip():
        psids = [p.strip() for p in psid_input.replace(",", " ").split() if p.strip()]
        st.caption(f"{len(psids)} service ID(s) entered")

with esid_tab:
    st.caption("Find the esID in the eService URL: ...?esID=230")
    esid_input = st.text_area(
        "eService IDs — one per line or space-separated",
        placeholder="230\n456",
        height=120,
        label_visibility="collapsed",
    )
    if esid_input.strip():
        esids = [e.strip() for e in esid_input.replace(",", " ").split() if e.strip()]
        st.caption(f"{len(esids)} eService ID(s) entered")

with csv_tab:
    st.caption("Upload a CSV with columns: PSID, Service Name, URL, Is_eService")
    uploaded_csv = st.file_uploader(
        "Services CSV", type=["csv"], label_visibility="collapsed"
    )
    if uploaded_csv:
        tmp_csv = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        tmp_csv.write(uploaded_csv.read())
        tmp_csv.flush()
        csv_path = tmp_csv.name
        try:
            import csv as _csv

            with open(csv_path, encoding="utf-8-sig") as f:
                n = sum(1 for _ in _csv.DictReader(f))
            st.success(f"Loaded {uploaded_csv.name} — {n} service(s)")
        except Exception:
            st.success(f"Loaded {uploaded_csv.name}")

st.divider()

# ── RUN ───────────────────────────────────────────────────────────────────────
has_input = bool(html_path or psids or esids or csv_path)
has_key = bool(gemini_key or groq_key or openrouter_key or mode == "rules")

if not has_input:
    st.info(
        "Select an input method above — upload an HTML page, enter service IDs, or upload a CSV."
    )
elif not has_key and mode != "rules":
    st.warning("Add at least one API key in the sidebar to enable AI checks.")

run_label = "Running audit..." if st.session_state.running else "Run audit"
run_disabled = st.session_state.running or not has_input or not has_key

if st.button(
    run_label, type="primary", disabled=run_disabled, use_container_width=False
):
    st.session_state.running = True
    st.session_state.results = None
    st.session_state.log_lines = []
    st.session_state.error = ""
    st.session_state.reviewing = False
    st.session_state.review_index = 0
    st.session_state.reviewed = {}
    st.session_state.added_issues = []

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
        target=_run_audit, args=(config, log_q, result_q), daemon=True
    )
    thread.start()

    log_box = st.empty()
    while thread.is_alive() or not log_q.empty():
        while not log_q.empty():
            line = log_q.get_nowait().strip()
            if line:
                st.session_state.log_lines.append(line)
        log_box.code("\n".join(st.session_state.log_lines[-30:]), language=None)
        time.sleep(0.3)

    if not result_q.empty():
        result = result_q.get_nowait()
        if "error" in result:
            st.session_state.error = result["error"]
        else:
            st.session_state.results = result

    st.session_state.running = False
    st.rerun()

# ── RESULTS ───────────────────────────────────────────────────────────────────
if st.session_state.error:
    st.error(f"Audit failed: {st.session_state.error}")

if st.session_state.results:
    res = st.session_state.results
    issues = res.get("issues", [])

    st.divider()
    st.subheader("Results")

    ss_count = sum(1 for i in issues if i.get("screenshot"))
    rules_count = sum(1 for i in issues if i.get("rule_id"))
    total_with_added = len(issues) + len(st.session_state.added_issues)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Services audited", res.get("completed", 0))
    m2.metric("Services failed", res.get("failed", 0))
    m3.metric("Total issues", total_with_added)
    m4.metric("From rules", rules_count)
    m5.metric("Screenshots", ss_count)

    if not issues:
        st.info("Audit completed — no issues found.")
    elif st.session_state.reviewing:
        st.divider()
        _render_review(issues, entity)
    else:
        st.divider()
        st.subheader("Issues by section")
        section_counts = {}
        for iss in issues:
            s = iss.get("section", "Other")
            section_counts[s] = section_counts.get(s, 0) + 1

        sec_cols = st.columns(min(5, len(section_counts)))
        for i, (sec, cnt) in enumerate(
            sorted(section_counts.items(), key=lambda x: -x[1])
        ):
            sec_cols[i % len(sec_cols)].metric(sec, cnt)

        st.divider()
        st.subheader("All issues")

        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            sel_section = st.selectbox(
                "Filter by section",
                ["All"] + sorted(set(i.get("section", "") for i in issues)),
            )
        with fc2:
            sel_lang = st.selectbox("Filter by language", ["All", "EN", "AR", "Both"])
        with fc3:
            sel_source = st.selectbox("Filter by source", ["All", "Rules", "AI"])

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
            status = {
                "accepted": "Accepted",
                "rejected": "Rejected",
                "edited": "Edited",
            }.get(rev.get("status", ""), "")
            table_data.append(
                {
                    "Review": status,
                    "Section": iss.get("section", ""),
                    "Language": iss.get("language", ""),
                    "Source": iss.get("rule_id", "AI"),
                    "Description": iss.get("issue_description", "")[:140],
                    "Solution": iss.get("proposed_solution", "")[:140],
                }
            )

        st.dataframe(
            table_data,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Review": st.column_config.TextColumn(width="small"),
                "Section": st.column_config.TextColumn(width="small"),
                "Language": st.column_config.TextColumn(width="small"),
                "Source": st.column_config.TextColumn(width="small"),
                "Description": st.column_config.TextColumn(width="large"),
                "Solution": st.column_config.TextColumn(width="large"),
            },
        )

        st.divider()
        dl_col, rev_col = st.columns([1, 1])
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
                use_container_width=True,
            )
        with rev_col:
            if st.button("Review issues before export", use_container_width=True):
                st.session_state.reviewing = True
                st.session_state.review_index = 0
                st.rerun()

        if st.session_state.results.get("_final_csv"):
            st.download_button(
                label="Download reviewed CSV",
                data=st.session_state.results["_final_csv"].encode("utf-8-sig"),
                file_name=f"audit_reviewed_{entity.replace(' ','_') or 'results'}.csv",
                mime="text/csv",
            )

if st.session_state.log_lines:
    with st.expander("Audit log"):
        st.code("\n".join(st.session_state.log_lines), language=None)
