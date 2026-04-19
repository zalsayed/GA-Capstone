"""
pipeline.py - True service-parallel multi-agent pipeline with smart scheduler.

Each service runs its complete pipeline independently:
    scrape -> audit -> screenshot -> report

Smart scheduler:
  Worker concurrency is scaled per stage by resource weight — scrape workers
  are I/O-bound (cheap), audit workers hold AI API connections (expensive),
  screenshot workers hold Playwright browser instances (heaviest).
  The scheduler also checks cloud-provider rate pressure before dispatching
  audit work, pausing briefly when all providers are congested rather than
  flooding them with requests that will immediately return 429s.

Cache:
  A shared AuditCache instance detects unchanged pages by content hash and
  reuses previous audit results, skipping the AI call entirely for those
  services.

Smarter prompts:
  The audit stage pre-scans which sections are present on each page and
  builds a tailored prompt that omits checks for absent sections, cutting
  token usage and reducing hallucinations on sparse pages.
"""

import sys
import os
import json
import threading
import time
import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import scraper as S
import ai as AI
import rules as R
import output as O
import screenshot as SS
from cache import AuditCache
from qa_agent import _scan_flags, _build_content


_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _force_load(name: str):
    for folder in (_HERE, _PARENT):
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

CHECKPOINT_DIR = "reports"
CHECKPOINT_FILE = os.path.join(CHECKPOINT_DIR, ".checkpoint_v3.json")

_checkpoint_lock = threading.Lock()
_print_lock = threading.Lock()

_STAGE_WEIGHT = {
    "scrape": 1,
    "audit": 3,
    "screenshot": 4,
    "report": 1,
}


def _log(message: str) -> None:
    with _print_lock:
        sys.stdout.write("\r" + " " * 60 + "\r")
        print(message, flush=True)


def _load_checkpoint() -> dict:
    """Return {psid: status} dict, or {} if no checkpoint exists."""
    if not os.path.exists(CHECKPOINT_FILE):
        return {}
    with _checkpoint_lock:
        try:
            with open(CHECKPOINT_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}


def _mark_service_done(
    checkpoint: dict, psid: str, stage: str, is_eservice: bool = False
) -> None:
    """
    Atomically mark psid as completed at `stage` in both the in-memory dict
    and the on-disk checkpoint file.

    Value format: "stage" or "stage|eservice" so --resume can reconstruct URLs.
    The entire read-modify-write is performed inside a single lock acquisition
    so concurrent workers can never interleave their writes and corrupt the file.
    """
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    value = f"{stage}|eservice" if is_eservice else stage
    with _checkpoint_lock:
        # Re-read from disk so we merge concurrent updates rather than overwriting them
        on_disk: dict = {}
        if os.path.exists(CHECKPOINT_FILE):
            try:
                with open(CHECKPOINT_FILE, encoding="utf-8") as fh:
                    on_disk = json.load(fh)
            except Exception:
                on_disk = {}
        on_disk.update(checkpoint)  # keep in-memory entries not yet flushed
        on_disk[psid] = value
        checkpoint[psid] = value  # keep in-memory view consistent
        tmp = CHECKPOINT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(on_disk, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CHECKPOINT_FILE)  # atomic on POSIX


def clear_checkpoint() -> None:
    with _checkpoint_lock:
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)


def _dynamic_max_workers(stage: str, base_workers: int, take_screenshots: bool) -> int:
    """
    Translate base_workers into a stage-appropriate thread count.

    Capacity scales with user preference but is weighted inversely to the
    resource cost of each stage.
    """
    weight = _STAGE_WEIGHT.get(stage, 1)
    capacity = max(1, (base_workers * 3) // weight)
    if stage == "screenshot" and take_screenshots:
        capacity = min(capacity, 2)  # hard cap — browsers are memory-heavy
    return capacity


def _wait_if_all_congested(gemini_key: str, groq_key: str, openrouter_key: str) -> None:
    """
    Pause briefly when every configured cloud provider is congested and
    Ollama is not available, to avoid dispatching audit jobs that will
    immediately receive 429 responses.
    """
    try:
        from ai import _rate_tracker, is_ollama_available

        configured = [
            p
            for p, k in [
                ("gemini", gemini_key),
                ("groq", groq_key),
                ("openrouter", openrouter_key),
            ]
            if k
        ]
        if configured and all(_rate_tracker.is_congested(p) for p in configured):
            if not is_ollama_available():
                _log("  [scheduler] all cloud providers congested - waiting 10s...")
                time.sleep(10)
    except Exception:
        pass


def _run_service(job: dict, cfg: dict) -> dict:
    """
    Execute the complete scrape -> audit -> screenshot -> report pipeline
    for a single service in one worker thread.

    cfg keys:
        gemini_key, groq_key, openrouter_key,
        take_screenshots, screenshots_dir,
        drive_service, drive_folder, upload_fn,
        entity_name, checkpoint, audit_cache
    """
    psid = job["psid"]
    is_eservice = job.get("is_eservice", False)
    gemini_key = cfg["gemini_key"]
    groq_key = cfg["groq_key"]
    openrouter_key = cfg["openrouter_key"]
    take_screenshots = cfg["take_screenshots"]
    screenshots_dir = cfg["screenshots_dir"]
    drive_service = cfg.get("drive_service")
    drive_folder = cfg.get("drive_folder", "")
    upload_fn = cfg.get("upload_fn")
    entity_name = cfg.get("entity_name", "")
    checkpoint = cfg["checkpoint"]
    audit_cache: AuditCache = cfg["audit_cache"]
    audit_mode = cfg.get("audit_mode", "full")

    result = {
        "psid": psid,
        "job": job,
        "issues": [],
        "csv": "",
        "error": "",
        "from_cache": False,
    }

    _log(f"  [{psid}] scraping...")
    try:
        if is_eservice:
            en_data, ar_data, en_html, ar_html = S.scrape_esid(psid)
            en_url = S.ESERVICE_BASE_URL.format(lang="en", esid=psid)
            ar_url = S.ESERVICE_BASE_URL.format(lang="ar", esid=psid)
        else:
            en_data, ar_data, en_html, ar_html = S.scrape_psid(psid)
            en_url = S.BASE_URL.format(lang="en", psid=psid)
            ar_url = S.BASE_URL.format(lang="ar", psid=psid)

        service_name = en_data.get("service_name") or job.get("name", psid)
        _log(f"  [{psid}] scraped: {service_name[:45]}")
    except Exception as exc:
        result["error"] = f"scrape: {exc}"
        _log(f"  [{psid}] scrape failed: {exc}")
        return result

    entity = en_data.get("service_provider", "") or entity_name
    service = en_data.get("service_name", "") or job.get("name", psid)

    _log(f"  [{psid}] auditing...")
    try:
        cached = audit_cache.get(psid, en_html, ar_html)
        if cached:
            all_issues = cached["issues"]
            result["from_cache"] = True
            _log(f"  [{psid}] cache-hit — {len(all_issues)} issues reused")
        else:
            rule_issues = (
                R.run_all_rules(en_data, ar_data) if audit_mode != "ai" else []
            )

            raw_ai_issues = []
            if audit_mode != "rules":
                flags = _scan_flags(en_data, ar_data)
                content = _build_content(en_data, ar_data, is_eservice)

                if is_eservice:
                    raw_ai_issues = (
                        AI.call_ai_eservice(
                            user_prompt=content,
                            gemini_key=gemini_key,
                            groq_key=groq_key,
                            openrouter_key=openrouter_key,
                        )
                        or []
                    )
                else:
                    raw_ai_issues = (
                        AI.call_ai_split(
                            user_prompt=content,
                            flags=flags,
                            gemini_key=gemini_key,
                            groq_key=groq_key,
                            openrouter_key=openrouter_key,
                            psid=psid,
                        )
                        or []
                    )

            covered = (
                R.get_covered_categories(rule_issues) if audit_mode == "full" else set()
            )
            validated_ai = R.validate_and_clean(
                raw_ai_issues, entity, service, covered_categories=covered
            )
            all_issues = rule_issues + validated_ai

            audit_cache.set(psid, en_html, ar_html, all_issues)
            mode_tag = f"[{audit_mode}]" if audit_mode != "full" else ""
            _log(
                f"  [{psid}] {mode_tag}rules={len(rule_issues)} "
                f"ai={len(validated_ai)} total={len(all_issues)}"
            )

        for index, issue in enumerate(all_issues, 1):
            issue["id"] = index
            issue["screenshot"] = ""
            issue["_service_cell"] = O._service_cell(service, en_url)
            issue.setdefault("entity", entity)
            issue.setdefault("service", service)

        result["issues"] = all_issues

        if all_issues:
            csv_path = O.write_service_csv(
                issues=all_issues,
                psid=psid,
                service_url=en_url,
                entity=entity,
            )
            result["csv"] = csv_path
            _mark_service_done(checkpoint, psid, "audited", is_eservice)

    except Exception as exc:
        result["error"] = f"audit: {exc}"
        _log(f"  [{psid}] audit failed: {exc}")
        return result

    # ── Stage 3: Screenshots (non-fatal) ──────────────────────────────────
    if take_screenshots and result["issues"] and SS.is_available():
        _log(f"  [{psid}] taking screenshots...")
        try:
            updated = SS.take_screenshots(
                issues=result["issues"],
                psid=psid,
                en_url=en_url,
                ar_url=ar_url,
                screenshots_dir=screenshots_dir,
                en_html=en_html,
                ar_html=ar_html,
                drive_service=drive_service,
                drive_folder=drive_folder,
                upload_fn=upload_fn,
            )
            result["issues"] = updated or result["issues"]
            captured = sum(1 for i in result["issues"] if i.get("screenshot"))
            _log(f"  [{psid}] screenshots: {captured}/{len(result['issues'])}")
        except Exception as exc:
            _log(f"  [{psid}] screenshots failed (non-fatal): {exc}")

    # ── Stage 4: Final CSV with screenshot links ───────────────────────────
    if result["issues"]:
        try:
            entity = result["issues"][0].get("entity", entity_name)
            service = result["issues"][0].get("service", psid)
            result["csv"] = O.write_service_csv(
                issues=result["issues"],
                psid=psid,
                service_url=en_url,
                entity=entity,
            )
        except Exception as exc:
            _log(f"  [{psid}] final CSV write failed: {exc}")

    _mark_service_done(checkpoint, psid, "done", is_eservice)
    _log(f"  [{psid}] complete - {len(result['issues'])} issues")
    return result


def run_pipeline(initial_state: dict) -> dict:
    """
    Smart service-parallel pipeline.

    Each service runs all four stages in a single worker thread.
    The scheduler allocates worker slots by resource cost per stage and
    checks provider rate pressure before dispatching audit-heavy work.
    A shared AuditCache skips unchanged pages entirely.
    """
    gemini_key = initial_state.get("gemini_key", "")
    groq_key = initial_state.get("groq_key", "")
    openrouter_key = initial_state.get("openrouter_key", "")
    take_screenshots = initial_state.get("take_screenshots", False)
    screenshots_dir = initial_state.get("screenshots_dir", "screenshots")
    drive_service = initial_state.get("drive_service")
    drive_folder = initial_state.get("drive_folder", "")
    entity_name = initial_state.get("entity_name", "")
    max_workers = initial_state.get("max_workers", 3)
    audit_mode = initial_state.get("audit_mode", "full")
    run_reviewer = initial_state.get("run_reviewer", False)
    jobs = initial_state["pending_scrape"]

    upload_fn = None
    if drive_service and drive_folder:
        try:
            import drive as D

            upload_fn = D.upload
        except ImportError:
            pass

    audit_cache = AuditCache()
    checkpoint = _load_checkpoint()

    pending_jobs = [
        j for j in jobs if not str(checkpoint.get(j["psid"], "")).startswith("done")
    ]
    skipped_count = len(jobs) - len(pending_jobs)
    if skipped_count:
        _log(f"\n  Skipping {skipped_count} already-completed services (checkpoint)")

    scrape_workers = _dynamic_max_workers("scrape", max_workers, take_screenshots)
    audit_workers = _dynamic_max_workers("audit", max_workers, take_screenshots)
    ss_workers = _dynamic_max_workers("screenshot", max_workers, take_screenshots)

    print(
        f"\n  -- Pipeline -- {len(pending_jobs)} services | "
        f"workers: scrape={scrape_workers} audit={audit_workers} screenshot={ss_workers}\n"
    )

    worker_cfg = {
        "gemini_key": gemini_key,
        "groq_key": groq_key,
        "openrouter_key": openrouter_key,
        "take_screenshots": take_screenshots,
        "screenshots_dir": screenshots_dir,
        "drive_service": drive_service,
        "drive_folder": drive_folder,
        "upload_fn": upload_fn,
        "entity_name": entity_name,
        "checkpoint": checkpoint,
        "audit_cache": audit_cache,
        "audit_mode": audit_mode,
    }

    _wait_if_all_congested(gemini_key, groq_key, openrouter_key)

    all_results: list[dict] = []
    failed_results: list[dict] = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    with ThreadPoolExecutor(max_workers=scrape_workers) as executor:
        future_to_job = {
            executor.submit(_run_service, job, worker_cfg): job for job in pending_jobs
        }
        completed_count = 0
        for future in as_completed(future_to_job):
            completed_count += 1
            result = future.result()
            if result.get("error"):
                failed_results.append(
                    {"psid": result["psid"], "error": result["error"]}
                )
            else:
                all_results.append(result)
            _log(f"  Progress: {completed_count}/{len(pending_jobs)} services done")

    all_issues = [i for r in all_results for i in r.get("issues", [])]
    cache_hits = sum(1 for r in all_results if r.get("from_cache"))
    ss_count = sum(1 for i in all_issues if i.get("screenshot"))

    batch_csv = ""
    if all_issues:
        batch_csv = O.write_batch_csv(
            all_issues=all_issues,
            timestamp=timestamp,
            entity=entity_name,
        )

    print(f"\n  Services completed : {len(all_results)}")
    print(f"  Cache hits         : {cache_hits}")
    print(f"  Services failed    : {len(failed_results)}")
    print(f"  Total issues       : {len(all_issues)}")
    print(f"  Screenshots        : {ss_count}")
    if batch_csv:
        print(f"  Batch report       : {Path(batch_csv).name}")
    print(f"  Reports folder     : {os.path.abspath('reports')}/")

    if failed_results:
        print("\n  Failed services:")
        for entry in failed_results:
            print(f"    {entry['psid']}: {entry['error'][:70]}")
        # Write a failed-services CSV so there is always output to inspect
        try:
            import csv as _csv

            os.makedirs("reports", exist_ok=True)
            failed_csv = f"reports/failed_{timestamp}.csv"
            with open(failed_csv, "w", newline="", encoding="utf-8-sig") as fh:
                writer = _csv.DictWriter(fh, fieldnames=["psid", "error"])
                writer.writeheader()
                writer.writerows(failed_results)
            print(f"  Failed log         : {Path(failed_csv).name}")
        except Exception:
            pass

        print(
            f"\n  Checkpoint kept — run with --resume to retry {len(failed_results)} failed service(s)."
        )
        for entry in failed_results:
            _mark_service_done(checkpoint, entry["psid"], "failed")
    else:
        clear_checkpoint()

    if run_reviewer and all_issues and sys.stdin.isatty():
        try:
            from hitl_reviewer import run_review

            print("\n  --reviewer flag set. Starting human review...")
            review_result = run_review(
                issues=all_issues,
                entity=entity_name,
                original_csv_path=batch_csv,
            )
        except Exception as e:
            print(f"  Review skipped: {e}")

    return {
        "completed": all_results,
        "failed": failed_results,
        "output_csv": batch_csv,
        "total_issues": len(all_issues),
    }
