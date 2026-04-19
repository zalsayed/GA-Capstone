import sys
import os
import json
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from langgraph.graph import StateGraph, END

    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False

from scraper_agent import scraper_agent
from qa_agent import qa_agent
from screenshot_agent import screenshot_agent
from reporter_agent import reporter_agent
from reviewer_agent import reviewer_agent
from agent_iq import AgentIQ

CHECKPOINT_DIR = "reports"
CHECKPOINT_FILE = os.path.join(CHECKPOINT_DIR, ".checkpoint_v2.json")

MAX_RETRIES = 2
RETRY_BASE_DELAY = 5
MAX_PIPELINE_STEPS = 30

_checkpoint_lock = threading.Lock()


def _save_checkpoint(state: dict, stage: str) -> None:
    """Persist a slim snapshot of in-flight state after each stage."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)

    snapshot = {
        "stage": stage,
        "pending_screenshot": state.get("pending_screenshot", []),
        "pending_report": state.get("pending_report", []),
        "completed": state.get("completed", []),
        "failed": state.get("failed", []),
        "entity_name": state.get("entity_name", ""),
        "drive_folder": state.get("drive_folder", ""),
        "screenshots_dir": state.get("screenshots_dir", ""),
    }

    for queue_key in ("pending_screenshot", "pending_report", "completed"):
        for item in snapshot.get(queue_key, []):
            try:
                item["audited"]["scraped"]["en_html"] = ""
                item["audited"]["scraped"]["ar_html"] = ""
            except (KeyError, TypeError):
                pass

    with _checkpoint_lock:
        with open(CHECKPOINT_FILE, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=2, default=str)

    print(f"  [checkpoint] saved - stage={stage}")


def load_checkpoint() -> dict | None:
    """Return saved state or None if no checkpoint exists."""
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    with _checkpoint_lock:
        try:
            with open(CHECKPOINT_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            print(f"  [checkpoint] resuming from stage={data.get('stage')}")
            return data
        except Exception as exc:
            print(f"  [checkpoint] load failed: {exc}")
            return None


def clear_checkpoint() -> None:
    with _checkpoint_lock:
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)


def _extract_failed_psids(state: dict, stage: str) -> list[str]:
    """Return psids that failed in the given stage during the last agent run."""
    return [
        entry["psid"]
        for entry in state.get("failed", [])
        if entry.get("stage") == stage
    ]


def _retry_failed_services(
    state: dict,
    stage: str,
    agent_fn,
    iq: AgentIQ,
    retry_number: int,
) -> dict:
    """
    Re-run agent_fn for services that failed in stage.

    Pulls matching failed entries out of state["failed"], builds a minimal
    retry state with those jobs in the correct input queue, runs the agent,
    and merges the results back into the main state.
    """
    failed_this_stage = [e for e in state.get("failed", []) if e.get("stage") == stage]
    if not failed_this_stage:
        return state

    delay = RETRY_BASE_DELAY * (2 ** (retry_number - 1))
    print(
        f"\n  [retry] attempt {retry_number}/{MAX_RETRIES} for "
        f"{len(failed_this_stage)} failed {stage} service(s) "
        f"(back-off {delay}s)..."
    )
    time.sleep(delay)

    input_queue_key = {
        "scrape": "pending_scrape",
        "audit": "pending_audit",
    }.get(stage, f"pending_{stage}")

    retry_items = []
    for entry in failed_this_stage:
        item = (
            entry.get("scraped_data")
            or entry.get("job_data")
            or {"psid": entry["psid"]}
        )
        retry_items.append(item)

    retry_state = {
        **{k: v for k, v in state.items() if not k.startswith("pending_")},
        input_queue_key: retry_items,
        "failed": [e for e in state.get("failed", []) if e.get("stage") != stage],
    }

    patch = iq.run(
        f"{stage}_retry_{retry_number}",
        agent_fn,
        retry_state,
        retries=retry_number,
    )

    merged = dict(state)
    for key, value in patch.items():
        if isinstance(value, list) and key in merged and isinstance(merged[key], list):
            merged[key] = merged[key] + value
        else:
            merged[key] = value

    newly_failed = [e for e in patch.get("failed", []) if e.get("stage") == stage]
    other_failed = [e for e in state.get("failed", []) if e.get("stage") != stage]
    merged["failed"] = other_failed + newly_failed

    recovered = len(failed_this_stage) - len(newly_failed)
    print(f"  [retry] recovered {recovered}/{len(failed_this_stage)} service(s)")
    return merged


def _next_stage(state: dict) -> str:
    if state.get("pending_scrape"):
        return "scraper"
    if state.get("pending_audit"):
        return "qa"
    if (
        state.get("pending_screenshot")
        and state.get("run_reviewer")
        and not state.get("_reviewer_done")
    ):
        return "reviewer"
    if state.get("pending_screenshot"):
        return "screenshot"
    if state.get("pending_report"):
        return "reporter"
    return "__end__"


def run_pipeline(initial_state: dict) -> dict:
    """
    Orchestrate the four stage pipeline with retries and Agent IQ instrumentation.

    Stages execute sequentially parallelism lives inside each agent.
    A stage crash is caught and logged without killing the run.
    Failed services are retried with exponential back-off before being
    moved to the permanent failed list.
    An Agent IQ report is printed and saved at the end of every run.
    """
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    trace_path = os.path.join(CHECKPOINT_DIR, f"trace_{_timestamp()}.json")
    iq = AgentIQ(trace_file=trace_path)
    state = dict(initial_state)

    for step in range(1, MAX_PIPELINE_STEPS + 1):
        stage = _next_stage(state)

        if stage == "__end__":
            clear_checkpoint()
            break

        if step == MAX_PIPELINE_STEPS:
            print(
                f"  [supervisor] safety step limit ({MAX_PIPELINE_STEPS}) reached - stopping"
            )
            break

        if stage == "scraper":
            n = len(state.get("pending_scrape", []))
            print(f"\n  [supervisor] step {step}: scrape ({n} services)")
            try:
                state.update(iq.run("scraper", scraper_agent, state))
            except Exception as exc:
                print(f"  [supervisor] scraper crashed: {exc}")

            for attempt in range(1, MAX_RETRIES + 1):
                if not _extract_failed_psids(state, "scrape"):
                    break
                state = _retry_failed_services(
                    state, "scrape", scraper_agent, iq, attempt
                )

            _save_checkpoint(state, "scraped")

        elif stage == "qa":
            n = len(state.get("pending_audit", []))
            print(f"\n  [supervisor] step {step}: audit ({n} services)")
            _print_rate_usage()
            try:
                state.update(iq.run("qa", qa_agent, state))
            except Exception as exc:
                print(f"  [supervisor] qa_agent crashed: {exc}")

            for attempt in range(1, MAX_RETRIES + 1):
                if not _extract_failed_psids(state, "audit"):
                    break
                state = _retry_failed_services(state, "audit", qa_agent, iq, attempt)

            _save_checkpoint(state, "audited")

        elif stage == "screenshot":
            n = len(state.get("pending_screenshot", []))
            print(f"\n  [supervisor] step {step}: screenshot ({n} services)")
            try:
                state.update(iq.run("screenshot", screenshot_agent, state))
            except Exception as exc:
                print(f"  [supervisor] screenshot_agent crashed: {exc}")

            _save_checkpoint(state, "screenshotted")

        elif stage == "reporter":
            n = len(state.get("pending_report", []))
            print(f"\n  [supervisor] step {step}: report ({n} services)")
            try:
                state.update(iq.run("reporter", reporter_agent, state))
            except Exception as exc:
                print(f"  [supervisor] reporter_agent crashed: {exc}")

    iq.report()
    return state


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _print_rate_usage() -> None:
    """Print current cloud provider token usage so the operator can see headroom."""
    try:
        from ai import get_rate_usage, _RATE_LIMIT_TPM

        usage = get_rate_usage()
        if any(v > 0 for v in usage.values()):
            print("  [rate usage] last 60s:")
            for provider, used in usage.items():
                limit = _RATE_LIMIT_TPM.get(provider, 0)
                pct = f"{used / limit:.0%}" if limit else "n/a"
                print(f"    {provider:<14} {used:>8,} / {limit:>10,} TPM  ({pct})")
    except Exception:
        pass
