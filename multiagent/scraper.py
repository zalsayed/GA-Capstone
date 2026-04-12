import csv
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
import threading
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


PLAYWRIGHT_OK = False

BASE_URL = (
    "https://www.bahrain.bh/wps/portal/{lang}/BNP/"
    "ServicesCatalogue/GSX-UI-PServiceDetails?psID={psid}"
)

# eService pages live on the services portal (services.bahrain.bh)
ESERVICE_BASE_URL = (
    "https://services.bahrain.bh/wps/portal/{lang}/BSP/"
    "GSX-UI-EServiceDetails?esID={esid}"
)

# Accordion section labels on eService pages (EN)
ESERVICE_ACCORDION_EN = {
    "Service Conditions": "service_conditions",
    "Required Attachments": "required_attachments",
    "Fees": "fees",
    "Process Time": "process_time",
    "Legal Regulations": "legal_regulations",
}

# Accordion section labels on eService pages (AR)
ESERVICE_ACCORDION_AR = {
    "شروط الخدمة": "service_conditions",
    "المستندات المطلوبة": "required_attachments",
    "الرسوم": "fees",
    "وقت الإنجاز": "process_time",
    "الأدوات القانونية": "legal_regulations",
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

ENTITY_DETAIL_PATTERN = re.compile(r"(GSX-UI-EServiceDetails|GSX-UI-PServiceDetails)")


def _make_session() -> requests.Session:
    s = requests.Session()
    kw = dict(total=4, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    try:
        retry = Retry(**kw, allowed_methods=["GET"])
    except TypeError:
        retry = Retry(**kw, method_whitelist=["GET"])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    return s


_thread_local = threading.local()


def _get_session() -> requests.Session:
    """Return a Session private to the calling thread, creating one if needed."""
    if not hasattr(_thread_local, "session"):
        _thread_local.session = _make_session()
    return _thread_local.session


def extract_services_from_html(html_path: str, output_csv: str) -> list[dict]:
    """
    Read a locally saved entity page HTML and extract all service cards.
    Handles both psID (Service Catalog, bahrain.bh) and
    esID (eServices Portal, services.bahrain.bh) links.

    Writes results to output_csv and returns the list of services.
    Each service dict: {psid, name, url, is_eservice}
    """
    html_file = Path(html_path)
    if not html_file.exists():
        raise FileNotFoundError(
            f"HTML file not found: {html_file.resolve()}\n"
            "Save the entity page from your browser first:\n"
            "  File → Save Page As → Webpage, HTML Only"
        )

    with open(html_file, "r", encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    services = []
    seen_ids = set()

    for anchor in soup.find_all("a", href=ENTITY_DETAIL_PATTERN):
        href = anchor.get("href", "").strip()
        if not href:
            continue

        is_eservice = "GSX-UI-EServiceDetails" in href

        if is_eservice:
            if href.startswith("http"):
                full_url = href
            else:
                full_url = urljoin("https://services.bahrain.bh/", href)
        else:
            if href.startswith("http"):
                full_url = href
            else:
                full_url = urljoin(
                    "https://www.bahrain.bh/wps/portal/en/BNP/ServicesCatalogue/", href
                )

        params = parse_qs(urlparse(full_url).query)
        id_val = ""
        id_type = ""
        for key in ("esID", "psID"):
            if key in params:
                id_val = params[key][0]
                id_type = key
                break

        if not id_val:
            continue

        dedup_key = (id_val, id_type)
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)

        name_tag = anchor.find("h5")
        name = (
            name_tag.get_text(strip=True)
            if name_tag
            else anchor.get_text(" ", strip=True)
        )
        name = " ".join(name.split())

        if is_eservice:
            canonical_url = ESERVICE_BASE_URL.format(lang="en", esid=id_val)
        else:
            canonical_url = BASE_URL.format(lang="en", psid=id_val)

        services.append(
            {
                "psid": id_val,
                "name": name,
                "url": canonical_url,
                "is_eservice": is_eservice,
            }
        )

    out = Path(output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=["PSID", "Service Name", "URL", "Is_eService"]
        )
        writer.writeheader()
        for svc in services:
            writer.writerow(
                {
                    "PSID": svc["psid"],
                    "Service Name": svc["name"],
                    "URL": svc["url"],
                    "Is_eService": "yes" if svc["is_eservice"] else "no",
                }
            )

    ps_count = sum(1 for s in services if not s["is_eservice"])
    es_count = sum(1 for s in services if s["is_eservice"])
    print(
        f"  ✓ Extracted {len(services)} services ({ps_count} psID, {es_count} esID) → {out}"
    )
    return services


def load_services_csv(csv_path: str) -> list[dict]:
    """
    Load a services CSV and return list of dicts with psid, name, url, is_eservice.
    Accepts files with columns: PSID, Service Name, URL, Is_eService (in any order).
    Is_eService column is optional — defaults to False if missing.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Services CSV not found: {path.resolve()}")

    services = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            psid = str(row.get("PSID", "")).strip()
            name = str(row.get("Service Name", "")).strip()
            url = str(row.get("URL", "")).strip()
            is_eservice_raw = str(row.get("Is_eService", "")).strip().lower()
            is_eservice = is_eservice_raw in ("yes", "true", "1", "eservice")
            if not is_eservice_raw and url:
                is_eservice = "GSX-UI-EServiceDetails" in url or "esID=" in url
            if psid:
                services.append(
                    {
                        "psid": psid,
                        "name": name,
                        "url": url,
                        "is_eservice": is_eservice,
                    }
                )

    ps_count = sum(1 for s in services if not s["is_eservice"])
    es_count = sum(1 for s in services if s["is_eservice"])
    print(
        f"  ✓ Loaded {len(services)} services from {path.name} ({ps_count} psID, {es_count} esID)"
    )
    return services


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _el_text(el) -> str:
    """Convert element to plain text, preserving link annotations."""
    for a in el.find_all("a", href=True):
        href = a["href"].strip()
        txt = _clean(a.get_text())
        if href and href not in ("#", "javascript:void(0)"):
            a.replace_with(f"{txt} [LINK:{href}]")
    return _clean(el.get_text())


def _fetch_html(url: str) -> str:
    """
    Fetch page HTML using requests only.
    Playwright is NOT used for scraping — bahrain.bh blocks headless browsers.
    Playwright is only used for screenshots (with pre-fetched HTML).
    """
    r = _get_session().get(url, timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def _get_section(soup, h3_label: str) -> str:
    """
    Extract section content by h3 heading text.

    Portal HTML structure (from probe):
    - Each h3 owns the sibling elements that follow it until the next h3
    - Legal Regulations → next sibling is <ul>
    - Fees → next siblings are <ul>, <h4>, <ul>, <h4>, <ul>...
    - Process Time → next sibling is <ul>
    - Service Provider → next sibling is <p>
    - Service Processes → parent div.section__inner contains accordion
    """
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
            items = [_el_text(li) for li in div.find_all("li") if _clean(li.get_text())]
            if items:
                return " | ".join(items)

    items = []
    current = target.next_sibling

    while current:
        if hasattr(current, "name"):
            if current.name == "h3":
                break

            elif current.name in ("ul", "ol"):
                for li in current.find_all("li"):
                    raw = li.get_text(" ")

                    parts = [p.strip() for p in raw.split() if p.strip()]
                    text = " ".join(parts)

                    if text and text != ":":
                        items.append(text)

            elif current.name == "h4":
                label = _clean(current.get_text())
                if label:
                    items.append(f"[{label}]")

            elif current.name == "p":
                text = _clean(current.get_text())
                if text:
                    items.append(text)

            elif current.name == "div":
                sub_items = [
                    _el_text(li)
                    for li in current.find_all("li")
                    if _clean(li.get_text())
                ]
                if sub_items:
                    items.extend(sub_items)
                else:
                    text = _clean(current.get_text())
                    if text:
                        items.append(text)

            elif current.name == "a":
                text = _el_text(current)
                if text:
                    items.append(text)

        current = current.next_sibling

    return " | ".join(items)


def scrape_service_page(lang: str, url: str) -> tuple[dict, str]:
    """
    Scrape a single service page (EN or AR).
    Returns (data dict, raw HTML string).
    Raw HTML is passed to screenshot engine to avoid re-loading blocked pages.
    """
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    data = {"lang": lang, "url": url}

    intro = soup.select_one("div.intro__inner")
    if intro:
        h1 = intro.find("h1")
        p = None
        for candidate in intro.find_all("p"):
            text = _clean(candidate.get_text())
            if len(text) > 10:
                p = candidate
                break
        data["service_name"] = _clean(h1.get_text()) if h1 else ""
        data["service_description"] = _clean(p.get_text()) if p else ""
    else:
        data["service_name"] = data["service_description"] = ""

    h3_map = SECTION_H3.get(lang, SECTION_H3["en"])
    for label, key in h3_map.items():
        data[key] = _get_section(soup, label)

    return data, html


def scrape_psid(psid: str) -> tuple[dict, dict, str, str]:
    """
    Scrape both EN and AR pages for a given PSID.
    Returns (en_data, ar_data, en_html, ar_html).
    HTML strings are used by screenshot engine to render pages locally.
    """
    en_url = BASE_URL.format(lang="en", psid=psid)
    ar_url = BASE_URL.format(lang="ar", psid=psid)
    en, en_html = scrape_service_page("en", en_url)
    ar, ar_html = scrape_service_page("ar", ar_url)
    return en, ar, en_html, ar_html


def scrape_eservice_page(lang: str, url: str) -> tuple[dict, str]:
    """
    Scrape a single eService page (EN or AR).
    eService pages live on services.bahrain.bh and have a different
    HTML structure from psID pages:

      - Service name/description: div.section__head > h2 / p
      - eService URL: a.btn-base href
      - Provider: h3 "eService Provider" → next sibling a > span
      - Category: h3 "eService Category" → next sibling a > span
      - Sections: accordion items inside accordion-primary
                  button.accordion__button = section name
                  div.accordion__content = section body

    Returns (data dict, raw HTML string).
    """
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    data = {"lang": lang, "url": url, "page_type": "eservice"}

    head = soup.select_one("div.section__head")
    if head:
        h2 = head.find("h2")
        p = head.find("p")
        data["service_name"] = _clean(h2.get_text()) if h2 else ""
        data["service_description"] = _clean(p.get_text()) if p else ""
    else:
        data["service_name"] = data["service_description"] = ""

    btn_link = soup.select_one("a.btn-base")
    data["eservice_url"] = btn_link["href"].strip() if btn_link else ""

    data["service_provider"] = ""
    data["eservice_category"] = ""
    _PROVIDER_KEYWORDS = {"provider", "مزود", "مزوّد", "مقدم", "الجهة"}
    _CATEGORY_KEYWORDS = {"category", "فئة", "تصنيف", "نوع"}
    for div in soup.select("div.section__action-alt"):
        h3 = div.find("h3")
        if not h3:
            continue
        label = h3.get_text(strip=True).lower()
        a = div.find("a")
        if not a:
            continue
        span = a.find("span")
        val = _clean(span.get_text() if span else a.get_text())
        if not val:
            continue
        if any(kw in label for kw in _PROVIDER_KEYWORDS):
            data["service_provider"] = val
        elif any(kw in label for kw in _CATEGORY_KEYWORDS):
            data["eservice_category"] = val

    accordion_map = ESERVICE_ACCORDION_EN if lang == "en" else ESERVICE_ACCORDION_AR
    for key in accordion_map.values():
        data.setdefault(key, "")

    for item in soup.select(".accordion__item, .accordion-item"):
        btn = item.select_one(".accordion__button")
        if not btn:
            continue
        btn_text = _clean(btn.get_text())

        field_key = None
        for label, key in accordion_map.items():
            if label.lower() == btn_text.lower():
                field_key = key
                break
        if not field_key:
            for label, key in accordion_map.items():
                if (
                    label.lower() in btn_text.lower()
                    or btn_text.lower() in label.lower()
                ):
                    field_key = key
                    break
        if not field_key:
            continue

        content = item.select_one(".accordion__content")
        if not content:
            continue

        parts = []
        for child in content.children:
            if not hasattr(child, "name") or not child.name:
                continue
            if child.name in ("h3", "h4"):
                sub = _clean(child.get_text())
                if sub:
                    parts.append(f"[{sub}]")
            elif child.name in ("ul", "ol"):
                for li in child.find_all("li"):
                    txt = _clean(li.get_text())
                    if txt:
                        parts.append(txt)
            elif child.name == "div":
                for sub_h in child.find_all(["h3", "h4"]):
                    sub = _clean(sub_h.get_text())
                    if sub:
                        parts.append(f"[{sub}]")
                for li in child.find_all("li"):
                    txt = _clean(li.get_text())
                    if txt:
                        parts.append(txt)
            elif child.name == "p":
                txt = _clean(child.get_text())
                if txt:
                    parts.append(txt)

        data[field_key] = " | ".join(p for p in parts if p)

    for key in ["legal_regulations", "service_processes"]:
        data.setdefault(key, "")

    return data, html


def scrape_esid(esid: str) -> tuple[dict, dict, str, str]:
    """
    Scrape both EN and AR eService pages for a given esID.
    Returns (en_data, ar_data, en_html, ar_html).
    """
    en_url = ESERVICE_BASE_URL.format(lang="en", esid=esid)
    ar_url = ESERVICE_BASE_URL.format(lang="ar", esid=esid)
    en, en_html = scrape_eservice_page("en", en_url)
    ar, ar_html = scrape_eservice_page("ar", ar_url)
    return en, ar, en_html, ar_html


def format_eservice_for_ai(en_data: dict, ar_data: dict) -> str:
    """
    Format eService data as readable text for the AI prompt.
    Sections not present on eService pages are omitted.
    """
    lines = []
    for lang, data in [("EN", en_data), ("AR", ar_data)]:
        lines.append(f"=== {lang} ESERVICE PAGE ===")
        lines.append(f"[SERVICE NAME]\n  {data.get('service_name', '') or '(empty)'}")
        lines.append(
            f"[SERVICE DESCRIPTION]\n  {data.get('service_description', '') or '(empty)'}"
        )
        lines.append(f"[ESERVICE URL]\n  {data.get('eservice_url', '') or '(empty)'}")
        lines.append(
            f"[SERVICE PROVIDER]\n  {data.get('service_provider', '') or '(empty)'}"
        )
        lines.append(f"[CATEGORY]\n  {data.get('eservice_category', '') or '(empty)'}")

        for key, label in [
            ("service_conditions", "SERVICE CONDITIONS"),
            ("required_attachments", "REQUIRED ATTACHMENTS"),
            ("fees", "FEES"),
            ("process_time", "PROCESS TIME"),
            ("legal_regulations", "LEGAL REGULATIONS"),
        ]:
            val = data.get(key, "")
            lines.append(f"[{label}]")
            if val:
                for item in val.split(" | "):
                    item = item.strip()
                    if item:
                        lines.append(f"  • {item}")
            else:
                lines.append("  (empty)")
        lines.append("")
    return "\n".join(lines)


def format_for_ai(data: dict, lang: str) -> str:
    """Format scraped page data as readable text for the AI prompt."""
    lmap = SECTION_LABELS.get(lang, SECTION_LABELS["en"])
    lines = [f"=== {lang.upper()} PAGE ===", ""]

    for key in ["service_name", "service_description"]:
        lines.append(f"[{lmap[key]}]")
        val = data.get(key, "") or "(empty)"
        lines.append(f"  {val}")
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
        lines.append(f"[{lmap[key]}]")
        val = data.get(key, "")
        if val:
            for item in val.split(" | "):
                item = item.strip()
                if item:
                    lines.append(f"  • {item}")
        else:
            lines.append("  (empty)")
        lines.append("")

    return "\n".join(lines)
