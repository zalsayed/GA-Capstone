import json
import re
import time
import threading
from collections import deque

import requests

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
]
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
]
OPENROUTER_MODELS = [
    "qwen/qwen3.6-plus:free",
    "qwen/qwen3.6-plus-preview:free",
    "qwen/qwen2.5-72b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]


_RATE_LIMIT_TPM: dict[str, int] = {
    "gemini": 1_000_000,
    "groq": 6_000,
    "openrouter": 20_000,
}
_CONGESTION_THRESHOLD = 0.90


class _RateLimitTracker:
    """Thread-safe rolling-window token counter per provider."""

    def __init__(self):
        self._lock = threading.Lock()
        self._windows: dict[str, "deque[tuple[float, int]]"] = {
            provider: deque() for provider in _RATE_LIMIT_TPM
        }

    def record(self, provider: str, tokens: int) -> None:
        if provider not in self._windows:
            return
        now = time.time()
        with self._lock:
            self._windows[provider].append((now, tokens))

    def is_congested(self, provider: str) -> bool:
        if provider not in _RATE_LIMIT_TPM:
            return False
        limit = _RATE_LIMIT_TPM[provider]
        now = time.time()
        with self._lock:
            window = self._windows[provider]
            while window and now - window[0][0] > 60:
                window.popleft()
            used = sum(tokens for _, tokens in window)
        if used >= limit * _CONGESTION_THRESHOLD:
            print(
                f"\n  [router] {provider} congested ({used:,}/{limit:,} TPM) - routing to next provider"
            )
            return True
        return False

    def usage_summary(self) -> dict[str, int]:
        now = time.time()
        summary = {}
        with self._lock:
            for provider, window in self._windows.items():
                while window and now - window[0][0] > 60:
                    window.popleft()
                summary[provider] = sum(t for _, t in window)
        return summary


_rate_tracker = _RateLimitTracker()


def get_rate_usage() -> dict[str, int]:
    """Return current rolling-window token usage per provider (last 60 seconds)."""
    return _rate_tracker.usage_summary()


QA_PROMPT_A = """You are a government content quality reviewer for the Bahrain.bh Service Catalog.
You will receive English and Arabic versions of a government service page.
Links appear as: descriptive text [LINK:url]

Work through EVERY check below in order. For each check:
  - If the content PASSES -> skip it, do not output anything for it.
  - If the content FAILS -> output one issue entry in the JSON array.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Work through EVERY check below in order. For each check output an issue ONLY if the content fails.
Focus ONLY on checks 1-6 in this pass.

CHECK 1 — SERVICE NAME
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1a. Is the EN service name spelled correctly? Flag any typos.
1b. Is the AR service name spelled correctly? Flag any typos.
1c. Does the EN name match the AR name in meaning? Flag if not.
1d. Is the name overly long (more than 10 words) or unclear?
    If yes — suggest a shorter refined name in BOTH EN and AR.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 2 — SERVICE DESCRIPTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2a. Does the EN description start with "This service allows" or "This service enables"?
    If not — you MUST rewrite it fully. Use the service name, attachments, and processes
    to infer: (1) WHO can use this service (the target audience), (2) WHAT they can do.
    The rewrite must be a complete, ready-to-publish sentence. No placeholders.
    Good example: "This service allows pharmaceutical importers and government hospitals
    to obtain approval for importing medicines and pharmaceutical products through the
    Drug Utilisation Review (DUR) system."
2b. Does the AR description start with "تتيح هذه الخدمة" or "تمكّن هذه الخدمة"?
    If not — rewrite it fully in formal Modern Standard Arabic. No placeholders.
2c. Does the EN description explain BOTH: (1) who can use it AND (2) what they get/can do?
    If either element is missing — rewrite the full description with both elements present.
2d. Does the AR description convey the same meaning as EN?
    If not — flag as Translation Mismatch and provide corrected AR text.
2e. Is the description just the service name repeated or a one-sentence copy of the name?
    Flag as Missing Content and provide a full rewrite.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 3 — REQUIRED ATTACHMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
3a. Does each EN attachment item have a clear, specific name users can act on?
    Vague single-word or generic items MUST be flagged. This is one of the most common issues.
    The item must answer: WHAT invoice? WHAT document? WHAT certificate? WHAT form?
    Vague terms that MUST always be flagged (no exceptions):
      "Invoice"              → Flag: specify what invoice (e.g. "Supplier invoice for the imported medicines")
      "Document"             → Flag: always vague
      "Supporting documents" → Flag: list the specific documents
      "Certificate"          → Flag: specify which certificate
      "Form"                 → Flag: specify which form and where to get it
      "Letter"               → Flag: specify what letter from whom
      "Report"               → Flag: specify what report
    Provide the specific rewrite using context from the service name and description.
3b. Is each attachment that applies only to a specific group of applicants clearly
    labelled as conditional?
    This is a CRITICAL check. Look for ANY attachment that contains phrases like:
      "if the request is for...", "for government...", "in case of...", "when applicable"
    These MUST be explicitly labelled as conditional in this exact format:
      "[Attachment name] (required for [specific group] only)"
    Example: "Purchase order if the request is for government hospitals"
      → Flag: rewrite as "Purchase order (required for government hospital requests only)"
    Flag any attachment that seems to apply to a subset of users but is not labelled as such.
3c. Does each AR attachment item match its EN equivalent in meaning?
    Flag any translation mismatches with corrected AR text.
3d. Does EN have attachments that are missing from AR, or vice versa?
    Flag as Missing Content.
3e. Are any items listed as both required and optional without clarity?
    Flag with suggested clarification.
3f. Does any attachment mention submitting or filling a form WITHOUT specifying where to get it?
    Keywords: "fill the form", "submit the form", "complete the form", "الاستمارة", "ملء النموذج", "تعبئة الاستمارة".
    If found, flag it and suggest specifying the source (e.g. "available at the service center", "downloadable from [URL]", "provided on-site").

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 4 — LEGAL REGULATIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4a. Is every cited law or regulation name a clickable hyperlink [LINK:...]?
    Links appear as: descriptive text [LINK:url]
    MANDATORY: If ANY regulation, decree, law, or ministerial resolution appears as PLAIN TEXT
    with no [LINK:...] after it — you MUST flag it. Do not skip any.
    Flag format: "Missing Hyperlink: Make '[exact regulation name]' a clickable hyperlink
    to the official Bahrain legislation portal: https://www.legalaffairs.gov.bh"
    Flag EVERY unlinked regulation as a separate issue entry.
    Common regulation types to check: Ministerial Resolution, Decree-Law, Law No., Resolution No.
4b. Do EN and AR list the same regulations? Flag any that appear in one but not the other.
4c. Are there spelling errors in law names (EN or AR)? Flag with correction.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 5 — FEES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
5a. Does every EN fee item include the amount AND currency (BD or BHD)?
    Flag any missing amounts or currency labels.
5b. Does every AR fee item include the amount AND currency (دينار بحريني or د.ب)?
    Flag any missing amounts or currency labels.
5c. Is the fee label consistent? "Service cost" should be "Service fee" in EN.
    "رسوم الخدمة" is the correct AR standard term.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 6 — PROCESS TIME
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
6a. Is EN format correct? Should be "[N] Working Day(s)" or "Immediate".
    Do NOT change the value — only flag format or spelling issues.
6b. Is AR format correct? Should be "[N] يوم/أيام عمل" or "فوري".
    Do NOT change the value — only flag format or spelling issues.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROPOSED SOLUTION must be ACTUAL corrected text — never use placeholders like "Rewrite this", "N/A", "TBD".
DEDUPLICATION: Group same-type issues into ONE entry listing all placements separated by " | ".
Do NOT flag: raw emails, raw URLs, double spaces, space before punctuation, Arabic comma, mixed numerals,
  Hamza errors, Alif Maqsura errors, empty sections, process time mismatch, fee amount mismatch.
Do NOT flag fee differences that are the same value written differently (0.5 BD = 500 فلس).
Do NOT flag spacing around colons in fee lines.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT — raw JSON array only, zero prose, zero markdown:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[
  {
    "id": <integer>,
    "entity": "<service provider name>",
    "service": "<service name>",
    "section": "<one of the allowed section values below>",
    "language": "<EN|AR|Both>",
    "issue_placement": "<Section Name>: \"<verbatim quote from page — 5 to 15 words>\"",
    "issue_description": "<clear explanation of what is wrong and why it matters>",
    "proposed_solution": "<actual corrected text in EN and/or AR — copy-paste ready>"
  }
]

ALLOWED SECTION VALUES (use exactly as written):
Service Name | Service Description | Required Attachments | Legal Regulations |
Fees | Process Time | Service Provider | Service Processes | Service Conditions |
Formatting | Wrong Information | Incomplete Process | User Clarity | Deprecated
"""

QA_PROMPT_B = """You are a government content quality reviewer for the Bahrain.bh Service Catalog.
You will receive English and Arabic versions of a government service page.
Links appear as: descriptive text [LINK:url]

Work through EVERY check below in order. For each check:
  - If the content PASSES -> skip it, do not output anything for it.
  - If the content FAILS -> output one issue entry in the JSON array.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Work through EVERY check below in order. For each check output an issue ONLY if the content fails.
Focus ONLY on checks 7-11 in this pass.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 7 — SERVICE PROVIDER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
7a. Does the EN provider name match the AR provider name in meaning?
    If not — flag as Translation Mismatch with correct names in both languages.
7b. Is the provider name spelled correctly in both languages?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 8 — SERVICE PROCESSES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
8a. Does the LAST step clearly tell the user what happens after they submit?
    If not — flag as Incomplete Process with this exact text:
    "Needs Clarification: It is not clear what happens after submission.
    Specify whether the request will be reviewed, how long it takes, and how
    the user will be notified (email, SMS, portal notification)."
8b. Are the steps in logical order? Login -> Fill -> Attach -> Pay -> Submit -> Outcome.
    If not — flag with corrected sequence.
8c. Does EN have steps missing from AR, or AR missing from EN?
    Flag as Translation Mismatch with the missing steps filled in.
8d. Does any step reference a form without saying where to get it?
    Flag: "Needs Clarification: Specify where the user can access or obtain this form."
8e. Does any step mention an email or phone channel without the actual contact details?
    Flag: "Needs Clarification: Provide the [email address / phone number]."
8f. Are there steps listed under wrong channel headers?
    Flag as Section Misplacement with correct placement.
8g. Does any step reference an external system, portal, or platform by name
    (e.g. "DUR system", "Sijilat", "Labour Market Regulatory Authority portal",
    "National Health Portal", "eGovernment portal", "Bahrain.bh") WITHOUT providing
    a clickable link [LINK:url]?
    This MUST be flagged — users cannot access systems without links.
    Flag as: "Needs Linking: Add a hyperlink to the [system name] so users can access
    it directly."
    Known system URLs to suggest:
      DUR system → https://dur.nhra.bh
      Sijilat → https://www.sijilat.bh
      eGovernment portal → https://www.bahrain.bh
      National Health Portal → https://www.health.bh
      Labour Market → https://www.lmra.bh
      Nationality & Passports → https://www.npra.gov.bh
    If the system is not in this list, note the URL is unconfirmed but still flag the missing link.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 9 — SERVICE CONDITIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
9a. Do conditions contain WHO can apply (eligibility, prerequisites)?
    OR do they contain action steps (submit, fill, access, login)?
    Action steps belong in Service Processes — flag as Section Misplacement.
9b. Do EN and AR conditions match in meaning and count? Flag any mismatches.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 10 — LANGUAGE QUALITY (Arabic)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
10a. Is any Arabic text colloquial or non-MSA?
     Flag and provide formal MSA government rewrite.
10b. Are there word-choice errors that change meaning?
     Example: ملء (filling a form) vs مليء (full/rich). Flag with correct word.
10c. Is Arabic terminology consistent across all sections?
     Example: using both "رخص سياقة" and "رخص قيادة" for the same thing.
     Flag as Terminology Inconsistency with the standardized term.
10d. Does any section use informal Arabic terms?
     Example: "السواق" -> "القيادة". Flag with correction.
NOTE: Hamza and Alif Maqsura errors are handled separately — do not repeat them here.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 11 — LANGUAGE QUALITY (English)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
11a. Are there spelling mistakes or typos? Flag with correction.
11b. Are there grammar errors? Flag with corrected sentence.
11c. Is informal language used? (click here, don't, you'll) Flag with formal alternative.
11d. Are government terms used correctly?
     "CPR" / "Smartcard" -> should be "Identity Card"
     "Minister of Interiors" -> should be "Minister of Interior"
     "Service cost" -> should be "Service fee"
NOTE: Raw emails, URLs, double spaces handled separately — do not repeat them here.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROPOSED SOLUTION must be ACTUAL corrected text — never use placeholders like "Rewrite this", "N/A", "TBD".
DEDUPLICATION: Group same-type issues into ONE entry listing all placements separated by " | ".
Do NOT flag: raw emails, raw URLs, double spaces, space before punctuation, Arabic comma, mixed numerals,
  Hamza errors, Alif Maqsura errors, empty sections, process time mismatch, fee amount mismatch.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT — raw JSON array only, zero prose, zero markdown:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[
  {
    "id": <integer>,
    "entity": "<service provider name>",
    "service": "<service name>",
    "section": "<one of the allowed section values below>",
    "language": "<EN|AR|Both>",
    "issue_placement": "<Section Name>: \"<verbatim quote from page — 5 to 15 words>\"",
    "issue_description": "<clear explanation of what is wrong and why it matters>",
    "proposed_solution": "<actual corrected text in EN and/or AR — copy-paste ready>"
  }
]

ALLOWED SECTION VALUES (use exactly as written):
Service Name | Service Description | Required Attachments | Legal Regulations |
Fees | Process Time | Service Provider | Service Processes | Service Conditions |
Formatting | Wrong Information | Incomplete Process | User Clarity | Deprecated
"""

QA_PROMPT = QA_PROMPT_A + QA_PROMPT_B


ESERVICE_QA_PROMPT = """You are a government content quality reviewer for the Bahrain.bh eServices Portal.
You will receive English and Arabic versions of an eService page.
These pages are simpler than Service Catalog pages — they have fewer sections.

Work through EVERY check below in order. For each check:
  - If the content PASSES -> skip it, do not output anything.
  - If the content FAILS -> output one issue entry in the JSON array.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 1 — SERVICE NAME
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1a. Is the EN service name spelled correctly? Flag any typos.
1b. Is the AR service name spelled correctly? Flag any typos.
1c. Do the EN and AR names match in meaning?
1d. Is the name overly long (more than 10 words) or unclear?
    If yes — suggest a shorter refined name in BOTH EN and AR.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 2 — SERVICE DESCRIPTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2a. Does the EN description start with "This service allows" or "This service enables"?
    If not — rewrite it fully. Do NOT use placeholders like [audience].
2b. Does the AR description start with "تتيح هذه الخدمة" or "تمكّن هذه الخدمة"?
    If not — rewrite it fully.
2c. Do EN and AR descriptions match in meaning?
2d. Is either description empty? Flag as missing.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 3 — SERVICE CONDITIONS (if present)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
3a. Are conditions clearly written as eligibility criteria (not action steps)?
3b. Do EN and AR conditions match in meaning and count?
3c. Any spelling or grammar issues?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 4 — REQUIRED ATTACHMENTS (if present)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4a. Does each attachment item have a specific, actionable name?
    Vague items like "Invoice", "Document", "Certificate", "Relevant documents"
    with no qualifier must be flagged. The name must answer: WHAT invoice? WHAT document?
    Provide a specific rewrite in the proposed solution.
4b. Is each attachment that only applies to a specific group labelled as conditional?
    Example: "Purchase order" with no qualifier → flag if it only applies to certain
    applicants. Correct form: "Purchase order (required for government hospital requests only)".
4c. Do EN and AR attachment lists match in meaning and count?
4d. Does any attachment mention submitting or filling a form WITHOUT specifying where to get it?
    Keywords: "fill the form", "submit the form", "complete the form", "الاستمارة", "ملء النموذج", "تعبئة الاستمارة".
    If found, flag it and suggest specifying the source (e.g. "available at the service center", "downloadable from [URL]", "provided on-site").

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 5 — FEES (if present)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
5a. Are fee amounts consistent between EN and AR?
5b. Is currency clearly stated (BD / د.ب)?
5c. Any formatting issues?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 6 — PROCESS TIME (if present)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
6a. Is process time stated in a standard format (e.g. "X Working Days")?
6b. Do EN and AR values match?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 7 — ARABIC LANGUAGE QUALITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
7a. Is Arabic written in Modern Standard Arabic (MSA)?
7b. Any Hamza errors (أ/إ/ا confusion)?
7c. Any Alif Maqsura / Yaa confusion (ى vs ي)?
7d. Any inappropriate use of colloquial Arabic?
7e. Wrong terminology for government/official context?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK 8 — ENGLISH LANGUAGE QUALITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
8a. Any spelling errors?
8b. Any grammatical errors?
8c. Any inconsistent terminology?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DO NOT flag:
- Missing sections that are simply not applicable to this service
- The eService URL format
- Navigation elements or page chrome

IMPORTANT: The rule engine already handles:
  - Raw email/URL detection
  - Skiplino references
  - Double spaces, space before punctuation
  - Arabic comma in Arabic text
Do NOT repeat those.

OUTPUT FORMAT — return ONLY a JSON array, no other text:
[
  {
    "section":           "Service Name",
    "language":          "EN" | "AR" | "Both",
    "issue_placement":   "Service Name: \\"exact text from page\\"",
    "issue_description": "Clear description of what is wrong.",
    "proposed_solution": "Specific fix or rewrite."
  }
]

Return [] if no issues found.

ALLOWED SECTION VALUES (use exactly):
Service Name | Service Description | Service Conditions |
Required Attachments | Fees | Process Time | Service Provider |
Formatting | Wrong Information | User Clarity | Deprecated
"""


def _repair_json(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r"(?m)^```[a-z]*$", "", raw)
    raw = re.sub(r"(?m)^```$", "", raw)
    raw = raw.strip()
    raw = re.sub(r",\s*([}\]])", r"\1", raw)

    out = []
    in_s = False
    esc = False
    for ch in raw:
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            esc = True
            out.append(ch)
            continue
        if ch == '"':
            in_s = not in_s
            out.append(ch)
            continue
        if in_s and ch == "\n":
            out.append("\\n")
            continue
        if in_s and ch == "\t":
            out.append("\\t")
            continue
        out.append(ch)
    raw = "".join(out)

    ob = raw.count("{") - raw.count("}")
    ok = raw.count("[") - raw.count("]")
    if ob > 0 or ok > 0:
        last = max(raw.rfind("}"), 0)
        if last:
            raw = raw[: last + 1]
            ob = raw.count("{") - raw.count("}")
            ok = raw.count("[") - raw.count("]")
        raw += "]" * max(0, ok)
        raw += "}" * max(0, ob)
    return raw


def parse_ai_response(raw: str) -> list[dict]:
    raw = _repair_json(raw)
    m = re.search(r"\[[\s\S]*\]", raw)
    if m:
        try:
            result = json.loads(m.group())
            if isinstance(result, list):
                return result
        except Exception:
            pass
    m2 = re.search(r"\{[\s\S]*\}", raw)
    if m2:
        try:
            result = json.loads(m2.group())
            if "issues" in result:
                return result["issues"]
        except Exception:
            pass
    return []


def _estimate_tokens(text: str) -> int:
    """Rough token estimate used for rate-limit tracking (4 chars per token)."""
    return max(1, len(text) // 4)


# GEMINI
def call_gemini(full_prompt: str, api_key: str) -> list[dict]:
    if "\n\n=== EN PAGE ===" in full_prompt:
        sep = full_prompt.index("\n\n=== EN PAGE ===")
        system_part = full_prompt[:sep].strip()
        content_part = full_prompt[sep:].strip()
    else:
        system_part = ""
        content_part = full_prompt

    if system_part:
        payload = {
            "systemInstruction": {"parts": [{"text": system_part}]},
            "contents": [{"role": "user", "parts": [{"text": content_part}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        }
    else:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        }
    last_err = ""
    for model in GEMINI_MODELS:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model}:generateContent"
        )
        try:
            print(
                f"\r  Gemini -> {model}...                         ", end="", flush=True
            )
            r = requests.post(
                url,
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=120,
            )
            if r.status_code in (403, 404, 429):
                last_err = f"HTTP {r.status_code}"
                time.sleep(1)
                continue
            r.raise_for_status()
            raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            token_count = _estimate_tokens(full_prompt) + _estimate_tokens(raw)
            _rate_tracker.record("gemini", token_count)
            print(f"\r  OK Gemini: {model}" + " " * 30, flush=True)
            return parse_ai_response(raw)
        except Exception as e:
            last_err = str(e)[:80]
            continue
    raise RuntimeError(f"All Gemini models failed. Last: {last_err}")


# GROQ
def call_groq(user_prompt: str, api_key: str) -> list[dict]:
    last_err = ""
    for model in GROQ_MODELS:
        try:
            print(
                f"\r  Groq -> {model}...                         ", end="", flush=True
            )
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": QA_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0,
                    "max_tokens": 8192,
                },
                timeout=120,
            )
            if r.status_code in (400, 404, 429, 503):
                last_err = f"HTTP {r.status_code}"
                time.sleep(2)
                continue
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
            token_count = _estimate_tokens(user_prompt) + _estimate_tokens(raw)
            _rate_tracker.record("groq", token_count)
            print(f"\r  OK Groq: {model}" + " " * 30, flush=True)
            return parse_ai_response(raw)
        except Exception as e:
            last_err = str(e)[:80]
            continue
    raise RuntimeError(f"All Groq models failed. Last: {last_err}")


# OPENROUTER
def call_openrouter(user_prompt: str, api_key: str) -> list[dict]:
    last_err = ""
    for model in OPENROUTER_MODELS:
        try:
            print(
                f"\r  OpenRouter -> {model}...                         ",
                end="",
                flush=True,
            )
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://bahrain.bh",
                    "X-Title": "Bahrain QA Auditor",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": QA_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0,
                    "max_tokens": 8192,
                },
                timeout=120,
            )
            if r.status_code in (402, 404, 429, 503):
                last_err = f"HTTP {r.status_code}"
                time.sleep(2)
                continue
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
            token_count = _estimate_tokens(user_prompt) + _estimate_tokens(raw)
            _rate_tracker.record("openrouter", token_count)
            print(f"\r  OK OpenRouter: {model}" + " " * 30, flush=True)
            return parse_ai_response(raw)
        except Exception as e:
            last_err = str(e)[:80]
            continue
    raise RuntimeError(f"All OpenRouter models failed. Last: {last_err}")


def is_ollama_available() -> bool:
    """Ollama support removed."""
    return False


def call_ai(
    user_prompt: str = "",
    gemini_key: str = "",
    groq_key: str = "",
    openrouter_key: str = "",
    ollama_key: str = "",
    **kwargs,
) -> list[dict]:
    """
    Route a QA audit prompt through the provider chain.

    Resolves legacy kwarg names so callers that pass prompt= or en_content=
    instead of user_prompt= still work correctly.
    """
    final_prompt = user_prompt or kwargs.get("prompt") or kwargs.get("en_content") or ""
    if not final_prompt:
        print(
            f"  [ai] WARNING: empty prompt received. kwargs keys: {list(kwargs.keys())}"
        )

    return _call_with_prompt(
        final_prompt, QA_PROMPT, gemini_key, groq_key, openrouter_key
    )


def call_ai_eservice(
    user_prompt: str = "",
    gemini_key: str = "",
    groq_key: str = "",
    openrouter_key: str = "",
    **kwargs,
) -> list[dict]:
    """Route an eService audit prompt through the provider chain."""
    final_prompt = user_prompt or kwargs.get("formatted_content") or ""
    return _call_with_prompt(
        final_prompt,
        ESERVICE_QA_PROMPT,
        gemini_key,
        groq_key,
        openrouter_key,
    )


def call_ai_split(
    user_prompt: str = "",
    gemini_key: str = "",
    groq_key: str = "",
    openrouter_key: str = "",
    max_retries: int = 2,
    psid: str = "",
) -> list[dict]:
    """
    Two-pass audit: runs QA_PROMPT_A (checks 1-6) and QA_PROMPT_B (checks 7-11)
    as separate AI calls, then merges and returns combined results.

    Returns a tuple-like dict so the pipeline can log pass statuses clearly.
    Each pass retries independently up to max_retries times before giving up.
    Pass A results are always returned even if Pass B fails entirely.
    """
    tag = f"[{psid}]" if psid else "[audit]"

    def _run_pass(pass_label: str, checks_label: str, system_prompt: str) -> tuple:
        """Returns (issues: list, status: str, error: str)"""
        last_err = ""
        for attempt in range(1, max_retries + 1):
            try:
                print(
                    f"\r  {tag} {pass_label} ({checks_label})...{chr(32)*20}",
                    end="",
                    flush=True,
                )
                result = _call_with_prompt(
                    user_prompt, system_prompt, gemini_key, groq_key, openrouter_key
                )
                if result is not None:
                    print(
                        f"\r  {tag} {pass_label} ✓  {len(result)} issues found{chr(32)*20}",
                        flush=True,
                    )
                    return result, "ok", ""
                last_err = "empty response"
            except Exception as e:
                last_err = str(e)[:120]
                if attempt < max_retries:
                    print(
                        f"\r  {tag} {pass_label} attempt {attempt}/{max_retries} failed "
                        f"— retrying in {3 * attempt}s...{chr(32)*15}",
                        flush=True,
                    )
                    time.sleep(3 * attempt)

        print(
            f"\r  {tag} {pass_label} ✗  FAILED after {max_retries} attempts: "
            f"{last_err[:70]}{chr(32)*10}",
            flush=True,
        )
        return [], "failed", last_err

    issues_a, status_a, err_a = _run_pass(
        "Pass A", "name/desc/attachments/regs/fees/time", QA_PROMPT_A
    )
    issues_b, status_b, err_b = _run_pass(
        "Pass B", "provider/processes/conditions/language", QA_PROMPT_B
    )

    a_icon = "✓" if status_a == "ok" else "✗"
    b_icon = "✓" if status_b == "ok" else "✗"
    total = len(issues_a) + len(issues_b)
    print(
        f"\r  {tag} audit done — "
        f"A:{a_icon}{len(issues_a)} B:{b_icon}{len(issues_b)} total:{total}{chr(32)*20}",
        flush=True,
    )

    if status_b == "failed":
        print(
            f"  {tag} ⚠  Pass B failed — checks 7-11 incomplete. "
            f"Run with --resume to retry this service.",
            flush=True,
        )

    return issues_a + issues_b


def _call_with_prompt(
    user_prompt: str,
    system_prompt: str,
    gemini_key: str = "",
    groq_key: str = "",
    openrouter_key: str = "",
) -> list[dict]:
    """
    Hybrid provider router.

    Order: Gemini -> Groq -> OpenRouter -> Ollama (local fallback).

    Each cloud provider is skipped proactively when its rolling token-per-minute
    usage exceeds 90% of its known limit, avoiding wasted round-trips that would
    end in a 429.  Ollama is only attempted if it is reachable.
    """
    errors = []

    if gemini_key:
        if _rate_tracker.is_congested("gemini"):
            errors.append("Gemini: skipped (rate limit congestion)")
        else:
            try:
                return call_gemini(system_prompt + "\n\n" + user_prompt, gemini_key)
            except Exception as e:
                errors.append(f"Gemini: {e}")
                print(f"\r  [router] Gemini failed - trying Groq...{chr(32)*20}")

    if groq_key:
        if _rate_tracker.is_congested("groq"):
            errors.append("Groq: skipped (rate limit congestion)")
        else:
            try:
                return _call_groq_with_prompt(user_prompt, system_prompt, groq_key)
            except Exception as e:
                errors.append(f"Groq: {e}")
                print(f"\r  [router] Groq failed - trying OpenRouter...{chr(32)*15}")

    if openrouter_key:
        if _rate_tracker.is_congested("openrouter"):
            errors.append("OpenRouter: skipped (rate limit congestion)")
        else:
            try:
                return _call_openrouter_with_prompt(
                    user_prompt, system_prompt, openrouter_key
                )
            except Exception as e:
                errors.append(f"OpenRouter: {e}")
                print(f"\r  [router] OpenRouter failed - trying Ollama...{chr(32)*15}")

    raise RuntimeError(
        "All AI providers failed:\n"
        + "\n".join(f"  - {e}" for e in errors)
        + "\n\nOptions to resolve:\n"
        "  Cloud keys : https://aistudio.google.com  |  https://console.groq.com  |  https://openrouter.ai\n"
    )


def _call_groq_with_prompt(
    user_prompt: str, system_prompt: str, api_key: str
) -> list[dict]:
    last_err = ""
    for model in GROQ_MODELS:
        try:
            print(
                f"\r  Groq -> {model}...                         ", end="", flush=True
            )
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0,
                    "max_tokens": 8192,
                    "response_format": {"type": "json_object"},
                },
                timeout=120,
            )
            if r.status_code in (400, 404, 429, 503):
                last_err = f"HTTP {r.status_code}"
                time.sleep(2)
                continue
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
            token_count = _estimate_tokens(user_prompt) + _estimate_tokens(raw)
            _rate_tracker.record("groq", token_count)
            print(f"\r  OK Groq: {model}" + " " * 30, flush=True)
            return parse_ai_response(raw)
        except Exception as e:
            last_err = str(e)[:80]
            continue
    raise RuntimeError(f"All Groq models failed. Last: {last_err}")


def _call_openrouter_with_prompt(
    user_prompt: str, system_prompt: str, api_key: str
) -> list[dict]:
    last_err = ""
    for model in OPENROUTER_MODELS:
        try:
            print(
                f"\r  OpenRouter -> {model}...                         ",
                end="",
                flush=True,
            )
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://bahrain.bh",
                    "X-Title": "Bahrain QA Auditor",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0,
                    "max_tokens": 8192,
                    "response_format": {"type": "json_object"},
                },
                timeout=120,
            )
            if r.status_code in (402, 404, 429, 503):
                last_err = f"HTTP {r.status_code}"
                time.sleep(2)
                continue
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
            token_count = _estimate_tokens(user_prompt) + _estimate_tokens(raw)
            _rate_tracker.record("openrouter", token_count)
            print(f"\r  OK OpenRouter: {model}" + " " * 30, flush=True)
            return parse_ai_response(raw)
        except Exception as e:
            last_err = str(e)[:80]
            continue
    raise RuntimeError(f"All OpenRouter models failed. Last: {last_err}")
