"""
rules.py — Bahrain.bh QA Auditor
====================================
Deterministic Arabic + English rule checks.
These run BEFORE the AI call — zero API cost, 100% consistent output.

Checks covered:
  AR001 — Hamza errors (ادخال → إدخال, الإستمارة → الاستمارة)
  AR002 — Alif Maqsura / Yaa confusion (علي → على, حتي → حتى)
  AR003 — Arabic comma used where Latin comma should be (، vs ,) and vice versa
  AR004 — Arabic-Indic numerals mixed with Western digits
  AR005 — Missing Arabic definite article consistency
  EN001 — Raw email address not wrapped in mailto:
  EN002 — Raw URL as plain text
  EN003 — "Skiplino" reference — highlight for manual verification
  EN004 — Double spaces
  EN005 — Space before punctuation
  EN006 — Service name not in Title Case
  EN007 — Conditions text found in service name or description
  BI001 — Process time EN↔AR value mismatch
  BI002 — Fee amount EN↔AR value mismatch
  BI003 — Empty section in both languages
"""

import re

# ─────────────────────────────────────────────────────────────
# HAMZA ERROR MAP
# Both directions:
#   - Missing Hamza Qat (ا should be إ at start of word)
#   - Wrong Hamza Qat after definite article ال (الإستمارة → الاستمارة)
# ─────────────────────────────────────────────────────────────
HAMZA_ERRORS = {
    # ── Missing Hamza Qat ─────────────────────────────────────
    # Word starts with bare alif (ا) instead of alif with hamza below (إ)
    "الالكتروني": "الإلكتروني",
    "الالكترونية": "الإلكترونية",
    "الالكترونى": "الإلكتروني",
    "الكتروني": "إلكتروني",
    "الكترونية": "إلكترونية",
    "ادخال": "إدخال",
    "ارفاق": "إرفاق",
    "ايداع": "إيداع",
    "اعلان": "إعلان",
    "اصدار": "إصدار",
    "انجاز": "إنجاز",
    "اتمام": "إتمام",
    "اثبات": "إثبات",
    "ارسال": "إرسال",
    "اجراء": "إجراء",
    "اجراءات": "إجراءات",
    "اقامة": "إقامة",
    "ايجار": "إيجار",
    "الغاء": "إلغاء",
    "اتاحة": "إتاحة",
    "اعداد": "إعداد",
    "احضار": "إحضار",
    "ابرام": "إبرام",
    "اصلاح": "إصلاح",
    "اعادة": "إعادة",
    "افراد": "أفراد",
    "اشخاص": "أشخاص",
    "اعمال": "أعمال",
    "اموال": "أموال",
    "اسماء": "أسماء",
    "اسباب": "أسباب",
    "احكام": "أحكام",
    "اوراق": "أوراق",
    "اصول": "أصول",
    "انواع": "أنواع",
    "اقسام": "أقسام",
    "اطراف": "أطراف",
    "اهداف": "أهداف",
    "احداث": "أحداث",
    "ابواب": "أبواب",
    "اضرار": "أضرار",
    "اخطار": "أخطار",
    # ── Wrong Hamza Qat after ال — should be Hamza Wasl ──────
    "الإستمارة": "الاستمارة",
    "الإستفسار": "الاستفسار",
    "الإستعلام": "الاستعلام",
    "الإستلام": "الاستلام",
    "الإستخدام": "الاستخدام",
    "الإستيفاء": "الاستيفاء",
    "الإستثمار": "الاستثمار",
    "الإستيراد": "الاستيراد",
    "الإستئناف": "الاستئناف",
    "الإستكمال": "الاستكمال",
    "الإشتراك": "الاشتراك",
    "الإتصال": "الاتصال",
    "الإنتهاء": "الانتهاء",
    "الإختيار": "الاختيار",
    "الإنتساب": "الانتساب",
    "الإنضمام": "الانضمام",
    "الإفلاس": "الإفلاس",  # keep — correct hamza
    # ── Hamza on Alif — wrong placement ──────────────────────
    "لإستيراد": "لاستيراد",
    "لإستخدام": "لاستخدام",
    "لإستكمال": "لاستكمال",
    "لإستلام": "لاستلام",
    "لإستمارة": "لاستمارة",
    "بإستخدام": "باستخدام",
    "بإستيراد": "باستيراد",
    "بإستلام": "باستلام",
    # ── Taa Marbuta errors — written with Haa (ه) instead of Taa Marbuta (ة) ──
    "الأجهزه": "الأجهزة",
    "الأوراق المقدمه": "الأوراق المقدمة",
    "للأوراق المقدمه": "للأوراق المقدمة",
    "المقدمه": "المقدمة",
    "الهيئه": "الهيئة",
    "الجهه": "الجهة",
    "الخدمه": "الخدمة",
    "المنشأه": "المنشأة",
    "المنشاه": "المنشأة",
    "الرخصه": "الرخصة",
    "الشهاده": "الشهادة",
    "الموافقه": "الموافقة",
    "الطلبه": "الطلبة",
    "المرفقه": "المرفقة",
    "المطلوبه": "المطلوبة",
    "اللازمه": "اللازمة",
    "المعتمده": "المعتمدة",
    "المحدده": "المحددة",
    "الطبيه": "الطبية",
    "الصيدليه": "الصيدلية",
    "التجاريه": "التجارية",
    "الحكوميه": "الحكومية",
    "الرسميه": "الرسمية",
    "الإلكترونيه": "الإلكترونية",
    "الصناعيه": "الصناعية",
    "الماليه": "المالية",
    "القانونيه": "القانونية",
    "الاجتماعيه": "الاجتماعية",
    "الصحيه": "الصحية",
    "البيئيه": "البيئية",
    "العلميه": "العلمية",
    "الأكاديميه": "الأكاديمية",
    "الوطنيه": "الوطنية",
    "الدوليه": "الدولية",
    "المهنيه": "المهنية",
    "المدنيه": "المدنية",
    "الجنائيه": "الجنائية",
    "الإداريه": "الإدارية",
    "النظاميه": "النظامية",
    "التنظيميه": "التنظيمية",
    "التجاريه": "التجارية",
    "التشغيليه": "التشغيلية",
    "المصرفيه": "المصرفية",
    "المحليه": "المحلية",
    "الخارجيه": "الخارجية",
    "الداخليه": "الداخلية",
    "السنويه": "السنوية",
    "الشهريه": "الشهرية",
    "اليوميه": "اليومية",
    "الأساسيه": "الأساسية",
    "الإضافيه": "الإضافية",
    "الخاصه": "الخاصة",
    "العامه": "العامة",
    "الكامله": "الكاملة",
    "الصحيحه": "الصحيحة",
    "الدقيقه": "الدقيقة",
    "السريعه": "السريعة",
    "المتعلقه": "المتعلقة",
    "المقدمه": "المقدمة",
    "المسجله": "المسجلة",
    "المرخصه": "المرخصة",
    "المعتمده": "المعتمدة",
    "المحدده": "المحددة",
    "الواجبه": "الواجبة",
    "اللازمه": "اللازمة",
    "المناسبه": "المناسبة",
    "المحدده": "المحددة",
    "المطلوبه": "المطلوبة",
}

# ─────────────────────────────────────────────────────────────
# ALIF MAQSURA / YAA CONFUSION MAP
# Words that should end in ى (Alif Maqsura) but are written with ي (Yaa)
# ─────────────────────────────────────────────────────────────
ALIF_MAQSURA_ERRORS = {
    # ── Alif Maqsura written as Yaa ──────────────────────────
    "علي": "على",
    "الي": "إلى",
    "حتي": "حتى",
    "متي": "متى",
    "لدي": "لدى",
    "اخري": "أخرى",
    "كبري": "كبرى",
    "صغري": "صغرى",
    "اولي": "أولى",
    "مستوي": "مستوى",
    "محتوي": "محتوى",
    "مدي": "مدى",
    "سوي": "سوى",
    "عدي": "عدى",
    "هدي": "هدى",
    "ادي": "أدى",
    "ابقي": "أبقى",
    "انهي": "أنهى",
    "اجري": "أجرى",
    "اعطي": "أعطى",
    "اوفي": "أوفى",
    "اوحي": "أوحى",
    "تبقي": "تبقى",
    "يبقي": "يبقى",
    "يجري": "يجري",  # ambiguous — keep as is
    "يرجي": "يُرجى",
    "يعني": "يعنى",
    "تعني": "تعنى",
    "معني": "معنى",
    "مبني": "مبنى",
    "مستشفي": "مستشفى",
    "مصطفي": "مصطفى",
    "موسي": "موسى",
    "عيسي": "عيسى",
    "يحيي": "يحيى",
}

# ─────────────────────────────────────────────────────────────
# GENERIC SOLUTION PATTERNS — flag these as non-solutions
# ─────────────────────────────────────────────────────────────
GENERIC_SOLUTION_PATTERNS = [
    r"rewrite (this|the)",
    r"provide a (clearer|better|more)",
    r"improve (this|the|wording)",
    r"update (this|the)",
    r"should be (revised|updated|improved)",
    r"needs (to be|clarification|improvement)",
    r"consider (rewrit|updat|chang)",
    r"please (rewrite|update|revise)",
    r"^\s*n/?a\s*$",
    r"^\s*tbd\s*$",
    r"^\s*to be (determined|confirmed|added)\s*$",
]

_GENERIC_RE = re.compile("|".join(GENERIC_SOLUTION_PATTERNS), re.IGNORECASE)

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_URL_RE = re.compile(r"https?://[^\s\[\]]+")
_ARABIC_INDIC_RE = re.compile(r"[٠-٩]")
_WESTERN_DIG_RE = re.compile(r"[0-9]")
_DOUBLE_SPACE_RE = re.compile(r"  +")
# Space before punctuation — excludes fee price patterns like "0.5BD : for ..."
# and French-style spacing before : ; ? ! which appears in some government docs
_SPACE_PUNCT_RE = re.compile(r"\w +[.,;!?]")  # colon excluded — valid in fee lines


def _snippet(text: str, n: int = 60) -> str:
    return (text[:n] + "…") if len(text) > n else text


def _make_issue(
    rule_id: str,
    section: str,
    language: str,
    placement: str,
    description: str,
    solution: str,
) -> dict:
    return {
        "rule_id": rule_id,
        "section": section,
        "language": language,
        "issue_placement": placement,
        "issue_description": description,
        "proposed_solution": solution,
    }


# ─────────────────────────────────────────────────────────────
# ARABIC CHECKS
# ─────────────────────────────────────────────────────────────
def check_hamza(ar_data: dict) -> list[dict]:
    """AR001 — Detect Hamza errors in all Arabic section content."""
    issues = []
    all_text = " ".join(v for v in ar_data.values() if isinstance(v, str) and v)

    words = re.findall(r"[\u0600-\u06FF]+", all_text)
    seen = set()

    for word in words:
        if word in seen:
            continue
        seen.add(word)
        correct = HAMZA_ERRORS.get(word)
        if not correct:
            continue

        # Find which section contains the error
        section = "Linguistic"
        for field, val in ar_data.items():
            if isinstance(val, str) and word in val:
                section = _field_to_section(field)
                break

        # Detect whether this is a Taa Marbuta error (ه→ة) or a Hamza error
        if word.endswith("ه") and correct.endswith("ة"):
            issue_type = "Taa Marbuta error"
            desc = (
                f"Taa Marbuta error: '{word}' ends with Haa (ه) instead of "
                f"Taa Marbuta (ة). The correct form is '{correct}'."
            )
        elif "لإست" in word or "بإست" in word:
            issue_type = "Hamza error"
            desc = (
                f"Hamza error: '{word}' has an incorrect Hamza after a preposition. "
                f"The correct form is '{correct}'."
            )
        else:
            issue_type = "Hamza error"
            desc = (
                f"Hamza error: '{word}' is incorrectly written without Hamza. "
                f"The correct form is '{correct}'."
            )
        issues.append(
            _make_issue(
                rule_id="AR001",
                section=section,
                language="AR",
                placement=f'{section}: "{word}"',
                description=desc,
                solution=f"Replace '{word}' with '{correct}' throughout the Arabic content.",
            )
        )

    return issues


def check_alif_maqsura(ar_data: dict) -> list[dict]:
    """AR002 — Detect Alif Maqsura / Yaa confusion."""
    issues = []
    all_text = " ".join(v for v in ar_data.values() if isinstance(v, str) and v)

    words = re.findall(r"[\u0600-\u06FF]+", all_text)
    seen = set()

    for word in words:
        if word in seen:
            continue
        seen.add(word)
        correct = ALIF_MAQSURA_ERRORS.get(word)
        if not correct:
            continue

        section = "Linguistic"
        for field, val in ar_data.items():
            if isinstance(val, str) and word in val:
                section = _field_to_section(field)
                break

        issues.append(
            _make_issue(
                rule_id="AR002",
                section=section,
                language="AR",
                placement=f'{section}: "{word}"',
                description=(
                    f"Alif Maqsura error: '{word}' should be written with ى not ي. "
                    f"The correct form is '{correct}'."
                ),
                solution=f"Replace '{word}' with '{correct}'.",
            )
        )

    return issues


def check_arabic_comma(ar_data: dict) -> list[dict]:
    """AR003 — Latin comma inside Arabic text (should be Arabic comma ،)."""
    issues = []
    for field, val in ar_data.items():
        if not isinstance(val, str) or not val:
            continue
        if not _ARABIC_RE.search(val):
            continue
        # Find Latin comma surrounded by or following Arabic text
        matches = re.finditer(r"(?<=[\u0600-\u06FF\s]),|,(?=\s*[\u0600-\u06FF])", val)
        for m in matches:
            snippet = val[max(0, m.start() - 20) : m.start() + 20].strip()
            section = _field_to_section(field)
            issues.append(
                _make_issue(
                    rule_id="AR003",
                    section=section,
                    language="AR",
                    placement=f'{section}: "{_snippet(snippet)}"',
                    description="Latin comma (,) used inside Arabic text. Arabic text requires the Arabic comma (،).",
                    solution="Replace the Latin comma (,) with the Arabic comma (،).",
                )
            )
            break  # one issue per section is enough

    return issues


def check_mixed_numerals(ar_data: dict) -> list[dict]:
    """AR004 — Arabic-Indic numerals mixed with Western digits."""
    issues = []
    for field, val in ar_data.items():
        if not isinstance(val, str) or not val:
            continue
        if _ARABIC_INDIC_RE.search(val) and _WESTERN_DIG_RE.search(val):
            section = _field_to_section(field)
            issues.append(
                _make_issue(
                    rule_id="AR004",
                    section=section,
                    language="AR",
                    placement=f'{section}: "{_snippet(val)}"',
                    description="Mixed numeral styles: Arabic-Indic (٠١٢) and Western (012) digits appear together. Use one style consistently.",
                    solution="Standardize to Western digits (0–9) throughout Arabic content.",
                )
            )

    return issues


# ─────────────────────────────────────────────────────────────
# ENGLISH CHECKS
# ─────────────────────────────────────────────────────────────
def check_raw_emails(en_data: dict, ar_data: dict) -> list[dict]:
    """EN001 — Raw email addresses not wrapped in mailto: hyperlinks."""
    issues = []
    for lang, data in [("EN", en_data), ("AR", ar_data)]:
        for field, val in data.items():
            if not isinstance(val, str) or not val:
                continue
            # Skip if already inside a [LINK:...] annotation
            text = re.sub(r"\[LINK:[^\]]+\]", "", val)
            for m in _EMAIL_RE.finditer(text):
                email = m.group()
                section = _field_to_section(field)
                issues.append(
                    _make_issue(
                        rule_id="EN001",
                        section=section,
                        language=lang,
                        placement=f'{section}: "{email}"',
                        description=f"Email address '{email}' appears as plain text. It should be a clickable mailto: link.",
                        solution=f'Make "{email}" a clickable hyperlink to mailto:{email}',
                    )
                )

    return issues


def check_raw_urls(en_data: dict, ar_data: dict) -> list[dict]:
    """EN002 — Raw URLs appearing as plain visible text in content fields.

    Deduplicates: same URL appearing in multiple fields = one issue.
    Solution uses surrounding context to suggest a descriptive label.
    """
    issues = []
    seen_urls = set()  # deduplicate across fields and languages

    CONTENT_FIELDS = {
        "service_name", "service_description", "required_attachments",
        "legal_regulations", "fees", "process_time", "service_provider",
        "service_processes", "service_conditions",
    }

    # Build a label from URL domain for the solution
    def _url_label(url: str) -> str:
        """Suggest a human-readable label from the URL."""
        clean = re.sub(r"[),.\']+$", "", url)  # strip trailing punctuation
        try:
            from urllib.parse import urlparse
            parts = urlparse(clean)
            domain = parts.netloc.replace("www.", "")
            # Use domain as label basis
            label_map = {
                "subsidies.gov.bh": "Bahrain Subsidies Portal",
                "dur.nhra.bh": "DUR System",
                "sijilat.bh": "Sijilat Business Portal",
                "lmra.bh": "Labour Market Regulatory Authority",
                "bahrain.bh": "Bahrain National Portal",
                "health.bh": "National Health Portal",
                "mawaeed.bh": "Mawaeed Appointment App",
                "legalaffairs.gov.bh": "Bahrain Legislation Portal",
                "npra.gov.bh": "Nationality, Passports & Residence Affairs",
            }
            for key, label in label_map.items():
                if key in domain:
                    return label
            # Fallback: capitalize domain
            return domain.split(".")[0].replace("-", " ").title() + " Website"
        except Exception:
            return "the referenced website"

    for lang, data in [("EN", en_data), ("AR", ar_data)]:
        for field, val in data.items():
            if field not in CONTENT_FIELDS:
                continue
            if not isinstance(val, str) or not val:
                continue
            # Remove already-linked URLs
            text = re.sub(r"\[LINK:[^\]]+\]", "", val)
            for m in _URL_RE.finditer(text):
                url = m.group()
                # Strip trailing punctuation from URL
                clean_url = re.sub(r"[),.\']+$", "", url)
                if "GSX-UI-PServiceDetails" in url or "GSX-UI-EServiceDetails" in url:
                    continue
                if "contenthandler" in url or "wps/portal" in url:
                    continue
                if clean_url in seen_urls:
                    continue
                seen_urls.add(clean_url)

                section = _field_to_section(field)
                label = _url_label(clean_url)
                issues.append(
                    _make_issue(
                        rule_id="EN002",
                        section=section,
                        language=lang,
                        placement=f'{section}: "{_snippet(clean_url, 50)}"',
                        description=(
                            f"The URL '{_snippet(clean_url, 50)}' appears as plain text. "
                            "It should be replaced with a descriptive hyperlink so users "
                            "know where the link leads before clicking."
                        ),
                        solution=(
                            f"Replace the raw URL with a descriptive hyperlink. "
                            f"Suggested label: \"{label}\". "
                            f"Portal format: \"{label} [LINK:{clean_url}]\""
                        ),
                    )
                )

    return issues


def check_skiplino(en_data: dict, ar_data: dict) -> list[dict]:
    """EN003 — Skiplino reference — highlight for manual verification."""
    issues = []
    all_en = " ".join(v for v in en_data.values() if isinstance(v, str))
    all_ar = " ".join(v for v in ar_data.values() if isinstance(v, str))

    if re.search(r"skiplino", all_en, re.IGNORECASE) or re.search(
        r"skiplino", all_ar, re.IGNORECASE
    ):
        issues.append(
            _make_issue(
                rule_id="EN003",
                section="Deprecated",
                language="Both",
                placement='Service Processes: "Skiplino"',
                description=(
                    "'Skiplino' is referenced in this service. "
                    "Please verify whether it is still actively in use. "
                    "If it has been replaced by Mawaeed or another system, update accordingly."
                ),
                solution=(
                    "Verify if Skiplino is still in use for this service. "
                    "If replaced, update the reference to the current queue management system (e.g. Mawaeed)."
                ),
            )
        )

    return issues


def check_double_spaces(en_data: dict) -> list[dict]:
    """EN004 — Double spaces in English text."""
    issues = []
    for field, val in en_data.items():
        if not isinstance(val, str) or not val:
            continue
        if _DOUBLE_SPACE_RE.search(val):
            section = _field_to_section(field)
            issues.append(
                _make_issue(
                    rule_id="EN004",
                    section=section,
                    language="EN",
                    placement=f'{section}: "{_snippet(val)}"',
                    description="Double space detected in English text.",
                    solution="Remove the extra space.",
                )
            )

    return issues


def check_space_before_punctuation(en_data: dict) -> list[dict]:
    """EN005 — Space before punctuation mark (excludes fees and legal fields where
    spacing around colons and punctuation is conventional)."""
    # Fields where space-before-punctuation is a valid formatting convention
    _EXCLUDED_FIELDS = {"fees", "legal_regulations", "process_time"}
    issues = []
    for field, val in en_data.items():
        if field in _EXCLUDED_FIELDS:
            continue
        if not isinstance(val, str) or not val:
            continue
        m = _SPACE_PUNCT_RE.search(val)
        if m:
            section = _field_to_section(field)
            issues.append(
                _make_issue(
                    rule_id="EN005",
                    section=section,
                    language="EN",
                    placement=f'{section}: "{m.group()}"',
                    description="Space before punctuation mark detected.",
                    solution="Remove the space before the punctuation mark.",
                )
            )

    return issues


# Title Case minor words (stay lowercase unless first word)
_TITLE_CASE_MINOR = {
    "a",
    "an",
    "the",
    "and",
    "but",
    "or",
    "for",
    "nor",
    "of",
    "in",
    "on",
    "at",
    "to",
    "by",
    "up",
    "as",
    "is",
}


def _is_title_case(name: str) -> bool:
    """Return True if the name follows Title Case rules."""
    words = name.split()
    for i, word in enumerate(words):
        # Strip leading punctuation/quotes for check
        core = word.lstrip("\"'(").rstrip("\"')")
        if not core:
            continue
        if i == 0:
            # First word must always be capitalized
            if core[0].islower():
                return False
        else:
            if core.lower() in _TITLE_CASE_MINOR:
                # Minor words should be lowercase
                if core[0].isupper() and len(core) > 1:
                    pass  # acceptable either way
            else:
                # All other words must start with capital
                if core[0].islower():
                    return False
    return True


def _to_title_case(name: str) -> str:
    """Convert a service name to proper Title Case."""
    words = name.split()
    result = []
    for i, word in enumerate(words):
        if i == 0 or word.lower() not in _TITLE_CASE_MINOR:
            result.append(word.capitalize())
        else:
            result.append(word.lower())
    return " ".join(result)


def check_service_name_title_case(en_data: dict) -> list[dict]:
    """EN006 — EN service name not in Title Case."""
    name = en_data.get("service_name", "")
    if not name or len(name.split()) < 2:
        return []
    # Skip if already all-caps (acronym-style names)
    if name.isupper():
        return []
    if not _is_title_case(name):
        suggested = _to_title_case(name)
        return [
            _make_issue(
                rule_id="EN006",
                section="Service Name",
                language="EN",
                placement=f'Service Name: "{name}"',
                description=(
                    f"Service name '{name}' does not follow Title Case. "
                    "Each major word should start with a capital letter."
                ),
                solution=f'Use Title Case: "{suggested}"',
            )
        ]
    return []


# Keywords that indicate eligibility conditions / restrictions
_CONDITION_PATTERNS_EN = re.compile(
    r"\b(must|only if|provided that|in case of|eligible|requirement|"
    r"condition|restricted to|exclusively for|limited to|applicable to|"
    r"subject to|upon|unless|except)\b",
    re.IGNORECASE,
)

_CONDITION_PATTERNS_AR = re.compile(
    r"(يشترط|بشرط|في حال|الشروط|شرط|مقتصر|حصراً|يقتصر|"
    r"يستوجب|ما لم|إلا إذا|فقط إذا|يُشترط)",
)


def check_conditions_in_name_or_description(en_data: dict, ar_data: dict) -> list[dict]:
    """EN007 — Eligibility conditions found inside service name or description."""
    issues = []

    for lang, data, pattern in [
        ("EN", en_data, _CONDITION_PATTERNS_EN),
        ("AR", ar_data, _CONDITION_PATTERNS_AR),
    ]:
        for field in ("service_name", "service_description"):
            val = data.get(field, "")
            if not val:
                continue
            m = pattern.search(val)
            if m:
                section = (
                    "Service Name" if field == "service_name" else "Service Description"
                )
                snippet = val[:80] + ("…" if len(val) > 80 else "")
                issues.append(
                    _make_issue(
                        rule_id="EN007",
                        section=section,
                        language=lang,
                        placement=f'{section}: "{snippet}"',
                        description=(
                            f"The {section.lower()} appears to contain eligibility conditions "
                            f"or restrictions (e.g. '{m.group()}'). "
                            "Conditions should be placed under 'Service Conditions', "
                            "not in the name or description."
                        ),
                        solution=(
                            f"Remove the condition from the {section.lower()} "
                            "and move it to the 'Service Conditions' section."
                        ),
                    )
                )

    return issues


# ─────────────────────────────────────────────────────────────
# BILINGUAL CHECKS
# ─────────────────────────────────────────────────────────────
def check_empty_sections(en_data: dict, ar_data: dict) -> list[dict]:
    """BI001 — Sections empty in both or one language.
    
    Groups all empty-in-both sections into a single issue to avoid
    cluttering the report with one row per empty field.
    """
    fields = [
        "required_attachments",
        "legal_regulations",
        "fees",
        "process_time",
        "service_provider",
        "service_processes",
    ]

    both_empty = []   # empty in EN and AR
    en_only_empty = []  # has AR but missing EN
    ar_only_empty = []  # has EN but missing AR

    for field in fields:
        en_val = en_data.get(field, "")
        ar_val = ar_data.get(field, "")
        section = _field_to_section(field)
        if not en_val and not ar_val:
            both_empty.append(section)
        elif not en_val:
            en_only_empty.append(section)
        elif not ar_val:
            ar_only_empty.append(section)

    issues = []

    if both_empty:
        names = ", ".join(both_empty)
        issues.append(
            _make_issue(
                rule_id="BI001",
                section=both_empty[0],
                language="Both",
                placement=" | ".join(f"{s}: (empty)" for s in both_empty),
                description=(
                    f"The following section(s) are empty in both English and Arabic: {names}. "
                    "Missing content may confuse users or reduce trust in the service page."
                ),
                solution=(
                    f"Add the relevant content to each empty section. "
                    "If a section is genuinely not applicable, state 'Not applicable' / 'لا ينطبق'."
                ),
            )
        )

    if en_only_empty:
        names = ", ".join(en_only_empty)
        issues.append(
            _make_issue(
                rule_id="BI001",
                section=en_only_empty[0],
                language="EN",
                placement=" | ".join(f"{s}: (empty in EN)" for s in en_only_empty),
                description=f"The following section(s) are empty in English but have Arabic content: {names}.",
                solution="Add the English equivalent of the Arabic content to each affected section.",
            )
        )

    if ar_only_empty:
        names = ", ".join(ar_only_empty)
        issues.append(
            _make_issue(
                rule_id="BI001",
                section=ar_only_empty[0],
                language="AR",
                placement=" | ".join(f"{s}: (empty in AR)" for s in ar_only_empty),
                description=f"The following section(s) are empty in Arabic but have English content: {names}.",
                solution="Add the Arabic equivalent of the English content to each affected section.",
            )
        )

    return issues


def check_process_time_mismatch(en_data: dict, ar_data: dict) -> list[dict]:
    """BI002 — Process time value mismatch between EN and AR."""
    issues = []
    en_t = en_data.get("process_time", "")
    ar_t = ar_data.get("process_time", "")

    if not en_t or not ar_t:
        return issues

    # Extract numeric values
    en_nums = re.findall(r"\d+", en_t)
    # Normalize Arabic-Indic digits
    ar_normalized = ar_t
    for ar_d, w_d in zip("٠١٢٣٤٥٦٧٨٩", "0123456789"):
        ar_normalized = ar_normalized.replace(ar_d, w_d)
    ar_nums = re.findall(r"\d+", ar_normalized)

    if en_nums and ar_nums and sorted(en_nums) != sorted(ar_nums):
        issues.append(
            _make_issue(
                rule_id="BI002",
                section="Process Time",
                language="Both",
                placement=f'Process Time: EN="{en_t}" | AR="{ar_t}"',
                description=f"Process time mismatch: English states '{en_t}' but Arabic states '{ar_t}'. These must be identical.",
                solution=f"Align the process time values. Correct value should appear consistently in both languages.",
            )
        )

    return issues


# Patterns that indicate legal reference text inside fees — numbers here are NOT fee amounts
_LEGAL_REF_RE = re.compile(
    r"(?:ملحقة|بموجب|وفقاً|استناداً|القرار|المرسوم|رقم|لسنة|بشأن)"
    r"[\s\d\(\)]+",
    re.UNICODE,
)

# Fee line separator — fee items are separated by | or newlines or bullet points
_FEE_LINE_RE = re.compile(r"[|\n•*،,]+")

# A fee amount is a number at the START of a fee line item (before :)
# e.g. "0.5 BD Per category : description" or "0.5 د.ب لكل صنف : وصف"
_FEE_AMOUNT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)")


def _extract_fee_amounts(text: str) -> list[float]:
    """
    Extract only genuine fee amounts from a fees field.

    Strategy:
    1. Strip legal reference footnotes (القرار / المرسوم / لسنة N) which
       contain numbers that are NOT fee amounts.
    2. Split into individual fee line items.
    3. From each line, extract only the FIRST number (the amount before the colon).

    This prevents decree numbers like "24" in "ملحقة بالقرار 24 لسنة 21"
    from being mistaken for fee amounts.
    """
    # Convert Arabic-Indic digits first
    for ar_d, w_d in zip("٠١٢٣٤٥٦٧٨٩", "0123456789"):
        text = text.replace(ar_d, w_d)

    # Remove legal reference fragments so their numbers are not extracted
    text = _LEGAL_REF_RE.sub(" ", text)

    amounts = []
    for line in _FEE_LINE_RE.split(text):
        line = line.strip()
        if not line:
            continue
        m = _FEE_AMOUNT_RE.match(line)
        if m:
            amounts.append(float(m.group(1)))

    return amounts


def _normalise_fee_to_fils(text: str) -> list[int]:
    """
    Extract fee amounts and normalise to fils (1/1000 BD) for comparison.

    Arabic commonly writes 0.5 BD as 500 فلس, while English writes 0.5 BD.
    Both represent the same amount — normalising prevents false positives.
    Uses _extract_fee_amounts to avoid picking up decree/resolution numbers.
    """
    fils_context = bool(re.search(r"فلس|fils|fill", text, re.IGNORECASE))
    amounts = _extract_fee_amounts(text)

    normalised = []
    for n in amounts:
        if n < 1 or (n < 10 and n != int(n)):
            # Decimal BD value (e.g. 0.5) → convert to fils
            normalised.append(round(n * 1000))
        elif fils_context and n >= 100:
            # Already expressed in fils
            normalised.append(round(n))
        else:
            # Whole BD value → convert to fils
            normalised.append(round(n * 1000))

    return sorted(normalised)


def check_fee_mismatch(en_data: dict, ar_data: dict) -> list[dict]:
    """BI003 — Fee amount mismatch between EN and AR.

    Normalises both EN and AR amounts to fils before comparing so that
    equivalent expressions like 0.5 BD (EN) and 500 فلس (AR) do not
    produce false positives. Legal reference numbers embedded in AR fee
    footnotes (e.g. القرار 24 لسنة 21) are excluded from comparison.
    """
    issues = []
    en_f = en_data.get("fees", "")
    ar_f = ar_data.get("fees", "")

    if not en_f or not ar_f:
        return issues

    en_amounts = _normalise_fee_to_fils(en_f)
    ar_amounts = _normalise_fee_to_fils(ar_f)

    if en_amounts and ar_amounts and en_amounts != ar_amounts:
        issues.append(
            _make_issue(
                rule_id="BI003",
                section="Fees",
                language="Both",
                placement=f'Fees: EN="{_snippet(en_f, 50)}" | AR="{_snippet(ar_f, 50)}"',
                description=(
                    f"Fee amount mismatch between English and Arabic versions. "
                    f"EN amounts (BD): {sorted(set(a/1000 for a in en_amounts))} | "
                    f"AR amounts (BD): {sorted(set(a/1000 for a in ar_amounts))}."
                ),
                solution="Verify the correct fee amounts and align both language versions to show identical values.",
            )
        )

    return issues


# ─────────────────────────────────────────────────────────────
# FIELD → SECTION NAME MAPPING
# ─────────────────────────────────────────────────────────────
_FIELD_SECTION_MAP = {
    "service_name": "Service Name",
    "service_description": "Service Description",
    "required_attachments": "Required Attachments",
    "legal_regulations": "Legal Regulations",
    "fees": "Fees",
    "process_time": "Process Time",
    "service_provider": "Service Provider",
    "service_processes": "Service Processes",
    "service_conditions": "Service Conditions",
}


def _field_to_section(field: str) -> str:
    return _FIELD_SECTION_MAP.get(field, "Service Description")




# ─────────────────────────────────────────────────────────────
# KNOWN SYSTEM LINK MAP
# If any of these system names appear as plain text (no [LINK:...] after them)
# in the service processes or description, flag it with the known URL.
# ─────────────────────────────────────────────────────────────
# EN systems (matched in en_data)
_KNOWN_SYSTEMS_EN = {
    "DUR system": "https://dur.nhra.bh",
    "Drug Utilisation Review": "https://dur.nhra.bh",
    "Sijilat": "https://www.sijilat.bh",
    "Labour Market Regulatory Authority": "https://www.lmra.bh",
    "LMRA": "https://www.lmra.bh",
    "National Health Portal": "https://www.health.bh",
    "Mawaeed app": "https://www.mawaeed.bh",
    "Tadbeer": "https://www.tadbeer.gov.bh",
    "Afaq": "https://sew.ofoq2.gov.bh/TFBSEW2/cusLogin/login.cl?lang=en",
}

# AR systems (matched in ar_data) — Arabic system names without links
# Value is (url, english_name) so descriptions are always in English
_KNOWN_SYSTEMS_AR = {
    "نظام أفق": ("https://sew.ofoq2.gov.bh/TFBSEW2/cusLogin/login.cl?lang=en", "Afaq system"),
    "أفق": ("https://sew.ofoq2.gov.bh/TFBSEW2/cusLogin/login.cl?lang=en", "Afaq system"),
    "نظام DUR": ("https://dur.nhra.bh", "DUR system"),
    "نظام مراجعة استخدام الدواء": ("https://dur.nhra.bh", "DUR system"),
    "نظام سجلات": ("https://www.sijilat.bh", "Sijilat system"),
    "مواعيد": ("https://www.mawaeed.bh", "Mawaeed app"),
    "تدبير": ("https://www.tadbeer.gov.bh", "Tadbeer system"),
}

# Kept for backward compat
_KNOWN_SYSTEMS = _KNOWN_SYSTEMS_EN


def check_unlinked_systems(en_data: dict, ar_data: dict) -> list[dict]:
    """EN007/AR007 — Known external systems referenced without a hyperlink.

    Deduplicates by (field, url, language) so that aliases for the same system
    (e.g. 'DUR system' and 'Drug Utilisation Review') produce only ONE issue
    per field per language, not one per alias.
    """
    issues = []
    fields = ["service_processes", "service_description", "service_conditions"]

    # EN content — one issue per (field, url) max
    for field in fields:
        val = en_data.get(field, "") or ""
        if not val:
            continue
        flagged_urls = set()
        for system_name, system_url in _KNOWN_SYSTEMS_EN.items():
            if system_url in flagged_urls:
                continue
            if system_name.lower() not in val.lower():
                continue
            idx = val.lower().find(system_name.lower())
            context = val[max(0, idx - 5):idx + len(system_name) + 60]
            if "[LINK:" in context:
                continue
            flagged_urls.add(system_url)
            section = _field_to_section(field)
            issues.append(
                _make_issue(
                    rule_id="EN007",
                    section=section,
                    language="EN",
                    placement=f'{section}: "{_snippet(val[max(0,idx-10):idx+len(system_name)+10])}"',
                    description=(
                        f"'{system_name}' is referenced without a hyperlink. "
                        "Users cannot access it without a direct link."
                    ),
                    solution=f"Add a hyperlink to '{system_name}': {system_url}",
                )
            )

    # AR content — one issue per (field, url) max
    for field in fields:
        val = ar_data.get(field, "") or ""
        if not val:
            continue
        flagged_urls = set()
        for system_name, (system_url, english_name) in _KNOWN_SYSTEMS_AR.items():
            if system_url in flagged_urls:
                continue
            if system_name not in val:
                continue
            idx = val.find(system_name)
            context = val[max(0, idx - 5):idx + len(system_name) + 60]
            if "[LINK:" in context:
                continue
            flagged_urls.add(system_url)
            section = _field_to_section(field)
            issues.append(
                _make_issue(
                    rule_id="AR007",
                    section=section,
                    language="AR",
                    placement=f'{section}: "{_snippet(val[max(0,idx-10):idx+len(system_name)+10])}"',
                    description=(
                        f"The Arabic content references '{english_name}' without a hyperlink. "
                        "Users cannot access it without a direct link."
                    ),
                    solution=f"Add a hyperlink to '{english_name}' in the Arabic content: {system_url}",
                )
            )

    return issues



# ─────────────────────────────────────────────────────────────
# NEW DETERMINISTIC RULES
# ─────────────────────────────────────────────────────────────

# Vague attachment words that must always be flagged when standalone
_VAGUE_ATTACHMENTS_EN = {
    "invoice", "document", "documents", "certificate", "certificates",
    "form", "forms", "report", "reports", "letter", "letters",
    "copy", "copies", "paper", "papers", "record", "records",
    "supporting documents", "relevant documents", "other documents",
    "required documents", "additional documents",
}
_VAGUE_ATTACHMENTS_AR = {
    "فاتورة", "وثيقة", "وثائق", "شهادة", "شهادات",
    "استمارة", "نموذج", "نماذج", "تقرير", "تقارير",
    "خطاب", "رسالة",
    "مستندات داعمة", "مستندات ذات صلة", "وثائق أخرى",
    "مستندات مطلوبة", "مستندات إضافية",
}

# Conditional attachment trigger phrases
_CONDITIONAL_TRIGGERS_EN = [
    "if the request is for", "if applicable", "where applicable",
    "for government", "for private", "for companies", "when required",
    "only for", "in case of", "if needed",
]
_CONDITIONAL_TRIGGERS_AR = [
    "في حال", "عند الطلب", "إذا كان", "للجهات الحكومية",
    "للشركات", "عند الحاجة", "في حالة", "إن وجد",
]

# Law/regulation patterns that should have hyperlinks
_LAW_PATTERN_EN = re.compile(
    r"(?:Ministerial Resolution|Decree.?Law|Law No\.|Resolution No\.|"
    r"Royal Decree|Legislative Decree|Decision No\.)\s*(?:No\.?)?\s*[\(\d]",
    re.IGNORECASE,
)
_LAW_PATTERN_AR = re.compile(
    r"(?:قرار وزاري|مرسوم بقانون|قانون رقم|قرار رقم|مرسوم ملكي|"
    r"مرسوم رقم|لائحة رقم|نظام رقم)\s*(?:رقم)?\s*[\(\d]"
)

# Process time valid formats
_PROCESS_TIME_EN = re.compile(
    r"^\s*(?:immediate|\d+\s+working\s+day(?:s)?|\d+\s+day(?:s)?)\s*$",
    re.IGNORECASE,
)
_PROCESS_TIME_AR = re.compile(
    r"^\s*(?:فوري|\d+\s+(?:يوم|أيام)\s+عمل|\d+\s+(?:يوم|أيام))\s*$"
)

# Description mandatory start phrases
_DESC_START_EN = ("this service allows", "this service enables")
_DESC_START_AR = ("تتيح هذه الخدمة", "تمكّن هذه الخدمة", "تمكن هذه الخدمة")


def check_description_start(en_data: dict, ar_data: dict) -> list[dict]:
    """EN008 — Service description must start with standard phrase."""
    issues = []

    en_desc = (en_data.get("service_description") or "").strip()
    ar_desc = (ar_data.get("service_description") or "").strip()

    if en_desc and not any(en_desc.lower().startswith(p) for p in _DESC_START_EN):
        issues.append(_make_issue(
            rule_id="EN008",
            section="Service Description",
            language="EN",
            placement=f'Service Description: "{_snippet(en_desc, 60)}"',
            description=(
                "EN service description does not start with the required phrase "
                "'This service allows' or 'This service enables'."
            ),
            solution=(
                "Rewrite to start with: 'This service allows [target audience] to [action].' "
                f"Current text: {_snippet(en_desc, 80)}"
            ),
        ))

    return issues


def check_vague_attachments(en_data: dict, ar_data: dict) -> list[dict]:
    """EN009 — Vague attachment names that don't tell users what to bring."""
    issues = []

    def _check(val: str, lang: str, vague_set: set) -> list:
        found = []
        if not val:
            return found
        for item in val.split("|"):
            item_clean = item.strip().lower().rstrip(".")
            if item_clean in vague_set:
                found.append(item.strip())
        return found

    en_att = en_data.get("required_attachments", "") or ""
    ar_att = ar_data.get("required_attachments", "") or ""

    en_vague = _check(en_att, "EN", _VAGUE_ATTACHMENTS_EN)
    ar_vague = _check(ar_att, "AR", _VAGUE_ATTACHMENTS_AR)

    if en_vague:
        issues.append(_make_issue(
            rule_id="EN009",
            section="Required Attachments",
            language="EN",
            placement=f'Required Attachments: "{" | ".join(en_vague)}"',
            description=(
                f"Vague attachment name(s): {', '.join(repr(v) for v in en_vague)}. "
                "Each attachment must specify exactly what document is needed — "
                "users cannot act on a name like 'Invoice' or 'Document'."
            ),
            solution=(
                "Replace each vague name with a specific one. Examples: "
                "'Invoice' → 'Supplier invoice for the imported goods'; "
                "'Certificate' → 'Valid import permit certificate'; "
                "'Document' → specify the exact document name."
            ),
        ))

    if ar_vague:
        issues.append(_make_issue(
            rule_id="AR009",
            section="Required Attachments",
            language="AR",
            placement=f'المستندات المطلوبة: "{" | ".join(ar_vague)}"',
            description=(
                f"Vague attachment name(s) in Arabic: {', '.join(repr(v) for v in ar_vague)}. "
                "Each attachment must clearly specify what document is required."
            ),
            solution=(
                "Replace each vague name with a specific descriptive name in Arabic."
            ),
        ))

    return issues


def check_conditional_attachments(en_data: dict, ar_data: dict) -> list[dict]:
    """EN010 — Conditional attachments must be explicitly labeled."""
    issues = []

    for lang, data, triggers in [
        ("EN", en_data, _CONDITIONAL_TRIGGERS_EN),
        ("AR", ar_data, _CONDITIONAL_TRIGGERS_AR),
    ]:
        val = data.get("required_attachments", "") or ""
        if not val:
            continue
        for item in val.split("|"):
            item = item.strip()
            if not any(t.lower() in item.lower() for t in triggers):
                continue
            item_lower = item.lower()
            if "required for" in item_lower and "only" in item_lower:
                continue
            already_labeled = any(w in item for w in [
                "إن وجد", "ان وجد", "إن وجدت", "ان وجدت",
                "عند الحاجة", "إذا وجد", "إذا توفر",
                "في حال توفر", "(مطلوب", "(مطلوبة",
                "if applicable", "where applicable", "if available",
            ])
            if already_labeled:
                continue
            issues.append(_make_issue(
                rule_id="EN010" if lang == "EN" else "AR010",
                section="Required Attachments",
                language=lang,
                placement=f'Required Attachments: "{_snippet(item, 60)}"',
                description=(
                    f"Conditional attachment not explicitly labeled: '{_snippet(item, 60)}'. "
                    "Conditional attachments must use the format: "
                    "'[Document name] (required for [specific group] only)'."
                ),
                solution=(
                    f"Rewrite as: '[Document name] (required for [specific group] only)'. "
                    f"Example: '{_snippet(item.split('if')[0].strip(), 40)} "
                    f"(required for [specific applicant group] only)'."
                ),
            ))
            break

    return issues


def check_missing_regulation_links(en_data: dict, ar_data: dict) -> list[dict]:
    """EN011 — Regulations referenced without hyperlinks."""
    issues = []

    for lang, data, pattern in [
        ("EN", en_data, _LAW_PATTERN_EN),
        ("AR", ar_data, _LAW_PATTERN_AR),
    ]:
        val = data.get("legal_regulations", "") or ""
        if not val:
            continue
        for item in val.split("|"):
            item = item.strip()
            if not item:
                continue
            if pattern.search(item) and "[LINK:" not in item:
                issues.append(_make_issue(
                    rule_id="EN011" if lang == "EN" else "AR011",
                    section="Legal Regulations",
                    language=lang,
                    placement=f'Legal Regulations: "{_snippet(item, 70)}"',
                    description=(
                        f"[Missing Link] Regulation '{_snippet(item, 60)}' "
                        "is referenced as plain text without a hyperlink."
                    ),
                    solution=(
                        f"Add a hyperlink to '{_snippet(item, 60)}' pointing to the "
                        "official Bahrain legislation portal: https://www.legalaffairs.gov.bh"
                    ),
                ))

    return issues


def check_process_time_format(en_data: dict, ar_data: dict) -> list[dict]:
    """EN012 — Process time must follow standard format."""
    issues = []

    en_pt = (en_data.get("process_time") or "").strip()
    ar_pt = (ar_data.get("process_time") or "").strip()

    if en_pt and not _PROCESS_TIME_EN.match(en_pt):
        issues.append(_make_issue(
            rule_id="EN012",
            section="Process Time",
            language="EN",
            placement=f'Process Time: "{en_pt}"',
            description=(
                f"Process time '{en_pt}' does not follow the standard format. "
                "Expected: 'N Working Day(s)' or 'Immediate'."
            ),
            solution=(
                f"Rewrite as: '{en_pt.strip()} Working Day(s)' if it represents working days, "
                "or 'Immediate' if the service is instant. Do not change the number."
            ),
        ))

    if ar_pt and not _PROCESS_TIME_AR.match(ar_pt):
        issues.append(_make_issue(
            rule_id="AR012",
            section="Process Time",
            language="AR",
            placement=f'وقت الإنجاز: "{ar_pt}"',
            description=(
                f"Process time '{ar_pt}' does not follow the standard format. "
                "Expected: 'N يوم/أيام عمل' or 'فوري'."
            ),
            solution=(
                f"Rewrite as: '{ar_pt.strip()} يوم عمل' or 'فوري' if instant. "
                "Do not change the number."
            ),
        ))

    return issues


def check_last_process_step(en_data: dict, ar_data: dict) -> list[dict]:
    """EN013 — Last process step must state the outcome."""
    issues = []

    _OUTCOME_WORDS_EN = {
        "approved", "issued", "notified", "received", "completed",
        "granted", "processed", "delivered", "confirmed", "done",
        "sent", "provided", "uploaded", "registered",
    }
    _OUTCOME_WORDS_AR = {
        "صدور", "إشعار", "اعتماد", "استلام", "إصدار",
        "منح", "إتمام", "تسليم", "تأكيد", "إرسال",
        "تسجيل", "قبول", "معالجة",
    }

    for lang, data, outcome_words in [
        ("EN", en_data, _OUTCOME_WORDS_EN),
        ("AR", ar_data, _OUTCOME_WORDS_AR),
    ]:
        val = data.get("service_processes", "") or ""
        if not val:
            continue
        steps = [s.strip() for s in val.split("|") if s.strip()]
        if not steps:
            continue
        last_step = steps[-1].lower()
        if not any(w in last_step for w in outcome_words):
            issues.append(_make_issue(
                rule_id="EN013" if lang == "EN" else "AR013",
                section="Service Processes",
                language=lang,
                placement=f'Service Processes (last step): "{_snippet(steps[-1], 60)}"',
                description=(
                    "The last process step does not state what happens after submission. "
                    "Users need to know: will they be notified? How long does it take? "
                    "Will they receive approval/rejection?"
                ),
                solution=(
                    "Add a final step explaining the outcome, e.g.: "
                    "'The application will be reviewed and the applicant will be notified "
                    "of the decision via [email/SMS/portal] within [N] working days.'"
                ),
            ))

    return issues


# ─────────────────────────────────────────────────────────────
# ATTACHMENT QUALITY RULES
# ─────────────────────────────────────────────────────────────

# Link text patterns that imply viewing but the URL downloads a file
_VIEW_LINK_PATTERNS_EN = re.compile(
    r"view\s+(document|doc|file|form|attachment|certificate)",
    re.IGNORECASE,
)
_VIEW_LINK_PATTERNS_AR = re.compile(
    r"عرض\s+(المستند|الوثيقة|الملف|النموذج|الاستمارة|الشهادة)"
)
_DOWNLOAD_EXTENSIONS = re.compile(
    r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx|zip|rar)(\?|$)",
    re.IGNORECASE,
)
# Detect [LINK:url] annotations
_LINK_ANNOTATION = re.compile(r"\[LINK:([^\]]+)\]")


def check_mislabeled_download_links(en_data: dict, ar_data: dict) -> list[dict]:
    """EN014 — Attachment links labeled 'View Document' that actually download a file.

    If the link text says 'View' but the URL points to a downloadable file (PDF, DOC...),
    the label is misleading. Users should know they are downloading, and should be told
    the file type, size, and page count where possible.
    """
    issues = []

    for lang, data, view_pattern in [
        ("EN", en_data, _VIEW_LINK_PATTERNS_EN),
        ("AR", ar_data, _VIEW_LINK_PATTERNS_AR),
    ]:
        val = data.get("required_attachments", "") or ""
        if not val:
            continue

        for item in val.split("|"):
            item = item.strip()
            if not item:
                continue

            # Check if item has a view-style label anywhere in text
            has_view_label = bool(view_pattern.search(item))

            # Also catch "View Document [LINK:url]" format from scraper
            # where link text IS "View Document" followed by the URL annotation
            if not has_view_label:
                has_view_label = bool(re.search(
                    r"view\s+[a-z]+\s+\[LINK:", item, re.IGNORECASE
                ))

            # Check if any embedded link points to a downloadable file
            links = _LINK_ANNOTATION.findall(item)
            has_download_link = any(_DOWNLOAD_EXTENSIONS.search(url) for url in links)

            # Also flag if link URL contains common document server paths
            if not has_download_link:
                has_download_link = any(
                    any(kw in url.lower() for kw in [
                        "download", "attachment", "document", "form", "file",
                        "getfile", "blob", "storage",
                    ])
                    for url in links
                )

            if has_view_label and (links or has_download_link):
                ext = ""
                for url in links:
                    m = _DOWNLOAD_EXTENSIONS.search(url)
                    if m:
                        ext = m.group(1).upper()
                        break
                issues.append(_make_issue(
                    rule_id="EN014" if lang == "EN" else "AR014",
                    section="Required Attachments",
                    language=lang,
                    placement=f'Required Attachments: "{_snippet(item, 60)}"',
                    description=(
                        f"The link is labeled 'View Document' but points to a downloadable "
                        f"{ext or 'file'}. This misleads users who expect to view inline. "
                        "If the file will remain downloadable, the label and metadata must reflect that."
                    ),
                    solution=(
                        f"Rename the link to 'Download [Form Name]' and state file metadata. "
                        f"Example: 'Download Application Form (PDF, 2 pages, 150 KB)'. "
                        "If inline viewing is possible, embed it as a viewer instead."
                    ),
                ))
                break  # one issue per language per section

    return issues


def check_attachment_upload_constraints(en_data: dict, ar_data: dict) -> list[dict]:
    """EN015 — Required attachments section lacks file metadata or upload constraints.

    Two scenarios handled separately:
    (a) Section has a download link but no file metadata (type, size, pages) →
        flag as missing download metadata
    (b) Section has upload items but no format/size constraints →
        flag as missing upload constraints
    """
    issues = []

    _FORMAT_KEYWORDS_EN = ["pdf", "jpg", "jpeg", "png", "doc", "format", "file type",
                           "accepted format", "supported format", "mb", "kb", "size limit",
                           "maximum size", "max size", "max file", "pages", "page"]
    _FORMAT_KEYWORDS_AR = ["pdf", "jpg", "png", "صيغة", "نوع الملف", "حجم الملف",
                           "الحجم الأقصى", "ميغابايت", "كيلوبايت", "mb", "kb",
                           "صفحة", "صفحات"]

    _DOWNLOAD_KEYWORDS_EN = ["download", "تحميل", "تنزيل"]
    _DOWNLOAD_KEYWORDS_AR = ["تحميل", "تنزيل", "download"]

    for lang, data, format_kws, download_kws in [
        ("EN", en_data, _FORMAT_KEYWORDS_EN, _DOWNLOAD_KEYWORDS_EN),
        ("AR", ar_data, _FORMAT_KEYWORDS_AR, _DOWNLOAD_KEYWORDS_AR),
    ]:
        val = data.get("required_attachments", "") or ""
        if not val or len(val.split("|")) < 1:
            continue

        val_lower = val.lower()
        has_metadata = any(kw in val_lower for kw in format_kws)
        has_download = any(kw in val_lower for kw in download_kws)

        if has_metadata:
            continue

        if has_download:
            issues.append(_make_issue(
                rule_id="EN015" if lang == "EN" else "AR015",
                section="Required Attachments",
                language=lang,
                placement=f'Required Attachments: "{_snippet(val, 60)}"',
                description=(
                    "A downloadable file is listed in the required attachments but its "
                    "metadata is not stated. Users need to know the file type, size, and "
                    "number of pages before downloading."
                ),
                solution=(
                    "Add file metadata next to the download link. "
                    "Example: 'Download Application Form (PDF, 2 pages, 150 KB)'. "
                    "This helps users know what they are downloading before clicking."
                ),
            ))
        else:
            items = [i.strip() for i in val.split("|") if i.strip()]
            if len(items) < 2:
                continue
            issues.append(_make_issue(
                rule_id="EN015" if lang == "EN" else "AR015",
                section="Required Attachments",
                language=lang,
                placement=f'Required Attachments: "{_snippet(val, 60)}"',
                description=(
                    "The required attachments section does not specify accepted file formats "
                    "or maximum file size per upload. Users may encounter upload errors "
                    "without this information."
                ),
                solution=(
                    "Add upload constraints to the attachments section: "
                    "'Accepted formats: PDF, JPG, PNG. Maximum file size: 2MB per file.' "
                    "If constraints differ per attachment, specify them per item."
                ),
            ))

    return issues

# ─────────────────────────────────────────────────────────────
# MAIN RUNNER
# ─────────────────────────────────────────────────────────────
def run_all_rules(en_data: dict, ar_data: dict) -> list[dict]:
    """
    Run all deterministic rule checks on EN and AR page data.
    Returns list of issue dicts (same structure as AI issues).
    These are merged with AI issues before writing the CSV.
    """
    issues = []

    # Arabic checks
    issues += check_hamza(ar_data)
    issues += check_alif_maqsura(ar_data)
    issues += check_arabic_comma(ar_data)
    issues += check_mixed_numerals(ar_data)

    # English checks
    issues += check_raw_emails(en_data, ar_data)
    issues += check_raw_urls(en_data, ar_data)
    issues += check_skiplino(en_data, ar_data)
    issues += check_double_spaces(en_data)
    issues += check_service_name_title_case(en_data)
    issues += check_conditions_in_name_or_description(en_data, ar_data)
    issues += check_unlinked_systems(en_data, ar_data)
    issues += check_mislabeled_download_links(en_data, ar_data)
    issues += check_attachment_upload_constraints(en_data, ar_data)

    # New deterministic rules
    issues += check_vague_attachments(en_data, ar_data)
    issues += check_conditional_attachments(en_data, ar_data)
    issues += check_missing_regulation_links(en_data, ar_data)

    # Bilingual checks
    issues += check_empty_sections(en_data, ar_data)
    issues += check_process_time_mismatch(en_data, ar_data)
    issues += check_fee_mismatch(en_data, ar_data)

    return issues




# ─────────────────────────────────────────────────────────────
# RULE COVERAGE MAP
# Used by validate_and_clean to suppress AI issues that duplicate
# what the rule engine already caught for this service.
# Format: rule_id → (section, category_tag)
# ─────────────────────────────────────────────────────────────
RULE_COVERAGE = {
    "AR001": ("*",                  "hamza_spelling"),
    "AR002": ("*",                  "alif_maqsura_spelling"),
    "AR003": ("*",                  "arabic_comma"),
    "AR004": ("*",                  "mixed_numerals"),
    "EN001": ("*",                  "raw_email"),
    "EN002": ("*",                  "raw_url"),
    "EN003": ("*",                  "skiplino"),
    "EN004": ("*",                  "double_space"),
    "EN006": ("Service Name",       "title_case"),
    "EN007": ("*",                  "unlinked_system"),
    "AR007": ("*",                  "unlinked_system"),
    "EN009": ("Required Attachments","vague_attachment"),
    "AR009": ("Required Attachments","vague_attachment"),
    "EN010": ("Required Attachments","conditional_attachment"),
    "AR010": ("Required Attachments","conditional_attachment"),
    "EN011": ("Legal Regulations",  "missing_link"),
    "AR011": ("Legal Regulations",  "missing_link"),
    "EN014": ("Required Attachments", "mislabeled_download"),
    "AR014": ("Required Attachments", "mislabeled_download"),
    "EN015": ("Required Attachments", "upload_constraints"),
    "AR015": ("Required Attachments", "upload_constraints"),
    "BI001": ("*",                  "empty_section"),
    "BI002": ("Process Time",       "process_time_mismatch"),
    "BI003": ("Fees",               "fee_mismatch"),
}


def get_covered_categories(rule_issues: list[dict]) -> set[tuple]:
    """
    Return set of (section, category) pairs already covered by rule issues.
    Used to suppress duplicate AI issues.
    """
    covered = set()
    for iss in rule_issues:
        rule_id = iss.get("rule_id", "")
        if rule_id in RULE_COVERAGE:
            section, category = RULE_COVERAGE[rule_id]
            covered.add((section.lower(), category))
            if section == "*":
                # Wildcard — covers this category in ALL sections
                covered.add(("*", category))
    return covered


def _ai_duplicates_rule(ai_issue: dict, covered: set[tuple]) -> bool:
    """
    Return True if this AI issue is already covered by a rule check.
    Checks both exact section match and wildcard.
    """
    section = str(ai_issue.get("section", "")).lower().strip()
    desc = str(ai_issue.get("issue_description", "")).lower()

    # Category keyword detection
    category_keywords = {
        "hamza_spelling":           ["hamza", "همزة", "همزه"],
        "alif_maqsura_spelling":    ["alif maqsura", "ألف مقصورة", "ى", "alif"],
        "arabic_comma":             ["arabic comma", "فاصلة عربية"],
        "mixed_numerals":           ["mixed numeral", "أرقام مختلطة"],
        "raw_email":                ["email address", "mailto"],
        "raw_url":                  ["raw url", "plain text url", "hyperlink"],
        "double_space":             ["double space"],
        "space_punctuation":        ["space before punctuation"],
        "title_case":               ["title case", "capitaliz"],
        "unlinked_system":          ["without a hyperlink", "direct link", "system"],
        "description_start":        ["this service allows", "this service enables",
                                     "تتيح هذه الخدمة", "تمكّن هذه الخدمة"],
        "vague_attachment":         ["vague", "too vague", "not specific", "غامض"],
        "conditional_attachment":   ["conditional", "not labeled", "not explicitly"],
        "missing_link":             ["missing link", "missing hyperlink", "plain text",
                                     "not linked", "no link", "hyperlink"],
        "process_time_format":      ["working day", "process time format", "يوم عمل"],
        "last_step_outcome":        ["last step", "after submission", "outcome",
                                     "what happens"],
        "empty_section":            ["empty", "missing content", "not provided"],
        "process_time_mismatch":    ["process time mismatch", "time mismatch"],
        "fee_mismatch":             ["fee mismatch", "fee amount"],
    }

    for (cov_section, category), keywords in zip(covered, [
        category_keywords.get(cat, []) for _, cat in covered
    ]):
        if not keywords:
            continue
        if not any(kw in desc for kw in keywords):
            continue
        # Section matches if wildcard or same section
        if cov_section == "*" or cov_section == section:
            return True

    return False

# ─────────────────────────────────────────────────────────────
# OUTPUT VALIDATION
# ─────────────────────────────────────────────────────────────
ALLOWED_SECTIONS = {
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
}


def _issue_fingerprint(iss: dict) -> str:
    """
    Generate a deduplication key for an issue.

    Language is EXCLUDED from the fingerprint so that the same issue detected
    in both EN and AR content merges into a single row with language="Both".

    Special case for Legal Regulations hyperlinks: key also includes the law
    name from placement so different laws don't collapse into one row.
    """
    section = str(iss.get("section", "")).lower().strip()
    desc_raw = re.sub(r"\s+", " ", str(iss.get("issue_description", "")).lower()).strip()

    # Legal Regulations hyperlinks: key by law name so each law is separate
    if section == "legal regulations" and "hyperlink" in desc_raw:
        placement = re.sub(r"\s+", " ", str(iss.get("issue_placement", "")).lower()).strip()[:80]
        return f"{section}|hyperlink|{placement}"

    # All other issues: key by section + description only (no language)
    # so EN and AR instances of the same problem merge into one "Both" row
    return f"{section}|{desc_raw[:80]}"


def validate_and_clean(
    issues: list[dict],
    entity: str,
    service: str,
    covered_categories: set = None,
) -> list[dict]:
    """
    Post-process AI issues:
      1. Suppress issues already covered by rule engine (if covered_categories provided)
      2. Fix section names to match allowed list
      3. Flag generic proposed solutions
      4. Ensure issue_placement has Section: "quote" format
      5. Fill missing entity/service
      6. Remove completely empty issues
      7. Deduplicate: same issue type on same section → merge into one row
    Returns cleaned list.
    """
    cleaned = []
    seen_fps = {}  # fingerprint → index in cleaned list (for merging placements)
    suppressed = 0

    for iss in issues:
        # Suppress if already covered by rule engine
        if covered_categories and _ai_duplicates_rule(iss, covered_categories):
            suppressed += 1
            continue
        # Fill missing entity/service
        if not iss.get("entity"):
            iss["entity"] = entity
        if not iss.get("service"):
            iss["service"] = service

        # Normalize section name
        raw_section = str(iss.get("section", "")).strip()
        if raw_section not in ALLOWED_SECTIONS:
            match = next(
                (s for s in ALLOWED_SECTIONS if s.lower() == raw_section.lower()), None
            )
            iss["section"] = match or "Linguistic"

        # Build placement — accept any non-empty placement, only fallback if truly missing
        placement = str(iss.get("issue_placement", "")).strip()
        if not placement:
            desc = str(iss.get("issue_description", ""))
            words = desc.split()[:8]
            quote = " ".join(words) if words else "see description"
            iss["issue_placement"] = f'{iss["section"]}: "{quote}…"'

        # Flag generic proposed solutions
        solution = str(iss.get("proposed_solution", "")).strip()
        if not solution or _GENERIC_RE.search(solution):
            iss["proposed_solution"] = (
                "[NEEDS REVIEW] " + solution
                if solution
                else "[NEEDS REVIEW] No proposed solution was provided."
            )

        # Skip completely empty issues
        if not iss.get("issue_description") and not iss.get("issue_placement"):
            continue

        # Ensure required keys exist
        for key in [
            "screenshot",
            "issue_placement",
            "issue_description",
            "proposed_solution",
        ]:
            if key not in iss:
                iss[key] = ""

        # Deduplication — merge repeated issues into a single row
        fp = _issue_fingerprint(iss)
        if fp in seen_fps:
            existing = cleaned[seen_fps[fp]]

            # Merge placement — add new quote if not already present
            existing_placement = existing["issue_placement"]
            new_placement = iss["issue_placement"]
            if new_placement and new_placement not in existing_placement:
                existing["issue_placement"] = existing_placement + " | " + new_placement

            # Upgrade language to "Both" if EN and AR are now merged
            ex_lang = str(existing.get("language", "")).strip()
            new_lang = str(iss.get("language", "")).strip()
            if ex_lang != new_lang and ex_lang != "Both":
                existing["language"] = "Both"

            # Note to apply fix across all occurrences
            sol = existing["proposed_solution"]
            if "all occurrences" not in sol:
                existing["proposed_solution"] = (
                    sol + " Apply this fix across all occurrences in both EN and AR."
                )
        else:
            seen_fps[fp] = len(cleaned)
            cleaned.append(iss)

    if suppressed:
        pass  # suppressed count available for debugging if needed
    return cleaned


def is_generic_solution(text: str) -> bool:
    """Returns True if the proposed solution is generic / non-actionable."""
    return bool(_GENERIC_RE.search(str(text)))