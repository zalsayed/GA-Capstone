import sys
import os
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from concurrent.futures import ThreadPoolExecutor, as_completed

import scraper as S

_print_lock = threading.Lock()


def _log(message: str) -> None:
    with _print_lock:
        print(message)


MAX_SCRAPE_WORKERS = 4


def _scrape_one(job: dict) -> dict:
    """Scrape EN and AR pages for a single service. Returns an isolated result dict."""
    psid = job["psid"]
    try:
        if job.get("is_eservice"):
            en_data, ar_data, en_html, ar_html = S.scrape_esid(psid)
            en_url = S.ESERVICE_BASE_URL.format(lang="en", esid=psid)
            ar_url = S.ESERVICE_BASE_URL.format(lang="ar", esid=psid)
        else:
            en_data, ar_data, en_html, ar_html = S.scrape_psid(psid)
            en_url = S.BASE_URL.format(lang="en", psid=psid)
            ar_url = S.BASE_URL.format(lang="ar", psid=psid)

        service_name = en_data.get("service_name") or job.get("name", psid)
        _log(f"    [scraped]  [{psid}] {service_name[:45]}")
        return dict(
            job=job,
            en_data=en_data,
            ar_data=ar_data,
            en_html=en_html,
            ar_html=ar_html,
            en_url=en_url,
            ar_url=ar_url,
            error="",
        )
    except Exception as exc:
        _log(f"    [failed]   [{psid}] {exc}")
        return dict(
            job=job,
            en_data={},
            ar_data={},
            en_html="",
            ar_html="",
            en_url="",
            ar_url="",
            error=str(exc),
        )


def scraper_agent(state: dict) -> dict:
    """
    Consume pending_scrape jobs and populate pending_audit with successfully scraped results.
    Returns a state-patch dict; the supervisor merges it into the shared state.
    """
    jobs = list(state.get("pending_scrape", []))
    if not jobs:
        return {}

    _log(
        f"\n  -- Scraper Agent -- {len(jobs)} services ({MAX_SCRAPE_WORKERS} parallel) --"
    )

    succeeded = []
    failed = list(state.get("failed", []))

    with ThreadPoolExecutor(max_workers=MAX_SCRAPE_WORKERS) as executor:
        future_to_job = {executor.submit(_scrape_one, job): job for job in jobs}
        for future in as_completed(future_to_job):
            result = future.result()
            if result["error"]:
                failed.append(
                    {
                        "psid": result["job"]["psid"],
                        "stage": "scrape",
                        "error": result["error"],
                    }
                )
            else:
                succeeded.append(result)

    _log(
        f"  -- Scraper done: {len(succeeded)} OK, {len(jobs) - len(succeeded)} failed --\n"
    )

    return {
        "pending_scrape": [],
        "pending_audit": state.get("pending_audit", []) + succeeded,
        "failed": failed,
    }
