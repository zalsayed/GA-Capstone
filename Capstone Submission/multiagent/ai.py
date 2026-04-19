"""
ai.py
================================
Handles all AI provider calls with a hybrid cloud-first / local-fallback routing
strategy and per-provider rate-limit tracking.

Provider order:
  1. Gemini   (cloud — fastest, highest quality)
  2. Groq     (cloud — fast free tier)
  3. OpenRouter (cloud — free tier models)
  4. Ollama   (local — zero rate limits, used when all cloud providers are exhausted)

Hybrid routing:
  Each cloud provider tracks tokens-used-per-minute in a rolling window.
  When a provider is within 90% of its known rate limit it is marked as
  "congested" and the router skips directly to the next provider rather than
  waiting for a 429 response. This avoids wasted latency on retries and
  distributes load proactively.

Key design decisions:
  - Temperature = 0 for maximum consistency between runs
  - Structured checklist prompt — AI works through fixed checks not open discovery
  - Output schema validation handled in rules.py (validate_and_clean)
"""

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


QA_PROMPT_A = """You are a senior bilingual content auditor for Bahrain government e-service pages.
You receive the English (EN) and Arabic (AR) versions of one service page.
Links in the text appear as: anchor text [LINK:url]

TASK: Find real quality issues that confuse, mislead, or block citizens. Output only issues that require action.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS A — CHECKS 1 THROUGH 6
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

══ CHECK 1: SERVICE NAME ══════════════════════════════════════════════
→ Spelling errors in EN or AR name?
→ EN name and AR name mean the same thing?
→ Name longer than 10 words or genuinely unclear? Suggest shorter version in both languages.

══ CHECK 2: SERVICE DESCRIPTION ══════════════════════════════════════
→ EN must start with "This service allows [WHO] to [WHAT]" or "This service enables [WHO] to [WHAT]".
  If it doesn't — write the complete replacement sentence using content from the page. No placeholders.
  GOOD: "This service allows Bahraini citizens to register for quarterly meat subsidy compensation..."
  BAD:  "This service allows [eligible individuals] to [obtain benefits]"  ← never do this
→ AR must start with "تتيح هذه الخدمة" or "تمكّن هذه الخدمة". If not — write full replacement in formal MSA.
→ Does EN explain WHO can use it AND WHAT they get? If either is missing — rewrite both.
→ Do EN and AR descriptions convey the same meaning? If not — flag mismatch, provide corrected AR.
→ Is the description just the service name repeated? Flag as Missing Content, provide full rewrite.

══ CHECK 3: REQUIRED ATTACHMENTS ════════════════════════════════════
→ VAGUE NAMES: Any attachment with a single generic word MUST be flagged:
    "Invoice" → what invoice? from whom? for what?
    "Document" / "Documents" → always vague
    "Certificate" → which certificate?
    "Form" → which form? where to get it?
    "Copy" / "Letter" / "Report" → too vague
  Write the specific replacement using context from the service description.

→ MISLABELED DOWNLOADS: Any item with link text containing "View Document", "View Doc",
  "عرض المستند", or similar "view" wording that links to a downloadable file:
  Flag as: "Mislabeled download: rename to 'Download [Form Name]' and state: type (PDF/DOC),
  pages, and file size. Example: 'Download Registration Form (PDF, 2 pages, 150 KB)'."

→ UNLABELED CONDITIONAL ITEMS: Any attachment that mentions "if", "for government", "in case of",
  "when applicable", "إذا كان", "في حال", "للجهات" but is NOT in the format:
  "[Document] (required for [specific group] only)" → flag and rewrite in that format.

→ BILINGUAL LEAK — EN field has Arabic text:
  Scan the EN attachments for Arabic characters (Arabic Unicode \u0600-\u06ff).
  If found → flag as: "Bilingual leak: untranslated Arabic text in English attachments."
  Provide the full English translation as solution.

→ BILINGUAL LEAK — AR field has English sentences:
  Scan the AR attachments for English sentences (not just abbreviations like PDF, MB, or system names).
  If found → flag as: "Bilingual leak: untranslated English text in Arabic attachments."
  Provide the full Arabic translation as solution.

→ FORM WITHOUT SOURCE: Any item mentioning "fill the form", "ملء الاستمارة", "تعبئة النموذج"
  without saying WHERE to get the form → flag with suggested source.

→ MISSING UPLOAD CONSTRAINTS: If there are 2+ attachment items and NOWHERE in the section
  do the words PDF, JPG, MB, KB, format, صيغة, حجم appear → flag:
  "Missing upload constraints. Add: Accepted formats: PDF, JPG. Maximum file size: 2MB per file."
  NOTE: Skip this if rule engine already caught it (EN015/AR015).

══ CHECK 4: LEGAL REGULATIONS ════════════════════════════════════════
→ Every law, decree, resolution, or ministerial decision must have [LINK:url] after it.
  If any appears as plain text with no link → flag each one separately:
  "Missing hyperlink: '[law name]' → add link to https://www.legalaffairs.gov.bh"
→ EN and AR list the same regulations? Flag any missing from one side.
→ Spelling errors in law names?

══ CHECK 5: FEES ═════════════════════════════════════════════════════
→ Every fee must include amount AND currency (BD/BHD in EN, دينار بحريني/د.ب in AR).
→ "Service cost" should be "Service fee" in EN. "رسوم الخدمة" is the correct AR standard.

══ CHECK 6: PROCESS TIME ════════════════════════════════════════════
→ EN format: "[N] Working Day(s)" or "Immediate". Flag format issues only — never change the number.
→ AR format: "[N] يوم/أيام عمل" or "فوري". Same — flag format, not the value.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULES FOR ALL OUTPUTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. proposed_solution = ready-to-publish corrected text. NEVER write "rewrite this", "N/A", "TBD".
2. Same issue in multiple places → ONE entry, list all placements in issue_placement separated by " | ".
3. DO NOT flag: raw URLs, raw emails, Hamza, Alif Maqsura, Arabic comma, double spaces,
   empty sections, fee mismatch, process time mismatch. These are handled by the rule engine.
4. DO NOT flag fee differences that are the same value in different notation (0.5 BD = 500 فلس).

OUTPUT — raw JSON array only, no prose, no markdown fences:
[
  {
    "id": <integer>,
    "entity": "<service provider name>",
    "service": "<service name>",
    "section": "<allowed value>",
    "language": "<EN|AR|Both>",
    "issue_placement": "<Section>: \"<5-15 word verbatim quote>\"",
    "issue_description": "<what is wrong and why it matters to citizens>",
    "proposed_solution": "<complete corrected text, copy-paste ready>"
  }
]

ALLOWED SECTION VALUES:
Service Name | Service Description | Required Attachments | Legal Regulations |
Fees | Process Time | Service Provider | Service Processes | Service Conditions |
Formatting | Wrong Information | Incomplete Process | User Clarity | Deprecated
"""

QA_PROMPT_B = """You are a senior bilingual content auditor for Bahrain government e-service pages.
You receive the English (EN) and Arabic (AR) versions of one service page.
Links in the text appear as: anchor text [LINK:url]

TASK: Find real quality issues that confuse, mislead, or block citizens. Output only issues that require action.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS B — CHECKS 7 THROUGH 11
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

══ CHECK 7: SERVICE PROVIDER ════════════════════════════════════════
→ EN and AR provider names match in meaning?
→ Spelling correct in both languages?

══ CHECK 8: SERVICE PROCESSES ═══════════════════════════════════════
→ LAST STEP OUTCOME: Does the last step tell the user what happens after submission?
  Must include: will it be reviewed? how long? how will they be notified?
  If not → flag: "The last step does not state the outcome. Add: 'Your application will be reviewed.
  You will be notified via [SMS/email/portal] within [N] working days.'"

→ LOGICAL ORDER: Are steps in the correct sequence?
  Expected: Login → Fill form → Attach documents → Pay fee → Submit → Receive outcome
  If steps are out of order → flag with corrected sequence.

→ CONTRADICTORY CHANNELS: Does the service say it can be completed online/electronically,
  BUT also requires a physical visit, appointment booking, or in-person attendance?
  This is a HIGH SEVERITY issue.
  Flag as: "Contradictory channels: the service states online registration is possible but
  step [X] requires a physical visit. Clarify: is the physical visit mandatory for ALL users,
  or only for those unable to register electronically? If optional, label it explicitly:
  'For users unable to complete registration online, visit [center name].'"

→ EN↔AR STEP MISMATCH: Does EN have steps not in AR, or AR have steps not in EN?
  Flag with the missing translation.

→ MISSING FORM SOURCE: Any step mentioning "fill", "complete", "submit" a form without
  stating WHERE to get it → flag with suggested source.

→ MISSING CONTACT DETAILS: Any step mentioning "contact us", "email", "call" without
  the actual email address or phone number → flag.

→ UNLINKED SYSTEMS: Any step naming an external system, portal, or app without [LINK:url]:
  Known URLs: DUR→https://dur.nhra.bh | Sijilat→https://www.sijilat.bh |
  Mawaeed app→https://www.mawaeed.bh | Bahrain.bh→https://www.bahrain.bh |
  Health→https://www.health.bh | LMRA→https://www.lmra.bh
  Flag each one: "Add hyperlink to [system name]: [url]"

══ CHECK 9: SERVICE CONDITIONS ══════════════════════════════════════
→ CONDITIONS CONTAIN ACTION STEPS? Conditions must be eligibility criteria (WHO can apply),
  not action steps (submit, login, fill). If action steps found → flag as Section Misplacement.

→ EN↔AR MISMATCH: Do EN and AR conditions state the same eligibility criteria?
  If count or meaning differs → flag with corrected translation.

→ BLACK-BOX ELIGIBILITY: Any condition that defers to an external definition without stating the rule?
  Examples that MUST be flagged:
    "as determined by iGA" → what does iGA check? state the actual criteria
    "eligible families" → what makes a family eligible? income threshold? residency?
    "head of family as per iGA records" → who qualifies as head of family? can a single mother apply?
    "permanent resident" → how many days per year constitutes permanent residency?
  Flag as: "Vague eligibility: '[condition]' relies on an undefined external criterion.
  State the specific rule so citizens can self-assess before applying. Example: 'Bahraini citizen
  registered as head of household in iGA records, physically residing in Bahrain for at least
  183 days per year.'"

→ VAGUE RESIDENCY: "resident on the soil of the Kingdom" or similar poetic language
  without a measurable threshold → flag with suggestion to add specific criterion.

══ CHECK 10: ARABIC LANGUAGE QUALITY ════════════════════════════════
→ Non-MSA or colloquial Arabic? Flag with formal MSA rewrite.
→ Word-choice errors that change meaning? (e.g. ملء vs مليء) Flag with correction.
→ Inconsistent terminology across sections? Flag the inconsistency and the standardized term.
→ Informal terms? (السواق → القيادة) Flag with correction.

══ CHECK 11: ENGLISH LANGUAGE QUALITY ═══════════════════════════════
→ Spelling mistakes or typos? Flag with correction.
→ Grammar errors? Flag with corrected sentence.
→ Informal language? ("click here", "don't", "you'll") → flag with formal alternative.
→ Wrong government terminology?
    "CPR" / "Smartcard" → "Identity Card"
    "Minister of Interiors" → "Minister of Interior"
    "Service cost" → "Service fee"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULES FOR ALL OUTPUTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. proposed_solution = ready-to-publish corrected text. NEVER write "rewrite this", "N/A", "TBD".
2. Same issue in multiple places → ONE entry, list all placements separated by " | ".
3. DO NOT flag: raw URLs, raw emails, Hamza, Alif Maqsura, Arabic comma, double spaces,
   empty sections, fee mismatch, process time mismatch. These are handled by the rule engine.

OUTPUT — raw JSON array only, no prose, no markdown fences:
[
  {
    "id": <integer>,
    "entity": "<service provider name>",
    "service": "<service name>",
    "section": "<allowed value>",
    "language": "<EN|AR|Both>",
    "issue_placement": "<Section>: \"<5-15 word verbatim quote>\"",
    "issue_description": "<what is wrong and why it matters to citizens>",
    "proposed_solution": "<complete corrected text, copy-paste ready>"
  }
]

ALLOWED SECTION VALUES:
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


def _inject_flags(system_prompt: str, flags: list) -> str:
    if not flags:
        return system_prompt
    lines = [
        "",
        "",
        "PRE-DETECTED ISSUES - OUTPUT AN ISSUE ENTRY FOR EVERY FLAG BELOW",
        "These patterns were confirmed automatically. Each MUST appear in your JSON output",
        "with a complete issue_description and proposed_solution. Do not skip any.",
        "",
    ]
    for i, f in enumerate(flags):
        lines.append(f"  FLAG {i+1}: {f}")
    lines.append("")
    return system_prompt + "\n".join(lines)


def call_ai_split(
    user_prompt: str = "",
    flags: list = None,
    gemini_key: str = "",
    groq_key: str = "",
    openrouter_key: str = "",
    max_retries: int = 2,
    psid: str = "",
) -> list[dict]:
    """
    Two-pass audit: runs QA_PROMPT_A (checks 1-6) and QA_PROMPT_B (checks 7-11)
    as separate AI calls then merges results.

    Flags from qa_agent._scan_flags() are injected into BOTH system prompts
    so the model is forced to address pre-detected patterns regardless of pass.
    Pass A results are always returned even if Pass B fails.
    """
    if flags is None:
        flags = []
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

    prompt_a = _inject_flags(QA_PROMPT_A, flags)
    prompt_b = _inject_flags(QA_PROMPT_B, flags)
    issues_a, status_a, err_a = _run_pass(
        "Pass A", "name/desc/attachments/regs/fees/time", prompt_a
    )
    issues_b, status_b, err_b = _run_pass(
        "Pass B", "provider/processes/conditions/language", prompt_b
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
