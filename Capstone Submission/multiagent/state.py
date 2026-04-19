from __future__ import annotations
from typing import TypedDict, Any


class ServiceJob(TypedDict):
    psid: str
    name: str
    url: str
    is_eservice: bool


class ScrapedService(TypedDict):
    job: ServiceJob
    en_data: dict
    ar_data: dict
    en_html: str
    ar_html: str
    en_url: str
    ar_url: str
    error: str


class AuditedService(TypedDict):
    scraped: ScrapedService
    issues: list
    error: str


class ScreenshottedService(TypedDict):
    audited: AuditedService
    issues: list
    error: str


class AuditState(TypedDict):
    entity_name: str
    screenshots_dir: str
    drive_folder: str
    gemini_key: str
    groq_key: str
    openrouter_key: str
    drive_service: Any
    take_screenshots: bool

    pending_scrape: list  # list[ServiceJob]
    pending_audit: list  # list[ScrapedService]
    pending_screenshot: list  # list[AuditedService]
    pending_report: list  # list[ScreenshottedService]

    # Output
    completed: list  # list[ScreenshottedService]
    failed: list  # list[dict]
    output_csv: str
    total_issues: int
