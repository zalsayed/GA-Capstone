import os
import re
import threading

_drive_upload_lock = threading.Lock()
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

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
    "Formatting": {"en": "Service Description", "ar": "وصف الخدمة"},
    "Wrong Information": {"en": "Service Description", "ar": "وصف الخدمة"},
    "Incomplete Process": {"en": "Service Processes", "ar": "خطوات تقديم الخدمة"},
    "User Clarity": {"en": "Service Processes", "ar": "خطوات تقديم الخدمة"},
    "Deprecated": {"en": "Service Processes", "ar": "خطوات تقديم الخدمة"},
}

_EMPTY_SIGNALS = (
    "empty",
    "missing",
    "not provided",
    "no content",
    "not present",
    "absent",
    "not found",
    "فارغ",
    "مفقود",
    "غير موجود",
    "(empty)",
)


def is_available() -> bool:
    return PLAYWRIGHT_OK


def _safe_name(text: str, n: int = 25) -> str:
    text = re.sub(r"[^\w\s\u0600-\u06FF]", "", text or "")
    return re.sub(r"\s+", "_", text.strip())[:n]


def _infer_section(iss: dict) -> str:
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
        "Legal Regulations": ["legal", "regulation", "قانون", "لائحة"],
        "Fees": ["fee", "رسوم", "bd ", "bhd"],
        "Process Time": ["process time", "working day", "وقت", "يوم عمل"],
        "Service Processes": ["process", "step", "خطوة", "submit"],
        "Service Provider": ["provider", "ministry", "وزارة", "جهة"],
        "Service Name": ["service name", "اسم الخدمة"],
        "Service Conditions": ["condition", "شرط", "eligib"],
    }
    for section, kws in mapping.items():
        if any(k in text for k in kws):
            return section
    return "Service Description"


_FIND_AND_HIGHLIGHT_JS = """
([quote, sectionKw, isServiceHead]) => {
    function norm(s) {
        return (s||'').replace(/\s+/g,' ').trim()
               .replace(/[\u064b-\u065f\u0670\u0640]/g,'').toLowerCase();
    }
    function isVisible(el) {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        return s.display!=='none' && s.visibility!=='hidden'
               && el.offsetWidth>0 && el.offsetHeight>0;
    }
    function rectOf(el) {
        const r = el.getBoundingClientRect();
        return {x: Math.max(0,r.left-20), y: Math.max(0,r.top-20),
                w: Math.min(r.width+40, 1420), h: Math.min(r.height+40, 860)};
    }
    function highlightAndShoot(el, isText) {
        if (isText) {
            el.style.setProperty('background-color','#FFD700','important');
            el.style.setProperty('outline','3px solid #FF6B00','important');
            // Scroll the element to center of viewport
            el.scrollIntoView({behavior:'instant', block:'center'});
            // Nudge down if behind sticky nav
            const nav = document.querySelector('header,nav.navbar,.header,.js-header');
            const navBottom = nav ? nav.getBoundingClientRect().bottom : 0;
            const r = el.getBoundingClientRect();
            if (r.top < navBottom + 10) window.scrollBy(0, r.top - navBottom - 20);
        } else {
            el.style.setProperty('outline','4px solid #FF6B00','important');
            el.style.setProperty('background-color','rgba(255,215,0,0.18)','important');
            el.scrollIntoView({behavior:'instant', block:'center'});
            const nav = document.querySelector('header,nav.navbar,.header,.js-header');
            const navBottom = nav ? nav.getBoundingClientRect().bottom : 0;
            const r = el.getBoundingClientRect();
            if (r.top < navBottom + 10) window.scrollBy(0, r.top - navBottom - 20);
        }
        const r = el.getBoundingClientRect();
        return {x: Math.max(0,r.left-20), y: Math.max(0,r.top-20),
                w: Math.min(r.width+40, 1420), h: Math.min(r.height+80, 860),
                found: true, method: isText ? 'quote' : 'section'};
    }

    // ── 1. Exact quote text via TreeWalker ────────────────────
    if (quote && quote.length >= 5) {
        const qN = norm(quote);
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let best = null, bestLen = Infinity, node;
        while ((node = walker.nextNode())) {
            if (!isVisible(node.parentElement)) continue;
            const t = norm(node.textContent);
            if (t.includes(qN) && t.length < bestLen) {
                best = node.parentElement;
                bestLen = t.length;
            }
        }
        if (best) return highlightAndShoot(best, true);
    }

    // ── 2. Service Name → h2, Service Description → p ────────
    const kwN = norm(sectionKw);
    if (isServiceHead) {
        // Target the specific element inside section__head
        const isName = kwN.includes('service name') || kwN.includes('\u0627\u0633\u0645');
        const sel = isName
            ? '.section__head h2, h1.section__title, .section__title'
            : '.section__head p, .section__desc, .section__head .lead';
        const el = Array.from(document.querySelectorAll(sel)).find(isVisible);
        if (el) return highlightAndShoot(el, false);
    }

    // ── 3. Section heading → container ───────────────────────
    let heading = null;
    for (const tag of ['h3','h2','button']) {
        heading = Array.from(document.querySelectorAll(tag))
            .filter(isVisible)
            .find(el => {
                const t = norm(el.innerText||el.textContent||'');
                return t === kwN || t.includes(kwN) || kwN.includes(t);
            });
        if (heading) break;
    }
    if (!heading) return {found: false};

    // Expand accordion and wait synchronously for animation
    if (heading.tagName==='BUTTON' &&
        (heading.classList.contains('collapsed')||
         heading.getAttribute('aria-expanded')==='false')) {
        heading.click();
        // Busy-wait up to 600ms for the accordion content to expand
        const t0 = Date.now();
        while (Date.now() - t0 < 600) { /* spin */ }
    }

    const container =
        heading.closest('.section__body') ||
        heading.closest('.section__inner') ||
        heading.closest('.section__content') ||
        heading.closest('.accordion__item') ||
        heading.closest('.accordion-item') ||
        heading.parentElement?.parentElement ||
        heading.parentElement;

    if (!container) return highlightAndShoot(heading, false);
    return highlightAndShoot(container, false);
}
"""

_CLEANUP_JS = """
() => {
    document.querySelectorAll('*').forEach(el => {
        if (el.style.getPropertyValue('background-color') === 'rgb(255, 215, 0)' ||
            el.style.outline.includes('FF6B00')) {
            el.style.removeProperty('background-color');
            el.style.removeProperty('outline');
        }
    });
}
"""


def take_screenshots(
    issues: list[dict],
    psid: str,
    en_url: str,
    ar_url: str,
    screenshots_dir: str,
    en_html: str = "",
    ar_html: str = "",
    drive_service=None,
    drive_folder: str = "",
    upload_fn=None,
) -> list[dict]:
    if not PLAYWRIGHT_OK:
        print("  No Playwright — screenshots skipped.")
        return issues

    out_dir = Path(screenshots_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    en_issues = [i for i in issues if i.get("language", "EN") in ("EN", "Both", "")]
    ar_issues = [i for i in issues if i.get("language", "") == "AR"]

    def shoot_group(page_issues: list[dict], url: str, lang: str, html: str):
        if not page_issues:
            return

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            loaded = False
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(2000)
                loaded = (
                    "403" not in page.title() and "blocked" not in page.title().lower()
                )
            except Exception:
                pass

            if not loaded and html:
                try:
                    css = "<style>.modal{display:none!important}.modal-backdrop{display:none!important}</style>"
                    h = (
                        html.replace("</head>", css + "</head>", 1)
                        if "</head>" in html
                        else css + html
                    )
                    page.set_content(h, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                    loaded = True
                except Exception:
                    pass

            if not loaded:
                browser.close()
                return

            # Dismiss cookie banner + expand accordions
            try:
                page.evaluate(
                    """() => {
                    // Accept cookie button (موافق / Accept)
                    document.querySelectorAll('button').forEach(b => {
                        const t = (b.innerText||'').trim();
                        if (t==='موافق'||t==='Accept'||t==='I Accept') b.click();
                    });
                    // Hide overlays
                    document.querySelectorAll('.modal,.modal-backdrop,[class*="cookie"]')
                        .forEach(m => m.style.setProperty('display','none','important'));
                    document.body.classList.remove('modal-open');
                    document.body.style.overflow = 'auto';
                    // Expand all accordions
                    document.querySelectorAll('button.accordion__button[aria-expanded="false"]')
                        .forEach(b => b.click());
                }"""
                )
                page.wait_for_timeout(1500)
            except Exception:
                pass

            for iss in page_issues:
                iid = iss.get("id", 0)
                section = iss.get("section", "") or _infer_section(iss)
                sdata = SECTION_HEADINGS.get(
                    section, {"en": "Service Description", "ar": "وصف الخدمة"}
                )
                kw = sdata["ar"] if lang == "AR" else sdata["en"]
                fname = f"{psid}_{iid:03d}_{_safe_name(section, 20)}.png"
                fpath = out_dir / fname

                desc = (iss.get("issue_description", "") or "").lower()
                place = (iss.get("issue_placement", "") or "").lower()
                if any(s in desc or s in place for s in _EMPTY_SIGNALS):
                    iss["screenshot"] = ""
                    print(f"     SKIP [{iid:03d}] {section:<25} [empty issue]")
                    continue

                placement = iss.get("issue_placement", "") or ""
                m = re.search(
                    r'["\u201c\u2018]([^"\']{4,120})["\u201d\u2019]', placement
                )
                quote = m.group(1).strip() if m else ""

                kw_lower = kw.lower()
                is_head = (
                    "service name" in kw_lower
                    or "service description" in kw_lower
                    or "اسم الخدمة" in kw
                    or "وصف الخدمة" in kw
                )

                try:
                    result = page.evaluate(_FIND_AND_HIGHLIGHT_JS, [quote, kw, is_head])
                    page.wait_for_timeout(600)

                    if result and result.get("found"):
                        page.screenshot(
                            path=str(fpath),
                            full_page=False,
                            clip={
                                "x": result["x"],
                                "y": result["y"],
                                "width": result["w"],
                                "height": result["h"],
                            },
                        )
                    else:
                        page.screenshot(path=str(fpath), full_page=False)

                    page.evaluate(_CLEANUP_JS)

                    if drive_service and drive_folder and fpath.exists() and upload_fn:
                        with _drive_upload_lock:
                            link = upload_fn(str(fpath), drive_folder, drive_service)
                        iss["screenshot"] = link or str(fpath)
                        indicator = "Drive" if link else "local"
                    else:
                        iss["screenshot"] = str(fpath)
                        indicator = "local"

                    method = result.get("method", "section") if result else "fallback"
                    print(
                        f"     OK  [{iid:03d}] {section:<25} [{method}→{indicator}] {fname}"
                    )

                except Exception as e:
                    try:
                        page.evaluate(_CLEANUP_JS)
                    except Exception:
                        pass
                    try:
                        page.screenshot(path=str(fpath), full_page=False)
                        iss["screenshot"] = str(fpath)
                        print(f"     ~   [{iid:03d}] {section} (fallback) {fname}")
                    except Exception:
                        iss["screenshot"] = ""
                        print(f"     ✗   [{iid:03d}] {section} FAILED: {str(e)[:50]}")

            browser.close()

    shoot_group(en_issues, en_url, "EN", en_html)
    shoot_group(ar_issues, ar_url, "AR", ar_html)

    done = sum(1 for i in issues if i.get("screenshot"))
    drive = sum(
        1 for i in issues if str(i.get("screenshot", "")).startswith("https://")
    )
    print(
        f"\n  {done}/{len(issues)} screenshots captured"
        + (f" · {drive} on Drive" if drive else "")
        + "\n"
    )
    return issues
