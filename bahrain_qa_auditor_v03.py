"""
╔══════════════════════════════════════════════════════════════════════╗
║  SFB - Health Checkup Autonomus Tool  —  v0.3                        ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝

SETUP:
    pip install requests beautifulsoup4 playwright google-api-python-client
    pip install google-auth google-auth-httplib2
    playwright install chromium

PROVIDERS (set any):
    export GEMINI_API_KEY=AIza...
    export GROQ_API_KEY=gsk_...
    export OPENROUTER_API_KEY=sk-or-...

GOOGLE DRIVE SETUP (for screenshot uploads):
    1. console.cloud.google.com → New Project
    2. Enable Google Drive API
    3. IAM → Service Accounts → Create → Download JSON key
    4. Share your Drive folder with the service account email
    5. Copy the folder ID from the Drive URL

USAGE:
    python3 bahrain_qa_auditor_v03.py --psid 1898 --key AIza...
    python3 bahrain_qa_auditor_v03.py --psid 1898 --key AIza... --screenshots
    python3 bahrain_qa_auditor_v03.py --psid 1898 --key AIza... --screenshots \\
        --drive-key service_account.json --drive-folder FOLDER_ID
    python3 bahrain_qa_auditor_v03.py --psid 1898 2154 --key AIza... --screenshots
"""

import csv, json, re, sys, time, os, argparse, requests, tempfile
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── dependencies ─────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False
    print(
        "Playwright not installed — using requests for scraping (Note that JS sections may be incomplete)"
    )
    print("   Fix: pip install playwright && playwright install chromium\n")

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    GDRIVE_OK = True
except ImportError:
    GDRIVE_OK = False


# ─────────────────────────────────────────────────────────────
# ANSI COLORS
# ─────────────────────────────────────────────────────────────
def _c(code):
    return f"\033[{code}m"


RST = _c(0)
BLD = _c(1)
DIM = _c(2)


def gold(s):
    return f"{BLD}{_c('38;5;220')}{s}{RST}"


def green(s):
    return f"{BLD}{_c('38;5;84')}{s}{RST}"


def red(s):
    return f"{BLD}{_c('38;5;203')}{s}{RST}"


def orange(s):
    return f"{BLD}{_c('38;5;215')}{s}{RST}"


def cyan(s):
    return f"{BLD}{_c('38;5;117')}{s}{RST}"


def grey(s):
    return f"{DIM}{_c('38;5;245')}{s}{RST}"


def blue(s):
    return f"{BLD}{_c('38;5;75')}{s}{RST}"


# CONSTANTS
BASE_URL = (
    "https://www.bahrain.bh/wps/portal/{lang}/BNP/"
    "ServicesCatalogue/GSX-UI-PServiceDetails?psID={psid}"
)

CSV_COLUMNS = [
    "ID",
    "Entity",
    "Service",
    "Issue Placement",
    "Issue Description",
    "Proposed Solution/Suggestion",
    "Screenshot",
]

# Provider models
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
    "qwen/qwen2.5-72b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
]

# Section headings for screenshot navigation (EN and AR) # To be added missing 2 once the portal is up
SECTION_HEADINGS = {
    "Service Name": {"en": "Service Name", "ar": "اسم الخدمة"},
    "Service Description": {"en": "Service Description", "ar": "وصف الخدمة"},
    "Required Attachments": {"en": "Required Attachments", "ar": "المستندات المطلوبة"},
    "Legal Regulations": {"en": "Legal Regulations", "ar": "الأدوات القانونية"},
    "Fees": {"en": "Fees", "ar": "الرسوم"},
    "Process Time": {"en": "Process Time", "ar": "وقت الإنجاز"},
    "Service Provider": {"en": "Service Provider", "ar": "الجهة المقدمة للخدمة"},
    "Service Processes": {"en": "Service Processes", "ar": "خطوات تقديم الخدمة"},
    "Service Conditions": {"en": "Service Conditions", "ar": "شروط الخدمة"},
    "Linguistic": {"en": "Service Description", "ar": "وصف الخدمة"},
    "Formatting": {"en": "Service Description", "ar": "وصف الخدمة"},
    "Wrong Information": {"en": "Service Description", "ar": "وصف الخدمة"},
    "Incomplete Process": {"en": "Service Processes", "ar": "خطوات تقديم الخدمة"},
    "User Clarity": {"en": "Service Processes", "ar": "خطوات تقديم الخدمة"},
    "Deprecated": {"en": "Service Processes", "ar": "خطوات تقديم الخدمة"},
}

SECTION_H3 = {
    "en": {
        "Required Attachments": "required_attachments",
        "Legal Regulations": "legal_regulations",
        "Fees": "fees",
        "Process Time": "process_time",
        "Service Provider": "service_provider",
        "Service Processes": "service_processes",
        "Service Conditions": "service_conditions",
    },
    "ar": {
        "المستندات المطلوبة": "required_attachments",
        "الأدوات القانونية": "legal_regulations",
        "الرسوم": "fees",
        "وقت الإنجاز": "process_time",
        "الجهة المقدمة للخدمة": "service_provider",
        "خطوات تقديم الخدمة": "service_processes",
        "شروط الخدمة": "service_conditions",
    },
}

SECTION_LABELS = {
    "en": {
        "service_name": "SERVICE NAME",
        "service_description": "SERVICE DESCRIPTION",
        "required_attachments": "REQUIRED ATTACHMENTS",
        "legal_regulations": "LEGAL REGULATIONS",
        "fees": "FEES",
        "process_time": "PROCESS TIME",
        "service_provider": "SERVICE PROVIDER",
        "service_processes": "SERVICE PROCESSES",
        "service_conditions": "SERVICE CONDITIONS",
    },
    "ar": {
        "service_name": "اسم الخدمة",
        "service_description": "وصف الخدمة",
        "required_attachments": "المستندات المطلوبة",
        "legal_regulations": "الأدوات القانونية",
        "fees": "الرسوم",
        "process_time": "وقت الإنجاز",
        "service_provider": "الجهة المقدمة للخدمة",
        "service_processes": "خطوات تقديم الخدمة",
        "service_conditions": "شروط الخدمة",
    },
}

ALLOWED_SECTIONS = list(SECTION_HEADINGS.keys())


# ─────────────────────────────────────────────────────────────
# QA PROMPT
# ─────────────────────────────────────────────────────────────
QA_PROMPT = """You are a government content quality reviewer for the Bahrain.bh Service Catalog.
I will provide Arabic and English versions of the same government service page.
Links appear as: descriptive text [LINK:url]

YOUR TASKS:
1. Identify spelling mistakes, typos, and writing errors in both Arabic and English.
2. Identify mistranslations, missing information, or inconsistencies between Arabic and English.
3. Compare Arabic and English side by side and flag:
   • Content in Arabic but missing in English
   • Content in English but missing in Arabic
   • Content in both but meaning does not match
4. Classify each issue as exactly one of:
   • Spelling / Typo Error
   • Grammar / Language Quality Issue
   • Translation Mismatch
   • Missing Content
   • Terminology Inconsistency (Government / Legal Terms)
   • Section Misplacement
   • Wrong Information
   • Incomplete Process
5. For each issue write the actual corrected text — not instructions about what to fix,
   but the real corrected content ready to copy-paste into the portal.
6. Use formal Bahrain eGovernment terminology.

ADDITIONAL REQUIRED CHECKS:
• Service description must start with "This service allows…" or "This service enables…" (EN)
  or "تتيح هذه الخدمة…" or "تمكّن هذه الخدمة…" (AR).
  If not — flag it and provide the full rewritten description using actual page context.
• If a law or regulation is cited without a hyperlink:
  [Missing linking] Make "[law name]" a clickable hyperlink to the official legislation portal.
• If "Skiplino" appears — flag: Skiplino is no longer in use. Remove or update to Mawaeed.
• If Service Conditions contains action steps instead of eligibility criteria — flag as Section Misplacement.
• If the last process step has no outcome — flag as Incomplete Process.
• If a raw URL appears as plain text — flag: make "[descriptive text]" a clickable hyperlink to [URL].
• If sections are empty (Fees, Legal Regulations, etc.) — flag and suggest what to add based on context.
• Do NOT change process time values — only flag if EN and AR values differ.
• If the service name is too long or unclear — suggest a refined name in both languages.

OUTPUT — return ONLY a raw JSON array. Zero prose. Zero markdown fences.
[
  {
    "id": <integer starting from startId>,
    "entity": "<service provider name>",
    "service": "<service name>",
    "section": "<one of the allowed section values>",
    "language": "<EN|AR|Both>",
    "issue_placement": "<Section Name>: \\"<verbatim quote from page — 5 to 15 words exactly as they appear>\\"",
    "issue_description": "<clear explanation of what is wrong and why it matters>",
    "proposed_solution": "<actual corrected text in EN and/or AR — copy-paste ready, not instructions>"
  }
]

ALLOWED SECTION VALUES (use exactly as written):
Service Name | Service Description | Required Attachments | Legal Regulations |
Fees | Process Time | Service Provider | Service Processes | Service Conditions |
Linguistic | Formatting | Wrong Information | Incomplete Process | User Clarity | Deprecated

CRITICAL — PROPOSED SOLUTION MUST BE ACTUAL CONTENT:
Wrong: "Provide a clearer description of the service."
Wrong: "Rewrite the name to be more concise."
Correct: Write the actual new description / name / corrected text.
Base corrections on the real content provided — use specific details from the page.

Example of one complete issue:
{
  "id": 1,
  "entity": "Ministry of Interior",
  "service": "Metal barrier rental",
  "section": "Service Description",
  "language": "Both",
  "issue_placement": "Service Description: \\"Metal barrier rental\\"",
  "issue_description": "The service description is identical to the service name in both languages and provides no explanation of scope, eligibility, or purpose.",
  "proposed_solution": "English: This service allows authorized entities such as places of worship, private schools, and private sector organizations to request the temporary rental of metal barriers for organizing events or managing crowds.\\nArabic: تتيح هذه الخدمة للجهات المصرح لها مثل دور العبادة والمدارس الخاصة ومؤسسات القطاع الخاص طلب إعارة الحواجز الحديدية بشكل مؤقت لتنظيم الفعاليات أو إدارة الحشود."
}"""


# HTTP SESSION
def _make_session():
    s = requests.Session()
    kw = dict(total=4, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    try:
        retry = Retry(**kw, allowed_methods=["GET"])
    except TypeError:
        retry = Retry(**kw, method_whitelist=["GET"])
    a = HTTPAdapter(max_retries=retry)
    s.mount("https://", a)
    s.mount("http://", a)
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    return s


SESSION = _make_session()


# PAGE SCRAPING
def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _el_text(el):
    """Convert element to text, preserving link annotations."""
    for a in el.find_all("a", href=True):
        href = a["href"].strip()
        txt = _clean(a.get_text())
        if href and href not in ("#", "javascript:void(0)"):
            a.replace_with(f"{txt} [LINK:{href}]")
    return _clean(el.get_text())


def _fetch_html(url):
    """Fetch page HTML using Playwright (JS-rendered) or requests fallback."""
    if PLAYWRIGHT_OK:
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(url, wait_until="networkidle", timeout=45000)
                try:
                    page.wait_for_selector("div.section__inner", timeout=8000)
                except Exception:
                    pass
                try:
                    page.evaluate(
                        """
                        () => document.querySelectorAll(
                            'button.accordion__button[aria-expanded="false"]'
                        ).forEach(b => b.click())
                    """
                    )
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
                html = page.content()
                browser.close()
                return html
        except Exception as e:
            print(f"\n  {orange()} Playwright failed ({str(e)[:50]}), using requests")
    r = SESSION.get(url, timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def _get_section(soup, h3_label):
    """Extract section content from soup by h3 heading."""
    target = None
    for h3 in soup.find_all("h3"):
        if _clean(h3.get_text()).lower() == h3_label.lower():
            target = h3
            break
    if not target:
        return ""

    label_lower = h3_label.lower()
    if "processes" in label_lower or "خطوات" in h3_label:
        div = target.find_parent("div", class_="section__inner")
        if div:
            parts = []
            for item in div.select("div.accordion__item"):
                btn = item.select_one("button.accordion__button")
                channel = _clean(btn.get_text()) if btn else ""
                steps = [
                    _el_text(li)
                    for li in item.select("div.accordion__content li")
                    if _clean(li.get_text())
                ]
                if channel:
                    parts.append(
                        (f"[{channel}]: " + " -> ".join(steps))
                        if steps
                        else f"[{channel}]"
                    )
            if parts:
                return " | ".join(parts)

    parent = (
        target.find_parent(
            "div",
            class_=lambda c: c
            and any(
                k in c
                for k in ("section__content-alt", "section__text", "section__body")
            ),
        )
        or target.parent
    )
    items = [_el_text(li) for li in parent.find_all("li") if _clean(li.get_text())]
    if not items:
        items = [_el_text(p) for p in parent.find_all("p") if _clean(p.get_text())]
    return " | ".join(items)


def scrape_page(lang, url):
    """Scrape a service page and return structured data dict."""
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    data = {"lang": lang, "url": url}

    intro = soup.select_one("div.intro__inner")
    if intro:
        h1 = intro.find("h1")
        p = intro.find("p")
        data["service_name"] = _clean(h1.get_text()) if h1 else ""
        data["service_description"] = _clean(p.get_text()) if p else ""
    else:
        data["service_name"] = data["service_description"] = ""

    h3_map = SECTION_H3.get(lang, SECTION_H3["en"])
    for label, key in h3_map.items():
        data[key] = _get_section(soup, label)

    return data


def format_for_ai(data, lang):
    """Format scraped data as readable text for the AI prompt."""
    lmap = SECTION_LABELS.get(lang, SECTION_LABELS["en"])
    lines = [f"=== {lang.upper()} PAGE ===", ""]

    lines.append(f"[{lmap['service_name']}]")
    lines.append(f"  {data.get('service_name','(empty)') or '(empty)'}")
    lines.append("")

    lines.append(f"[{lmap['service_description']}]")
    lines.append(f"  {data.get('service_description','(empty)') or '(empty)'}")
    lines.append("")

    for key in [
        "required_attachments",
        "legal_regulations",
        "fees",
        "process_time",
        "service_provider",
        "service_processes",
        "service_conditions",
    ]:
        val = data.get(key, "")
        lines.append(f"[{lmap[key]}]")
        if val:
            for item in val.split(" | "):
                item = item.strip()
                if item:
                    lines.append(f"  • {item}")
        else:
            lines.append("  (empty)")
        lines.append("")

    return "\n".join(lines)


# JSON REPAIR & PARSING
def _repair_json(raw):
    """Fix common JSON issues from AI responses."""
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


def _parse_response(raw):
    """Extract list of issue dicts from AI response."""
    raw = _repair_json(raw)
    # Try array
    m = re.search(r"\[[\s\S]*\]", raw)
    if m:
        try:
            result = json.loads(m.group())
            if isinstance(result, list):
                return result
        except Exception:
            pass
    # Try object with issues key
    m2 = re.search(r"\{[\s\S]*\}", raw)
    if m2:
        try:
            result = json.loads(m2.group())
            if "issues" in result:
                return result["issues"]
            if isinstance(result, list):
                return result
        except Exception:
            pass
    return []


# AI PROVIDERS  — Gemini → Groq → OpenRouter
def _call_gemini(full_prompt, key):
    payload = {
        "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 8192,
            "responseMimeType": "text/plain",
        },
    }
    last_err = ""
    for model in GEMINI_MODELS:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model}:generateContent"
        )
        try:
            print(f"\r  {grey(f'Gemini → {model}…')}", end="", flush=True)
            r = requests.post(
                url,
                params={"key": key},
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
            print(f"\r  {green('✓')} Gemini: {cyan(model)}" + " " * 30)
            return _parse_response(raw)
        except Exception as e:
            last_err = str(e)[:80]
            continue
    raise RuntimeError(f"Gemini failed: {last_err}")


def _call_groq(user_prompt, key):
    last_err = ""
    for model in GROQ_MODELS:
        try:
            print(f"\r  {grey(f'Groq → {model}…')}", end="", flush=True)
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": QA_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.1,
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
            print(f"\r  {green('✓')} Groq: {cyan(model)}" + " " * 30)
            return _parse_response(raw)
        except Exception as e:
            last_err = str(e)[:80]
            continue
    raise RuntimeError(f"Groq failed: {last_err}")


def _call_openrouter(user_prompt, key):
    last_err = ""
    for model in OPENROUTER_MODELS:
        try:
            print(f"\r  {grey(f'OpenRouter → {model}…')}", end="", flush=True)
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
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
                    "temperature": 0.1,
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
            print(f"\r  {green('✓')} OpenRouter: {cyan(model)}" + " " * 30)
            return _parse_response(raw)
        except Exception as e:
            last_err = str(e)[:80]
            continue
    raise RuntimeError(f"OpenRouter failed: {last_err}")


# Will change this function to accept the system prompt as an argument in case we want to test different prompts in the future
def call_ai(user_prompt, gemini_key="", groq_key="", openrouter_key=""):
    """
    Try providers in order: Gemini → Groq → OpenRouter.
    For Gemini the system prompt is prepended to the message.
    For Groq and OpenRouter it is sent as the system role.
    """
    errors = []

    if gemini_key:
        try:
            return _call_gemini(QA_PROMPT + "\n\n" + user_prompt, gemini_key)
        except Exception as e:
            errors.append(str(e))
            print(f"\n  {orange()} Gemini failed — trying Groq…")

    if groq_key:
        try:
            return _call_groq(user_prompt, groq_key)
        except Exception as e:
            errors.append(str(e))
            print(f"\n  {orange()} Groq failed — trying OpenRouter…")

    if openrouter_key:
        try:
            return _call_openrouter(user_prompt, openrouter_key)
        except Exception as e:
            errors.append(str(e))

    raise RuntimeError(
        "All AI providers failed:\n"
        + "\n".join(f"  • {e}" for e in errors)
        + "\n\nGet free keys:\n"
        "  Gemini:     https://aistudio.google.com\n"
        "  Groq:       https://console.groq.com\n"
        "  OpenRouter: https://openrouter.ai"
    )


# GOOGLE DRIVE UPLOAD
_drive_service = None
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DRIVE_TOKEN = "token.json"


def _init_drive(client_secret_file):
    """
    Initialise Google Drive using OAuth2.
    """
    global _drive_service
    if _drive_service:
        return _drive_service
    if not GDRIVE_OK:
        print(red("Google Drive libraries not installed."))
        print(
            "    pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        )
        return None
    try:
        creds = None
        if Path(DRIVE_TOKEN).exists():
            creds = Credentials.from_authorized_user_file(DRIVE_TOKEN, DRIVE_SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    client_secret_file, DRIVE_SCOPES
                )
                print(f"\n  {gold('→')} Opening browser for Google Drive login…")
                print(
                    f"  {grey('Token will be saved to token.json for future runs.')}\n"
                )
                creds = flow.run_local_server(port=0)

            # Save token for next run
            with open(DRIVE_TOKEN, "w") as f:
                f.write(creds.to_json())
            print(f"  {green()} Drive token saved → {DRIVE_TOKEN}")

        _drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
        print(f"  {green()} Google Drive connected")
        return _drive_service

    except Exception as e:
        print(red(f"Drive auth failed: {e}"))
        return None


# Will make to keep each psid under the same dir.
def upload_to_drive(local_path, folder_id, drive_service):
    """
    Upload a screenshot to Google Drive using OAuth2.
    """
    try:
        fname = Path(local_path).name
        media = MediaFileUpload(str(local_path), mimetype="image/png", resumable=False)
        meta = {"name": fname, "parents": [folder_id]}

        f = (
            drive_service.files()
            .create(
                body=meta,
                media_body=media,
                fields="id",
            )
            .execute()
        )
        fid = f.get("id")

        # Make shareable so anyone with link can view
        drive_service.permissions().create(
            fileId=fid,
            body={"type": "anyone", "role": "reader"},
        ).execute()

        return f"https://drive.google.com/file/d/{fid}/view"

    except Exception as e:
        print(f"  {orange()} Drive upload failed: {str(e)[:80]}")
        return ""


# SCREENSHOTS
def _safe_name(text, n=35):
    text = re.sub(r"[^\w\s\u0600-\u06FF]", "", text or "")
    return re.sub(r"\s+", "_", text.strip())[:n]


def _infer_section(iss):
    """Infer section from issue fields when section is missing."""
    text = " ".join(
        [
            iss.get("section", ""),
            iss.get("issue_placement", ""),
            iss.get("issue_description", ""),
        ]
    ).lower()

    mapping = {
        "Service Description": ["description", "وصف"],
        "Required Attachments": ["attachment", "document", "مستند", "مرفق"],
        "Legal Regulations": ["legal", "regulation", "law", "قانون", "لائحة"],
        "Fees": ["fee", "رسوم", "رسم", "bd ", "bhd", "currency"],
        "Process Time": ["process time", "working day", "وقت", "يوم عمل"],
        "Service Processes": [
            "process",
            "step",
            "خطوة",
            "channel",
            "submission",
            "submit",
        ],
        "Service Provider": ["provider", "ministry", "وزارة", "جهة"],
        "Service Name": ["service name", "اسم الخدمة"],
        "Service Conditions": ["condition", "شرط", "eligib"],
    }
    for section, keywords in mapping.items():
        if any(kw in text for kw in keywords):
            return section
    return "Service Description"


def take_screenshots(
    issues,
    en_url,
    ar_url,
    label,
    screenshots_dir,
    drive_service=None,
    drive_folder=None,
):
    """
    For every issue:
      1. Navigate to the correct page (EN or AR)
      2. Scroll to the relevant section
      3. Highlight section with orange border + yellow tint
      4. Screenshot and save locally
      5. If Drive configured → upload and replace local path with Drive link
    """
    if not PLAYWRIGHT_OK:
        print(red("Playwright not installed."))
        print("pip install playwright && playwright install chromium")
        return issues

    folder = Path(screenshots_dir) / _safe_name(label)
    folder.mkdir(parents=True, exist_ok=True)
    print(f"  {gold()} Screenshots → {cyan(str(folder))}")
    if drive_service:
        print(f"  {blue()}  Drive upload enabled")

    en_issues = [i for i in issues if i.get("language", "EN") in ("EN", "Both", "")]
    ar_issues = [i for i in issues if i.get("language", "") == "AR"]

    def shoot_group(page_issues, url, lang):
        if not page_issues or not url:
            return

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})

            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(1500)
                # Expand all accordion sections
                page.evaluate(
                    """
                    () => document.querySelectorAll(
                        'button.accordion__button[aria-expanded="false"]'
                    ).forEach(b => b.click())
                """
                )
                page.wait_for_timeout(1200)
            except Exception as e:
                print(f"\n  {orange('⚠')} Page load failed: {str(e)[:60]}")
                browser.close()
                return

            for iss in page_issues:
                iid = iss.get("id", 0)
                section = iss.get("section", "") or _infer_section(iss)
                sdata = SECTION_HEADINGS.get(
                    section, {"en": "Service Description", "ar": "وصف الخدمة"}
                )
                kw = sdata["ar"] if lang == "AR" else sdata["en"]
                fname = f"issue_{iid:03d}_{_safe_name(section,20)}.png"
                fpath = folder / fname

                try:
                    # Find section, highlight it, get its position
                    result = page.evaluate(
                        """
                        (kw) => {
                            const h3 = Array.from(document.querySelectorAll('h3'))
                                .find(el => el.innerText.trim()
                                    .toLowerCase()
                                    .includes(kw.toLowerCase()));
                            if (!h3) return null;

                            const container =
                                h3.closest('.section__inner') ||
                                h3.closest('.section__content') ||
                                h3.parentElement?.parentElement ||
                                h3.parentElement;

                            if (!container) return null;

                            // Apply highlight
                            container.style.setProperty(
                                'outline', '3px solid #FF6B00', 'important');
                            container.style.setProperty(
                                'background-color',
                                'rgba(255, 215, 0, 0.15)', 'important');

                            // Scroll into view
                            container.scrollIntoView(
                                {behavior: 'instant', block: 'center'});

                            const r = container.getBoundingClientRect();
                            return {
                                x: r.left, y: r.top,
                                w: r.width, h: r.height
                            };
                        }
                    """,
                        kw,
                    )

                    page.wait_for_timeout(500)

                    if result and result.get("w", 0) > 0:
                        page.screenshot(
                            path=str(fpath),
                            full_page=False,
                            clip={
                                "x": max(0, result["x"] - 15),
                                "y": max(0, result["y"] - 15),
                                "width": min(result["w"] + 30, 1430),
                                "height": min(result["h"] + 30, 880),
                            },
                        )
                    else:
                        # Section not found — full viewport
                        page.screenshot(path=str(fpath), full_page=False)

                    # Remove highlight before next issue
                    page.evaluate(
                        """
                        () => document.querySelectorAll(
                            '[style*="FF6B00"]'
                        ).forEach(el => {
                            el.style.removeProperty('outline');
                            el.style.removeProperty('background-color');
                        })
                    """
                    )

                    # Upload to Drive or keep local path
                    if drive_service and drive_folder and fpath.exists():
                        link = upload_to_drive(fpath, drive_folder, drive_service)
                        iss["screenshot"] = link or str(fpath)
                        indicator = blue("☁") if link else orange("~")
                    else:
                        iss["screenshot"] = str(fpath)
                        indicator = green("✓")

                    print(f"     {indicator}  [{iid:03d}] {section} → {fname}")

                except Exception as e:
                    # Last resort: viewport screenshot
                    try:
                        page.screenshot(path=str(fpath), full_page=False)
                        iss["screenshot"] = str(fpath)
                        print(
                            f"     {orange('~')}  [{iid:03d}] {section} → {fname} (fallback)"
                        )
                    except Exception:
                        iss["screenshot"] = ""
                        print(
                            f"     {red('✗')}  [{iid:03d}] {section} — failed: {str(e)[:40]}"
                        )

            browser.close()

    shoot_group(en_issues, en_url, "EN")
    shoot_group(ar_issues, ar_url, "AR")

    done = sum(1 for i in issues if i.get("screenshot"))
    drive = sum(
        1 for i in issues if str(i.get("screenshot", "")).startswith("https://drive")
    )
    print(
        f"\n  {green('✓')} {done}/{len(issues)} screenshots captured"
        + (f" · {blue(str(drive))} uploaded to Drive" if drive else "")
        + "\n"
    )
    return issues


# CSV OUTPUT
def write_csv(issues, path, service_url=""):
    """
    Write issues to CSV with the defined column structure:
    ID | Entity | Service (hyperlinked) | Issue Placement |
    Issue Description | Proposed Solution/Suggestion | Screenshot
    """
    rows = []
    for iss in issues:
        svc = str(iss.get("service", "") or "").replace('"', "'")

        # Service column — clickable hyperlink in Excel/Google Sheets
        if service_url:
            safe_url = service_url.replace('"', "%22")
            service_cell = f'=HYPERLINK("{safe_url}","{svc}")'
        else:
            service_cell = svc

        # Screenshot — if it's a Drive link make it clickable
        screenshot = iss.get("screenshot", "")
        if screenshot.startswith("https://"):
            screenshot_cell = f'=HYPERLINK("{screenshot}","View Screenshot")'
        else:
            screenshot_cell = screenshot

        rows.append(
            {
                "ID": iss.get("id", ""),
                "Entity": iss.get("entity", ""),
                "Service": service_cell,
                "Issue Placement": iss.get("issue_placement", ""),
                "Issue Description": iss.get("issue_description", ""),
                "Proposed Solution/Suggestion": iss.get("proposed_solution", ""),
                "Screenshot": screenshot_cell,
            }
        )

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# MAIN FUNCTION
def audit_service(
    psid,
    gemini_key,
    groq_key,
    openrouter_key,
    do_screenshots,
    screenshots_dir,
    start_id,
    drive_key_file=None,
    drive_folder_id=None,
):

    en_url = BASE_URL.format(lang="en", psid=psid)
    ar_url = BASE_URL.format(lang="ar", psid=psid)
    label = f"psID_{psid}"

    print()
    print(gold(f"     Auditing psID {psid}"))
    print(grey(f"     EN → {en_url}"))
    print(grey(f"     AR → {ar_url}"))
    print()

    # ── Step 1: Scrape ────────────────────────────────────────
    print(f"  {gold('①')} Scraping pages…", end="", flush=True)
    try:
        en = scrape_page("en", en_url)
        ar = scrape_page("ar", ar_url)
    except Exception as e:
        print(f"\n  {red()} Scrape failed: {e}")
        return []

    entity = en.get("service_provider", "") or ar.get("service_provider", "")
    service = en.get("service_name", "") or ar.get("service_name", "")

    if not service:
        print(f"\n  {orange()} No service name found — psID may not exist")
        return []

    print(f"\r  {green()} Scraped: {cyan(service[:55])}" + " " * 20)

    # ── Step 2: AI Audit ──────────────────────────────────────
    print(f"  {gold('②')} Running AI audit…", end="", flush=True)

    user_prompt = (
        f"startId: {start_id}\n\n"
        + format_for_ai(en, "en")
        + "\n\n"
        + "─" * 60
        + "\n\n"
        + format_for_ai(ar, "ar")
    )

    try:
        issues = call_ai(user_prompt, gemini_key, groq_key, openrouter_key)
    except Exception as e:
        print(f"\n  {red('✗')} AI failed: {e}")
        return []

    if not issues:
        print(f"  {orange('⚠')} AI returned no issues")
        return []

    # Fill missing entity/service from scraped data
    for iss in issues:
        if not iss.get("entity"):
            iss["entity"] = entity
        if not iss.get("service"):
            iss["service"] = service
        if not iss.get("screenshot"):
            iss["screenshot"] = ""
        if not iss.get("issue_placement"):
            iss["issue_placement"] = ""
        if not iss.get("issue_description"):
            iss["issue_description"] = ""
        if not iss.get("proposed_solution"):
            iss["proposed_solution"] = ""

    print(f"  {green('✓')} {len(issues)} issues found")

    # ── Step 3: Screenshots ───────────────────────────────────
    if do_screenshots:
        print(f"  {gold('③')} Taking screenshots…")

        drive_service = None
        if drive_key_file and drive_folder_id:
            drive_service = _init_drive(drive_key_file)

        issues = take_screenshots(
            issues,
            en_url,
            ar_url,
            label,
            screenshots_dir,
            drive_service,
            drive_folder_id,
        )
    else:
        print(f"  {grey('③')} Screenshots skipped (add --screenshots to enable)")

    # ── Step 4: Save CSV ──────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = f"qa_{psid}_{ts}.csv"
    write_csv(issues, csv_path, service_url=en_url)
    print(f"  {green('✓')} CSV → {cyan(csv_path)}\n")

    return issues


# CLI
def main():
    print()
    print(gold("╔" + "═" * 62 + "╗"))
    print(gold("║") + f"{'SFB Health Checkup  —  v0.3':^62}" + gold("║"))
    print(
        gold("║")
        + grey(f"{'Gemini · Groq · OpenRouter (Qwen2.5-72B)':^62}")
        + gold("║")
    )
    print(gold("╚" + "═" * 62 + "╝"))
    print()

    parser = argparse.ArgumentParser(
        description="SFB Health Checkup v0.3",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--psid", nargs="+", type=int, required=True, help="One or more service psIDs"
    )
    parser.add_argument(
        "--key", default="", help="Gemini API key (or set GEMINI_API_KEY env var)"
    )
    parser.add_argument(
        "--groq-key", default="", help="Groq API key (or set GROQ_API_KEY env var)"
    )
    parser.add_argument(
        "--openrouter-key",
        default="",
        help="OpenRouter API key (or set OPENROUTER_API_KEY env var)",
    )
    parser.add_argument(
        "--screenshots", action="store_true", help="Take screenshots for each issue"
    )
    parser.add_argument(
        "--screenshots-dir",
        default="screenshots",
        help="Local folder for screenshots (default: screenshots/)",
    )
    parser.add_argument(
        "--drive-key",
        default="",
        help="Path to Google Drive service account JSON key file",
    )
    parser.add_argument(
        "--drive-folder",
        default="",
        help="Google Drive folder ID to upload screenshots to",
    )
    parser.add_argument(
        "--start-id", type=int, default=1, help="Starting issue ID (default: 1)"
    )
    args = parser.parse_args()

    # Resolve keys — CLI args take priority over env vars
    gemini_key = args.key or os.environ.get("GEMINI_API_KEY", "")
    groq_key = args.groq_key or os.environ.get("GROQ_API_KEY", "")
    openrouter_key = args.openrouter_key or os.environ.get("OPENROUTER_API_KEY", "")

    if not any([gemini_key, groq_key, openrouter_key]):
        print(red("No API key provided.\n"))
        print("  Provide at least one of:")
        print("    --key AIza...              (Gemini)     https://aistudio.google.com")
        print("    --groq-key gsk_...         (Groq)       https://console.groq.com")
        print("    --openrouter-key sk-or-... (OpenRouter)  https://openrouter.ai\n")
        sys.exit(1)

    if args.screenshots and not PLAYWRIGHT_OK:
        print(orange("    --screenshots requires Playwright:"))
        print("     pip install playwright && playwright install chromium\n")

    if args.drive_key and not args.drive_folder:
        print(orange("    --drive-key provided but --drive-folder is missing."))
        print("     Screenshots will be saved locally only.\n")

    if args.drive_key and not GDRIVE_OK:
        print(orange("    Google Drive libraries not installed:"))
        print(
            "     pip install google-api-python-client google-auth google-auth-httplib2\n"
        )

    # Show active configuration
    print(f"  {gold('Providers:')} ", end="")
    active = []
    if gemini_key:
        active.append(green("Gemini"))
    if groq_key:
        active.append(green("Groq"))
    if openrouter_key:
        active.append(green("OpenRouter"))
    print(" → ".join(active))
    print(
        f"  {gold('Screenshots:')} {'enabled → ' + cyan(args.screenshots_dir) if args.screenshots else grey('disabled')}"
    )
    if args.drive_key and args.drive_folder:
        print(f"  {gold('Drive:')} {cyan(args.drive_folder)}")
    print()

    # Run
    current_id = args.start_id
    all_issues = []

    for psid in args.psid:
        issues = audit_service(
            psid,
            gemini_key,
            groq_key,
            openrouter_key,
            args.screenshots,
            args.screenshots_dir,
            current_id,
            args.drive_key or None,
            args.drive_folder or None,
        )
        if issues:
            all_issues.extend(issues)
            current_id = max(i.get("id", current_id) for i in issues) + 1

    # Batch summary
    if len(args.psid) > 1:
        print(gold("─" * 62))
        print(
            gold(
                f"  Batch complete — {len(all_issues)} total issues across {len(args.psid)} services"
            )
        )
        print(gold("─" * 62))

    print(gold("\n  Done \n"))


if __name__ == "__main__":
    main()
