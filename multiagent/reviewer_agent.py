"""
reviewer_agent.py - Feedback-loop quality reviewer.

A second, lightweight AI pass that reads every issue produced by the QA audit
and scores whether the proposed solution is genuinely actionable or vague
filler that slipped past the primary model.

Issues that score below QUALITY_THRESHOLD are either:
  - Upgraded by asking the primary model to rewrite them with a stricter prompt.
  - Flagged with a "needs_review" marker and moved to a separate CSV column so
    a human can inspect them without losing them entirely.

This stage is optional.  It is activated by passing "run_reviewer": True in
the pipeline state.  When disabled the state flows through unchanged.

Reviewer prompt strategy:
  A small, fast model is used here (Groq llama-3.3-70b or Gemini Flash) because
  the task is classification + short rewrite, not deep content analysis.
  Each issue is scored on three axes:
    1. proposed_solution specificity  (is it copy-paste ready, or just a hint?)
    2. issue_placement accuracy       (does the quoted text actually match the issue?)
    3. false_positive risk            (could this be flagged in error?)
  The score is 0-10.  Issues below QUALITY_THRESHOLD (default 6) are recycled.
"""

import sys
import os
import json
import threading
import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed

_print_lock = threading.Lock()


def _log(message: str) -> None:
    with _print_lock:
        print(message)


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


_force_load("ai")
import ai as AI

QUALITY_THRESHOLD = 6  # issues scoring below this are recycled or flagged
MAX_REVIEW_WORKERS = 4  # reviewing is cheaper than auditing — more parallelism

REVIEWER_SYSTEM_PROMPT = """You are a quality-control reviewer for a government web-audit system.
You will receive a single audit issue as JSON.

Score the issue on these three axes (0-10 each):
  1. solution_specificity  — Is proposed_solution copy-paste ready actual text,
     or a vague instruction like "rewrite this" / "improve the wording"?
     10 = full corrected text provided.  0 = only a vague instruction.
  2. placement_accuracy    — Does issue_placement quote real text that directly
     shows the problem described in issue_description?
     10 = exact relevant quote.  0 = unrelated or missing quote.
  3. false_positive_risk   — Could this issue be a misinterpretation of correct
     content?  10 = clearly a real problem.  0 = very likely a false positive.

If solution_specificity < 7, rewrite proposed_solution as actual corrected text.
Use the context from issue_placement and issue_description to infer the correct fix.
Never use placeholders like [audience], [name], [url], N/A, or TBD.

Return ONLY a JSON object — no prose, no markdown:
{
  "solution_specificity":  <int 0-10>,
  "placement_accuracy":    <int 0-10>,
  "false_positive_risk":   <int 0-10>,
  "overall_score":         <int 0-10>,
  "proposed_solution":     "<rewritten solution, or original if already good>",
  "reviewer_note":         "<one sentence explaining any concern, or empty string>"
}"""


def _review_issue(
    issue: dict,
    gemini_key: str,
    groq_key: str,
    openrouter_key: str,
) -> dict:
    """
    Score and optionally rewrite a single issue.
    Returns the issue dict enriched with reviewer fields.
    Always returns the issue — errors demote to needs_review rather than drop.
    """
    issue_json = json.dumps(
        {
            "section": issue.get("section", ""),
            "language": issue.get("language", ""),
            "issue_placement": issue.get("issue_placement", ""),
            "issue_description": issue.get("issue_description", ""),
            "proposed_solution": issue.get("proposed_solution", ""),
        },
        ensure_ascii=False,
    )

    try:
        raw_list = AI._call_with_prompt(
            user_prompt=issue_json,
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            gemini_key=gemini_key,
            groq_key=groq_key,
            openrouter_key=openrouter_key,
        )

        # _call_with_prompt returns a list (audit issues format).
        # The reviewer prompt returns a single JSON object — unwrap it safely.
        # parse_ai_response may wrap a bare object in a list, so handle both shapes.
        if isinstance(raw_list, list) and raw_list:
            review = raw_list[0]
        elif isinstance(raw_list, dict):
            review = raw_list
        elif isinstance(raw_list, list) and not raw_list:
            raise ValueError("reviewer returned empty list — model may have refused")
        else:
            raise ValueError(f"unexpected reviewer response type: {type(raw_list)}")

        overall = int(review.get("overall_score", 0))
        solution = review.get("proposed_solution", "").strip()

        issue["_review_score"] = overall
        issue["_solution_specificity"] = review.get("solution_specificity", 0)
        issue["_placement_accuracy"] = review.get("placement_accuracy", 0)
        issue["_false_positive_risk"] = review.get("false_positive_risk", 0)
        issue["_reviewer_note"] = review.get("reviewer_note", "")
        issue["needs_review"] = overall < QUALITY_THRESHOLD

        # Upgrade solution if the reviewer produced a better one
        if solution and overall >= QUALITY_THRESHOLD:
            issue["proposed_solution"] = solution
        elif solution and issue.get("proposed_solution", "").strip() in (
            "",
            "N/A",
            "TBD",
            "Rewrite this",
            "Improve the wording",
        ):
            issue["proposed_solution"] = solution

    except Exception as exc:
        issue["_review_score"] = -1
        issue["_reviewer_note"] = f"reviewer error: {str(exc)[:80]}"
        issue["needs_review"] = True

    return issue


def _review_service(
    audited: dict,
    gemini_key: str,
    groq_key: str,
    openrouter_key: str,
) -> dict:
    """
    Review all issues for a single service in parallel worker threads.
    Returns the audited dict with issues enriched by reviewer scores.
    """
    issues = list(audited.get("issues", []))
    if not issues:
        return audited

    psid = audited.get("scraped", {}).get("job", {}).get("psid", "?")

    reviewed = []
    with ThreadPoolExecutor(max_workers=MAX_REVIEW_WORKERS) as executor:
        futures = {
            executor.submit(
                _review_issue, issue, gemini_key, groq_key, openrouter_key
            ): issue
            for issue in issues
        }
        for future in as_completed(futures):
            reviewed.append(future.result())

    # Sort by original id to preserve order
    reviewed.sort(key=lambda i: i.get("id", 0))

    flagged = sum(1 for i in reviewed if i.get("needs_review"))
    avg_score = sum(
        i.get("_review_score", 0) for i in reviewed if i.get("_review_score", -1) >= 0
    ) / max(1, sum(1 for i in reviewed if i.get("_review_score", -1) >= 0))
    _log(
        f"    [reviewed]  [{psid}] {len(reviewed)} issues  "
        f"avg_score={avg_score:.1f}  flagged={flagged}"
    )

    result = dict(audited)
    result["issues"] = reviewed
    return result


def reviewer_agent(state: dict) -> dict:
    """
    Consume pending_screenshot items, review all their issues, and pass them on.

    This agent sits between the QA audit and the screenshot stage.  It rewrites
    or flags low-quality issues before screenshots are taken, so the screenshot
    stage operates on the cleaned issue list.

    When "run_reviewer" is False in state the function is a transparent pass-through.
    """
    if not state.get("run_reviewer", False):
        return {}

    jobs = list(state.get("pending_screenshot", []))
    if not jobs:
        return {}

    gemini_key = state.get("gemini_key", "")
    groq_key = state.get("groq_key", "")
    openrouter_key = state.get("openrouter_key", "")

    total_issues = sum(len(j.get("issues", [])) for j in jobs)
    print(
        f"\n  -- Reviewer Agent -- {len(jobs)} services, {total_issues} issues to review --"
    )

    reviewed_jobs = []
    for job in jobs:
        reviewed_jobs.append(_review_service(job, gemini_key, groq_key, openrouter_key))

    needs_review_total = sum(
        sum(1 for i in j.get("issues", []) if i.get("needs_review"))
        for j in reviewed_jobs
    )
    print(
        f"  -- Reviewer done: {needs_review_total}/{total_issues} issues flagged "
        f"for human review --\n"
    )

    return {
        "pending_screenshot": reviewed_jobs,
    }
