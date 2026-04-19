"""
core/jira_integration.py — Real Jira ticket creation from defect reports.
Requires JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY in environment.
"""

import requests
import json
import pandas as pd
from typing import Optional
from core.config import get_config


class JiraClient:
    def __init__(self):
        cfg = get_config()
        self.url = cfg.jira_url.rstrip("/")
        self.auth = (cfg.jira_email, cfg.jira_api_token)
        self.project_key = cfg.jira_project_key
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.auth[0] and self.auth[1])

    def test_connection(self) -> tuple[bool, str]:
        """Test Jira connectivity. Returns (success, message)."""
        if not self.is_configured:
            return False, "Jira not configured (missing URL, email, or token)."
        try:
            r = requests.get(
                f"{self.url}/rest/api/3/myself",
                auth=self.auth, headers=self.headers, timeout=10
            )
            if r.status_code == 200:
                name = r.json().get("displayName", "unknown")
                return True, f"Connected as {name}"
            return False, f"Auth failed: {r.status_code}"
        except Exception as e:
            return False, f"Connection error: {e}"

    def create_bug(self, defect: dict) -> tuple[Optional[str], str]:
        """
        Create a Jira bug from a defect dict.
        Returns (ticket_key, message).
        """
        if not self.is_configured:
            return None, "Jira not configured."

        severity_priority_map = {
            "Critical": "Highest",
            "High": "High",
            "Medium": "Medium",
            "Low": "Low",
        }

        description = {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": "Summary"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": defect.get("Description", "")}],
                },
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": "Expected Result"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": defect.get("Expected", "")}],
                },
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": "Actual Result"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": defect.get("Actual", "")}],
                },
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": "Root Cause Analysis"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": f"Category: {defect.get('Root_Cause', '')} — {defect.get('Root_Cause_Analysis', '')}"}],
                },
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": "Proposed Fix"}],
                },
                {
                    "type": "codeBlock",
                    "content": [{"type": "text", "text": defect.get("Proposed_Fix", "")}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": f"Test Case: {defect.get('TC_ID', '')} | Module: {defect.get('Module', '')} | Screenshot: {defect.get('Screenshot', 'N/A')}"}],
                },
            ],
        }

        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": defect.get("Summary", defect.get("Description", "Defect")[:100]),
                "description": description,
                "issuetype": {"name": "Bug"},
                "priority": {"name": severity_priority_map.get(defect.get("Severity", "Medium"), "Medium")},
                "labels": [
                    "req2defect",
                    f"module-{defect.get('Module', '').lower().replace(' ', '-')}",
                    defect.get("TC_ID", "").lower(),
                ],
                "components": [{"name": defect.get("Module", "")}] if defect.get("Module") else [],
            }
        }

        try:
            r = requests.post(
                f"{self.url}/rest/api/3/issue",
                auth=self.auth,
                headers=self.headers,
                json=payload,
                timeout=15,
            )
            if r.status_code in (200, 201):
                key = r.json().get("key", "UNKNOWN")
                return key, f"Created {key}"
            return None, f"Failed ({r.status_code}): {r.text[:200]}"
        except Exception as e:
            return None, f"Error: {e}"

    def push_defects(self, defects_df: pd.DataFrame) -> pd.DataFrame:
        """
        Push all Open defects to Jira.
        Returns updated DataFrame with JIRA_Ticket column filled.
        """
        df = defects_df.copy()
        if "JIRA_Ticket" not in df.columns:
            df["JIRA_Ticket"] = ""

        for idx, row in df[df.get("Status", pd.Series(["Open"] * len(df))) == "Open"].iterrows():
            key, msg = self.create_bug(row.to_dict())
            if key:
                df.at[idx, "JIRA_Ticket"] = key

        return df


def get_jira_client() -> JiraClient:
    return JiraClient()
