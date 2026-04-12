import sys
import os
import threading
import importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_print_lock = threading.Lock()


def _log(message: str) -> None:
    with _print_lock:
        print(message)


def _force_load(name: str):
    """Load a module by name from _HERE or _PARENT, bypassing sys.modules cache."""
    for folder in (_HERE, _PARENT):
        path = os.path.join(folder, f"{name}.py")
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError(f"{name}.py not found")


_force_load("ai")
_force_load("rules")
_force_load("scraper")
_force_load("output")

from concurrent.futures import ThreadPoolExecutor, as_completed

import ai as AI
import rules as R
import scraper as S
import output as O
from cache import AuditCache

MAX_AUDIT_WORKERS = 3

# Sections whose presence in en_data gates which prompt checks are included.
# Each entry: (data_key, check_ids_to_include_when_present)
_SECTION_CHECK_MAP = [
    ("attachments", {"3"}),
    ("regulations", {"4"}),
    ("fees", {"5"}),
    ("process_time", {"6"}),
    ("service_provider", {"7"}),
    ("processes", {"8"}),
    ("conditions", {"9"}),
]

_ALWAYS_INCLUDE = {"1", "2", "10", "11"}


def _detect_present_sections(en_data: dict, ar_data: dict) -> set[str]:
    """
    Return the set of check IDs that are relevant for this specific page.

    A check is relevant if the corresponding section has non-empty content
    in either the EN or AR data. This avoids asking the model to evaluate
    sections that simply do not exist on the page.
    """
    active = set(_ALWAYS_INCLUDE)
    for key, check_ids in _SECTION_CHECK_MAP:
        en_val = en_data.get(key)
        ar_val = ar_data.get(key)
        has_content = bool(en_val or ar_val)
        if has_content:
            active |= check_ids
    return active


def _build_tailored_prompt(
    en_data: dict,
    ar_data: dict,
    is_eservice: bool,
    active_checks: set[str],
) -> str:
    """
    Build a prompt that contains only the checks relevant to this page.

    For eService pages the full eService prompt is used unchanged (it is
    already compact). For Service Catalog pages a tailored subset is built.
    """
    if is_eservice:
        return S.format_eservice_for_ai(en_data, ar_data)

    base = S.format_for_ai(en_data, "en") + "\n\n" + S.format_for_ai(ar_data, "ar")

    skipped = {
        key for key, check_ids in _SECTION_CHECK_MAP if not (check_ids & active_checks)
    }

    if not skipped:
        return base

    skip_note = (
        "\n\nNOTE: The following sections are ABSENT from this page. "
        "Do NOT flag them as issues — skip entirely:\n"
        + ", ".join(sorted(skipped))
        + "\n"
    )
    return base + skip_note


def _audit_one(
    scraped: dict,
    gemini_key: str,
    groq_key: str,
    openrouter_key: str,
    cache: AuditCache,
) -> dict:
    """
    Run the full audit for a single scraped service.

    Checks the content-hash cache first. On a hit the previous issues are
    reused without any AI call. On a miss the page is audited and the result
    is stored in the cache for future runs.
    """
    job = scraped["job"]
    psid = job["psid"]
    en_data = scraped["en_data"]
    ar_data = scraped["ar_data"]
    en_html = scraped.get("en_html", "")
    ar_html = scraped.get("ar_html", "")
    is_eservice = job.get("is_eservice", False)
    en_url = scraped.get("en_url", "")

    entity = en_data.get("service_provider", "") or job.get("name", "")
    service = en_data.get("service_name", "") or job.get("name", psid)

    try:
        cached = cache.get(psid, en_html, ar_html)
        if cached:
            issues = cached["issues"]
            _log(f"    [cache-hit] [{psid}] {len(issues)} issues reused")
            return dict(scraped=scraped, issues=issues, error="", from_cache=True)

        rule_issues = R.run_all_rules(en_data, ar_data)

        active_checks = _detect_present_sections(en_data, ar_data)
        ai_prompt = _build_tailored_prompt(en_data, ar_data, is_eservice, active_checks)

        if is_eservice:
            raw_ai_issues = (
                AI.call_ai_eservice(
                    ai_prompt,
                    gemini_key=gemini_key,
                    groq_key=groq_key,
                    openrouter_key=openrouter_key,
                )
                or []
            )
        else:
            raw_ai_issues = (
                AI.call_ai(
                    ai_prompt,
                    gemini_key=gemini_key,
                    groq_key=groq_key,
                    openrouter_key=openrouter_key,
                )
                or []
            )

        validated_ai_issues = R.validate_and_clean(raw_ai_issues, entity, service)
        all_issues = rule_issues + validated_ai_issues

        for index, issue in enumerate(all_issues, 1):
            issue["id"] = index
            issue["screenshot"] = ""
            issue["_service_cell"] = O._service_cell(service, en_url)
            issue.setdefault("entity", entity)
            issue.setdefault("service", service)

        cache.set(psid, en_html, ar_html, all_issues)

        if all_issues:
            O.write_service_csv(
                issues=all_issues,
                psid=psid,
                service_url=en_url,
                entity=entity,
            )

        skipped_checks = len(_SECTION_CHECK_MAP) - len(
            [k for k, ids in _SECTION_CHECK_MAP if ids & active_checks]
        )
        _log(
            f"    [audited]  [{psid}] "
            f"rules={len(rule_issues)} ai={len(validated_ai_issues)} "
            f"total={len(all_issues)} skipped_checks={skipped_checks}"
        )
        return dict(scraped=scraped, issues=all_issues, error="")

    except Exception as exc:
        _log(f"    [failed]   [{psid}] {exc}")
        return dict(scraped=scraped, issues=[], error=str(exc))


def qa_agent(state: dict) -> dict:
    """
    Consume pending_audit jobs and populate pending_screenshot with results.

    A single AuditCache instance is shared across all workers in this run.
    Returns a state-patch dict; the supervisor merges it into the shared state.
    """
    jobs = list(state.get("pending_audit", []))
    if not jobs:
        return {}

    gemini_key = state.get("gemini_key", "")
    groq_key = state.get("groq_key", "")
    openrouter_key = state.get("openrouter_key", "")
    cache = state.get("_cache") or AuditCache()

    print(
        f"  Keys: gemini={'YES' if gemini_key else 'NO'} "
        f"groq={'YES' if groq_key else 'NO'} "
        f"openrouter={'YES' if openrouter_key else 'NO'}"
    )
    print(f"\n  -- QA Agent -- {len(jobs)} services ({MAX_AUDIT_WORKERS} parallel) --")

    succeeded = []
    failed = list(state.get("failed", []))

    with ThreadPoolExecutor(max_workers=MAX_AUDIT_WORKERS) as executor:
        future_to_scraped = {
            executor.submit(
                _audit_one,
                scraped,
                gemini_key,
                groq_key,
                openrouter_key,
                cache,
            ): scraped
            for scraped in jobs
        }
        for future in as_completed(future_to_scraped):
            result = future.result()
            if result["error"]:
                failed.append(
                    {
                        "psid": result["scraped"]["job"]["psid"],
                        "stage": "audit",
                        "error": result["error"],
                        "scraped_data": result["scraped"],
                    }
                )
            else:
                succeeded.append(result)

    cache_hits = sum(1 for r in succeeded if r.get("from_cache"))
    total_issues = sum(len(r["issues"]) for r in succeeded)
    print(
        f"  -- QA done: {len(succeeded)} OK ({cache_hits} from cache), "
        f"{len(jobs) - len(succeeded)} failed, {total_issues} issues --\n"
    )

    return {
        "pending_audit": [],
        "pending_screenshot": state.get("pending_screenshot", []) + succeeded,
        "failed": failed,
        "_cache": cache,
    }
