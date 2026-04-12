import csv
from datetime import datetime
from pathlib import Path
import re

REPORTED_BY = "Zainab S."
DEFAULT_STATUS = "New Issue"

CSV_COLUMNS = [
    "#",
    "Entity",
    "Page",
    "Issue",
    "Reported By",
    "Assignee",
    "Status",
    "Additional Comments",
]


def _safe_name(name: str, maxlen: int = 40) -> str:
    """Convert entity/service name to a safe filename."""
    name = re.sub(r"[^\w\u0600-\u06FF\s-]", "", str(name or "Unknown"))
    name = re.sub(r"\s+", "_", name.strip())
    return name[:maxlen] or "Unknown"


def _service_cell(service_name: str, service_url: str) -> str:
    """Build an Excel HYPERLINK formula for the service page."""
    name = str(service_name or "").replace('"', "'")
    if service_url:
        safe_url = service_url.replace('"', "%22")
        return f'=HYPERLINK("{safe_url}","{name}")'
    return name


def _issue_cell(placement: str, description: str, solution: str) -> str:
    """
    Combine placement, description, and solution into a single structured cell.

    Format:
        Placement: <text>
        Description: <text>
        Solution: <text>
    """
    parts = []
    if placement:
        parts.append(f"Placement: {placement}")
    if description:
        parts.append(f"Description: {description}")
    if solution:
        parts.append(f"Solution: {solution}")
    return "\n".join(parts)


def _screenshot_cell(screenshot: str) -> str:
    """Return a hyperlink formula for the screenshot, or empty string."""
    if not screenshot:
        return ""
    if screenshot.startswith("https://"):
        safe = screenshot.replace('"', "%22")
        return f'=HYPERLINK("{safe}","View Screenshot")'
    safe = str(screenshot).replace('"', "'")
    return f'=HYPERLINK("{safe}","View Screenshot")'


def _build_rows(issues: list[dict], service_url: str = "") -> list[dict]:
    rows = []
    for iss in issues:
        rows.append(
            {
                "#": iss.get("id", ""),
                "Entity": iss.get("entity", ""),
                "Page": _service_cell(iss.get("service", ""), service_url),
                "Issue": _issue_cell(
                    iss.get("issue_placement", ""),
                    iss.get("issue_description", ""),
                    iss.get("proposed_solution", ""),
                ),
                "Reported By": REPORTED_BY,
                "Assignee": "",
                "Status": DEFAULT_STATUS,
                "Additional Comments": _screenshot_cell(iss.get("screenshot", "")),
            }
        )
    return rows


def write_service_csv(
    issues: list[dict],
    psid: str,
    service_url: str = "",
    reports_dir: str = "reports",
    entity: str = "",
) -> str:
    """
    Write individual CSV for one service.
    Named: {entity}_{psid}.csv — overwritten on each run so no accumulation.
    """
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    entity_safe = _safe_name(entity or (issues[0].get("entity", "") if issues else ""))
    path = out_dir / f"{entity_safe}_{psid}.csv"

    rows = _build_rows(issues, service_url)
    _write(rows, path)
    print(f"  CSV -> {path.name}")
    return str(path)


def write_batch_csv(
    all_issues: list[dict],
    timestamp: str = "",
    reports_dir: str = "reports",
    entity: str = "",
) -> str:
    """
    Write batch CSV for this run and append to the persistent all-batches CSV.
    """
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    entity_name = entity or (all_issues[0].get("entity", "") if all_issues else "")
    entity_safe = _safe_name(entity_name)

    batch_path = out_dir / f"{entity_safe}_batch_{ts}.csv"
    rows = []
    for iss in all_issues:
        rows.append(
            {
                "#": iss.get("id", ""),
                "Entity": iss.get("entity", ""),
                "Page": iss.get(
                    "_service_cell", _service_cell(iss.get("service", ""), "")
                ),
                "Issue": _issue_cell(
                    iss.get("issue_placement", ""),
                    iss.get("issue_description", ""),
                    iss.get("proposed_solution", ""),
                ),
                "Reported By": REPORTED_BY,
                "Assignee": "",
                "Status": DEFAULT_STATUS,
                "Additional Comments": _screenshot_cell(iss.get("screenshot", "")),
            }
        )
    _write(rows, batch_path)
    print(f"  Batch CSV -> {batch_path.name}  ({len(all_issues)} issues)")

    all_batches_path = out_dir / f"{entity_safe}_all_batches.csv"
    file_exists = all_batches_path.exists()
    with open(all_batches_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
    total = sum(1 for _ in open(all_batches_path, encoding="utf-8-sig")) - 1
    print(f"  All-batches CSV -> {all_batches_path.name}  ({total} total rows)")

    return str(batch_path)


def _write(rows: list[dict], path: Path) -> None:
    """Write rows to CSV with UTF-8 BOM (Excel compatible)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
