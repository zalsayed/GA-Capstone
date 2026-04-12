import sys
import os
import threading
from threading import Event

_print_lock = threading.Lock()


def _log(message: str) -> None:
    with _print_lock:
        print(message)


_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import screenshot as SS

SCREENSHOT_TIMEOUT_SECONDS = 90


def _resolve_upload_fn(drive_service):
    """Return the Drive upload callable, or None if Drive is unavailable."""
    if not drive_service:
        return None
    try:
        import drive

        return drive.upload
    except ImportError:
        return None


def _screenshot_one(
    audited: dict,
    screenshots_dir: str,
    drive_service,
    drive_folder: str,
) -> dict:
    """
    Take screenshots for all issues belonging to a single service.

    Runs the Playwright work in a dedicated thread with a hard timeout.
    On timeout or error the original issues are returned unchanged so the
    pipeline can continue.
    """
    scraped = audited["scraped"]
    psid = scraped["job"]["psid"]
    issues = audited["issues"]

    if not issues:
        return dict(audited=audited, issues=issues, error="")

    _done = threading.Event()
    result_container = {"issues": issues, "error": ""}

    def _run_playwright():
        try:
            updated_issues = SS.take_screenshots(
                issues=list(issues),
                psid=psid,
                en_url=scraped.get("en_url", ""),
                ar_url=scraped.get("ar_url", ""),
                screenshots_dir=screenshots_dir,
                en_html=scraped.get("en_html", ""),
                ar_html=scraped.get("ar_html", ""),
                drive_service=drive_service,
                drive_folder=drive_folder,
                upload_fn=_resolve_upload_fn(drive_service),
            )
            result_container["issues"] = (
                updated_issues if updated_issues is not None else issues
            )
        except Exception as exc:
            result_container["error"] = str(exc)
        finally:
            _done.set()

    worker = threading.Thread(target=_run_playwright, daemon=True)
    worker.start()
    finished = _done.wait(timeout=SCREENSHOT_TIMEOUT_SECONDS)

    if not finished:
        _log(
            f"    [timeout]  [{psid}] exceeded {SCREENSHOT_TIMEOUT_SECONDS}s - skipping"
        )
        return dict(audited=audited, issues=issues, error="timeout")

    if result_container["error"]:
        _log(f"    [failed]   [{psid}] {result_container['error'][:80]}")
        return dict(audited=audited, issues=issues, error=result_container["error"])

    captured_count = sum(
        1 for issue in result_container["issues"] if issue.get("screenshot")
    )
    _log(f"    [captured] [{psid}] {captured_count}/{len(result_container['issues'])}")
    return dict(audited=audited, issues=result_container["issues"], error="")


def screenshot_agent(state: dict) -> dict:
    """
    Consume pending_screenshot jobs and populate pending_report with results.

    Screenshots are taken sequentially because each Playwright browser instance
    is not safe to share across threads. Returns a state-patch dict.
    """
    jobs = list(state.get("pending_screenshot", []))
    if not jobs:
        return {}

    take_screenshots = state.get("take_screenshots", False)
    screenshots_dir = state.get("screenshots_dir", "screenshots")
    drive_service = state.get("drive_service")
    drive_folder = state.get("drive_folder", "")
    failed = list(state.get("failed", []))

    _log(
        f"\n  -- Screenshot Agent -- {len(jobs)} services "
        f"(timeout={SCREENSHOT_TIMEOUT_SECONDS}s each) --"
    )

    if not take_screenshots or not SS.is_available():
        reason = "disabled" if not take_screenshots else "Playwright not installed"
        _log(f"  -- Screenshots {reason} - skipping --\n")
        ready = [
            dict(audited=audited, issues=audited["issues"], error="")
            for audited in jobs
        ]
        return {
            "pending_screenshot": [],
            "pending_report": state.get("pending_report", []) + ready,
        }

    ready = []
    for index, audited in enumerate(jobs, 1):
        psid = audited["scraped"]["job"]["psid"]
        _log(f"\n  [{index}/{len(jobs)}] {psid}")
        result = _screenshot_one(audited, screenshots_dir, drive_service, drive_folder)
        if result["error"] not in ("", "timeout"):
            failed.append(
                {"psid": psid, "stage": "screenshot", "error": result["error"]}
            )
        ready.append(result)

    total_captured = sum(
        sum(1 for issue in service["issues"] if issue.get("screenshot"))
        for service in ready
    )
    _log(f"\n  -- Screenshot done: {total_captured} total captures --\n")

    return {
        "pending_screenshot": [],
        "pending_report": state.get("pending_report", []) + ready,
        "failed": failed,
    }
