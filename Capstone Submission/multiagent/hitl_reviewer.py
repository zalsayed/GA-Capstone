import sys
import os
import csv
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_GT_PATH = os.path.join(_HERE, "ground_truth.csv")

_GT_COLS = [
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

_ALLOWED_SECTIONS = [
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
    "Deprecated",
]


def _hr():
    print("─" * 64)


def _extract_psid(issue: dict) -> str:
    for field in ("service", "issue_placement", "_service_cell"):
        val = issue.get(field, "") or ""
        m = re.search(r"[?&]psID=(\d+)", val, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r"[?&]esID=(\d+)", val, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def _prompt(label: str, default: str = "") -> str:
    if default:
        val = input(f"  {label} [{default[:60]}]: ").strip()
        return val if val else default
    return input(f"  {label}: ").strip()


def _save_gt(rows: list):
    exists = os.path.exists(_GT_PATH)
    with open(_GT_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_GT_COLS)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def _build_gt_row(
    issue: dict, status: str, severity: int, desc: str, notes: str, entity: str
) -> dict:
    return {
        "psid": _extract_psid(issue),
        "entity": issue.get("entity", entity),
        "service": issue.get("service", ""),
        "section": issue.get("section", ""),
        "language": issue.get("language", ""),
        "issue_type": issue.get("rule_id", "ai_detected"),
        "issue_summary": desc[:80],
        "is_valid": 0 if status == "rejected" else 1,
        "severity": severity,
        "found_by_rules": 1 if issue.get("rule_id") else 0,
        "found_by_ai": 0 if issue.get("rule_id") else 1,
        "found_by_hybrid": 1,
        "source_file": "cli_hitl",
        "notes": notes,
    }


def _write_final_csv(
    issues: list, reviewed: dict, added: list, original_path: str, entity: str
) -> str:
    from output import _issue_cell, _service_cell, CSV_COLUMNS, _write

    out_path = original_path.replace(".csv", "_reviewed.csv")
    rows = []
    for iss in issues:
        idx = str(iss.get("id", ""))
        rev = reviewed.get(idx, {})
        if rev.get("status") == "rejected":
            continue
        desc = rev.get("description", iss.get("issue_description", ""))
        sol = rev.get("solution", iss.get("proposed_solution", ""))
        rows.append(
            {
                "#": iss.get("id", ""),
                "Entity": iss.get("entity", entity),
                "Page": iss.get("_service_cell", iss.get("service", "")),
                "Issue": _issue_cell(iss.get("issue_placement", ""), desc, sol),
                "Reported By": "Zainab S.",
                "Assignee": "",
                "Status": rev.get("status", "New Issue")
                .replace("accepted", "New Issue")
                .replace("edited", "Edited")
                .replace("rejected", "Rejected"),
                "Additional Comments": iss.get("screenshot", ""),
            }
        )

    for i, extra in enumerate(added, start=len(rows) + 1):
        rows.append(
            {
                "#": i,
                "Entity": entity,
                "Page": extra.get("service", ""),
                "Issue": _issue_cell(
                    extra.get("issue_placement", ""),
                    extra.get("issue_description", ""),
                    extra.get("proposed_solution", ""),
                ),
                "Reported By": "Zainab S.",
                "Assignee": "",
                "Status": "New Issue (Added by reviewer)",
                "Additional Comments": "",
            }
        )

    from pathlib import Path

    _write(rows, Path(out_path))
    return out_path


def run_review(issues: list, entity: str, original_csv_path: str = "") -> dict:
    if not issues:
        print("  No issues to review.")
        return {"reviewed": {}, "added": []}

    print(f"\n{'='*64}")
    print(f"  HUMAN REVIEW — {len(issues)} issue(s) · entity: {entity}")
    print(f"{'='*64}")
    print("  Commands: a=accept  e=edit  r=reject  s=skip  q=quit & save")
    print(f"{'='*64}\n")

    reviewed = {}
    added = []
    gt_rows = []

    for i, iss in enumerate(issues):
        idx = str(iss.get("id", i + 1))
        section = iss.get("section", "")
        lang = iss.get("language", "")
        source = iss.get("rule_id", "AI")
        place = iss.get("issue_placement", "")
        desc = iss.get("issue_description", "")
        sol = iss.get("proposed_solution", "")

        _hr()
        print(f"  [{i+1}/{len(issues)}]  {section}  ·  {lang}  ·  {source}")
        print(f"  Placement : {place[:100]}")
        print(f"  Description: {desc[:120]}")
        print(f"  Solution  : {sol[:120]}")
        print()

        while True:
            cmd = input("  [a/e/r/s/q] → ").strip().lower()

            if cmd == "q":
                print(f"\n  Saving and exiting — {i} of {len(issues)} reviewed.")
                _save_gt(gt_rows)
                out = (
                    _write_final_csv(issues, reviewed, added, original_csv_path, entity)
                    if original_csv_path
                    else ""
                )
                if out:
                    print(f"  Reviewed CSV → {out}")
                if gt_rows:
                    print(f"  {len(gt_rows)} correction(s) saved to {_GT_PATH}")
                return {"reviewed": reviewed, "added": added}

            if cmd == "s":
                break

            if cmd == "a":
                sev = _get_severity()
                notes = input("  Notes (optional): ").strip()
                reviewed[idx] = {
                    "status": "accepted",
                    "severity": sev,
                    "description": desc,
                    "solution": sol,
                    "notes": notes,
                }
                gt_rows.append(_build_gt_row(iss, "accepted", sev, desc, notes, entity))
                break

            if cmd == "e":
                print("  Edit — press Enter to keep current value.")
                new_desc = _prompt("Description", desc)
                new_sol = _prompt("Solution", sol)
                sev = _get_severity()
                notes = input("  Notes (optional): ").strip()
                reviewed[idx] = {
                    "status": "edited",
                    "severity": sev,
                    "description": new_desc,
                    "solution": new_sol,
                    "notes": notes,
                }
                gt_rows.append(
                    _build_gt_row(iss, "edited", sev, new_desc, notes, entity)
                )
                break

            if cmd == "r":
                notes = input("  Reason (optional): ").strip()
                sev = _get_severity()
                reviewed[idx] = {
                    "status": "rejected",
                    "severity": sev,
                    "description": desc,
                    "solution": sol,
                    "notes": notes,
                }
                gt_rows.append(_build_gt_row(iss, "rejected", sev, desc, notes, entity))
                break

            print("  Enter a, e, r, s, or q.")

    _hr()
    print(f"\n  All {len(issues)} issues reviewed.")
    print("  Add any issues the tool missed? (press Enter to skip)")

    while True:
        cmd = input("  Add missed issue? [y/N] → ").strip().lower()
        if cmd != "y":
            break
        extra = _add_issue_manually(entity)
        if extra:
            added.append(extra)
            gt_rows.append(
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
                    "source_file": "cli_hitl",
                    "notes": "Added by reviewer",
                }
            )
            print(f"  Issue added. ({len(added)} total added)")

    _save_gt(gt_rows)
    out = ""
    if original_csv_path:
        out = _write_final_csv(issues, reviewed, added, original_csv_path, entity)

    _hr()
    accepted = sum(1 for v in reviewed.values() if v["status"] == "accepted")
    edited = sum(1 for v in reviewed.values() if v["status"] == "edited")
    rejected = sum(1 for v in reviewed.values() if v["status"] == "rejected")
    skipped = len(issues) - len(reviewed)

    print(f"  Review complete:")
    print(f"    Accepted  : {accepted}")
    print(f"    Edited    : {edited}")
    print(f"    Rejected  : {rejected}  (removed from output)")
    print(f"    Skipped   : {skipped}  (kept as-is)")
    print(f"    Added     : {len(added)}")
    if out:
        print(f"  Reviewed CSV → {out}")
    if gt_rows:
        print(f"  {len(gt_rows)} correction(s) appended → {_GT_PATH}")
    _hr()

    return {"reviewed": reviewed, "added": added}


def _get_severity() -> int:
    while True:
        s = input(
            "  Severity [1=Minor / 2=Moderate / 3=Critical] (default 2): "
        ).strip()
        if s == "":
            return 2
        if s in ("1", "2", "3"):
            return int(s)
        print("  Enter 1, 2, or 3.")


def _add_issue_manually(entity: str) -> dict:
    print()
    print("  Available sections:")
    for i, s in enumerate(_ALLOWED_SECTIONS, 1):
        print(f"    {i:2d}. {s}")
    sec_input = input("  Section number: ").strip()
    try:
        section = _ALLOWED_SECTIONS[int(sec_input) - 1]
    except Exception:
        print("  Invalid — skipping.")
        return {}

    lang = input("  Language [EN/AR/Both]: ").strip() or "EN"
    place = input("  Placement (quote from page): ").strip()
    desc = input("  Description: ").strip()
    sol = input("  Solution: ").strip()
    sev = _get_severity()

    if not desc:
        print("  Description required — skipping.")
        return {}

    return {
        "section": section,
        "language": lang,
        "issue_placement": place,
        "issue_description": desc,
        "proposed_solution": sol,
        "severity": sev,
        "rule_id": "HUMAN",
        "entity": entity,
        "service": "",
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="Human-in-the-loop reviewer for QA audit CSV outputs."
    )
    p.add_argument("--csv", required=True, help="Audit CSV to review")
    p.add_argument("--entity", default="", help="Entity name")
    args = p.parse_args()

    if not os.path.exists(args.csv):
        print(f"File not found: {args.csv}")
        sys.exit(1)

    sys.path.insert(0, _HERE)
    _force_load_output = True
    try:
        import output
    except ImportError:
        print("Warning: output.py not found — reviewed CSV will not be written.")
        _force_load_output = False

    issues_raw = []
    with open(args.csv, encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), 1):
            issue_cell = row.get("Issue", "")
            placement = description = solution = ""
            for line in issue_cell.split("\n"):
                if line.startswith("Placement:"):
                    placement = line[len("Placement:") :].strip()
                elif line.startswith("Description:"):
                    description = line[len("Description:") :].strip()
                elif line.startswith("Solution:"):
                    solution = line[len("Solution:") :].strip()
            if not description and not placement:
                continue
            issues_raw.append(
                {
                    "id": i,
                    "entity": row.get("Entity", args.entity),
                    "service": row.get("Page", ""),
                    "_service_cell": row.get("Page", ""),
                    "section": "",
                    "language": "",
                    "rule_id": "",
                    "issue_placement": placement,
                    "issue_description": description,
                    "proposed_solution": solution,
                    "screenshot": row.get("Additional Comments", ""),
                }
            )

    run_review(issues_raw, args.entity or "Unknown", original_csv_path=args.csv)
