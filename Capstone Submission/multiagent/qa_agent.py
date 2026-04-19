import sys
import os
import re
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

_ARABIC_CHARS = re.compile(r"[\u0600-\u06FF]{3,}")
_ENGLISH_WORDS = re.compile(r"[A-Za-z]{4,}\s+[A-Za-z]{3,}")
_BLACKBOX_EN = re.compile(
    r"(as determined by|as per\s+(iGA|authority|ministry)\s+records?"
    r"|eligible\s+(families|citizens|individuals)\s+as defined"
    r"|head of\s+(family|household)"
    r"|permanent resident"
    r"|according to\s+(the\s+)?(authority|ministry|iGA))",
    re.IGNORECASE,
)
_BLACKBOX_AR = re.compile(
    r"(وفقاً لسجلات|حسب سجلات"
    r"|رب\s+(الأسرة|العائلة)"
    r"|المقيم\s+(الدائم|بصفة دائمة)"
    r"|وفق معايير الجهة"
    r"|كما تحدده هيئة"
    r"|حسب ما تحدده)"
)
_ONLINE_CLAIM = re.compile(
    r"(online|electronically|e-?service|website|portal|directly|إلكتروني|عبر الموقع|مباشرة)",
    re.IGNORECASE,
)
_PHYSICAL_REQUIRED = re.compile(
    r"(visit|appointment|in.person|social cent|book.*appoint"
    r"|حجز\s*موعد|زيارة\s*(المرك|الجهة|مكتب)"
    r"|مركز\s*اجتماعي)",
    re.IGNORECASE,
)


def _scan_flags(en_data: dict, ar_data: dict) -> list[str]:
    flags = []

    for field in ("required_attachments", "service_processes", "service_description"):
        val = en_data.get(field, "") or ""
        if _ARABIC_CHARS.search(val):
            flags.append(
                f"BILINGUAL_LEAK_EN | field={field} | "
                f"Arabic characters found in English content — must be translated to English"
            )

    for field in ("required_attachments", "service_processes"):
        val = ar_data.get(field, "") or ""
        if _ENGLISH_WORDS.search(val):
            flags.append(
                f"BILINGUAL_LEAK_AR | field={field} | "
                f"English sentences found in Arabic content — must be translated to Arabic"
            )

    for field in ("service_conditions", "service_description"):
        val = en_data.get(field, "") or ""
        m = _BLACKBOX_EN.search(val)
        if m:
            flags.append(
                f"BLACK_BOX_ELIGIBILITY | lang=EN | field={field} | "
                f"Vague criterion: '{m.group()[:80]}' — "
                f"the specific measurable rule must be stated explicitly"
            )

    for field in ("service_conditions", "service_description"):
        val = ar_data.get(field, "") or ""
        m = _BLACKBOX_AR.search(val)
        if m:
            flags.append(
                f"BLACK_BOX_ELIGIBILITY | lang=AR | field={field} | "
                f"Vague criterion: '{m.group()[:80]}' — "
                f"the specific measurable rule must be stated explicitly"
            )

    en_proc = en_data.get("service_processes", "") or ""
    en_att = en_data.get("required_attachments", "") or ""
    ar_att = ar_data.get("required_attachments", "") or ""
    combined = en_proc + " " + en_att + " " + ar_att
    if _ONLINE_CLAIM.search(en_proc) and _PHYSICAL_REQUIRED.search(combined):
        flags.append(
            "CONTRADICTORY_CHANNELS | "
            "Service claims online completion but also requires physical visit or appointment — "
            "clarify whether physical attendance is mandatory for all or only as fallback"
        )

    return flags


def _build_content(en_data: dict, ar_data: dict, is_eservice: bool) -> str:
    if is_eservice:
        return S.format_eservice_for_ai(en_data, ar_data)
    return S.format_for_ai(en_data, "en") + "\n\n" + S.format_for_ai(ar_data, "ar")


def _detect_present_sections(en_data: dict, ar_data: dict) -> set[str]:
    active = {"1", "2", "10", "11"}
    checks = [
        ("required_attachments", {"3"}),
        ("legal_regulations", {"4"}),
        ("fees", {"5"}),
        ("process_time", {"6"}),
        ("service_provider", {"7"}),
        ("service_processes", {"8"}),
        ("service_conditions", {"9"}),
    ]
    for key, ids in checks:
        if en_data.get(key) or ar_data.get(key):
            active |= ids
    return active


def _audit_one(
    scraped: dict,
    gemini_key: str,
    groq_key: str,
    openrouter_key: str,
    cache: AuditCache,
) -> dict:
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

        covered = R.get_covered_categories(rule_issues)
        validated_ai = R.validate_and_clean(
            raw_ai_issues, entity, service, covered_categories=covered
        )
        all_issues = rule_issues + validated_ai

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

        _log(
            f"    [audited]  [{psid}] "
            f"rules={len(rule_issues)} ai={len(validated_ai)} "
            f"flags={len(flags)} total={len(all_issues)}"
        )
        return dict(scraped=scraped, issues=all_issues, error="")

    except Exception as exc:
        _log(f"    [failed]   [{psid}] {exc}")
        return dict(scraped=scraped, issues=[], error=str(exc))


def qa_agent(state: dict) -> dict:
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
