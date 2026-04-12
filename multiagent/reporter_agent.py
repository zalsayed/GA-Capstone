import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from datetime import datetime
from pathlib import Path

import output as O


def reporter_agent(state: dict) -> dict:
    """
    Consume pending_report jobs, write final CSVs, and print a run summary.
    Returns a state-patch dict; the supervisor merges it into the shared state.
    """
    jobs = list(state.get("pending_report", []))
    if not jobs:
        return {}

    entity_name = state.get("entity_name", "")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    failed = state.get("failed", [])

    print(f"\n  -- Reporter Agent -- {len(jobs)} services --")

    all_issues = []
    completed = list(state.get("completed", []))

    for service_result in jobs:
        scraped = service_result["audited"]["scraped"]
        job = scraped["job"]
        psid = job["psid"]
        issues = service_result["issues"]
        en_data = scraped["en_data"]
        en_url = scraped.get("en_url", "")

        entity = en_data.get("service_provider", "") or entity_name
        service_name = en_data.get("service_name", "") or job.get("name", psid)

        for issue in issues:
            issue.setdefault("entity", entity)
            issue.setdefault("service", service_name)
            issue["_service_cell"] = O._service_cell(service_name, en_url)

        if issues:
            O.write_service_csv(
                issues=issues,
                psid=psid,
                service_url=en_url,
                entity=entity,
            )
            print(f"    [done]     [{psid}] {len(issues)} issues")

        all_issues.extend(issues)
        completed.append(service_result)

    batch_csv = ""
    if all_issues:
        batch_csv = O.write_batch_csv(
            all_issues=all_issues,
            timestamp=timestamp,
            entity=entity_name,
        )

    screenshot_count = sum(1 for issue in all_issues if issue.get("screenshot"))
    print(f"\n  Services completed : {len(completed)}")
    print(f"  Services failed    : {len(failed)}")
    print(f"  Total issues       : {len(all_issues)}")
    print(f"  Screenshots        : {screenshot_count}")
    if batch_csv:
        print(f"  Batch report       : {Path(batch_csv).name}")

    if failed:
        print("\n  Failed services:")
        for entry in failed:
            print(f"    [{entry['stage']}] {entry['psid']}: {entry['error'][:70]}")

    return {
        "pending_report": [],
        "completed": completed,
        "output_csv": batch_csv,
        "total_issues": len(all_issues),
    }
