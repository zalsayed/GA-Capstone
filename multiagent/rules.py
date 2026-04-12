import re


HAMZA_ERRORS = {
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
    "الإفلاس": "الإفلاس",
    "لإستيراد": "لاستيراد",
    "لإستخدام": "لاستخدام",
    "لإستكمال": "لاستكمال",
    "لإستلام": "لاستلام",
    "لإستمارة": "لاستمارة",
    "بإستخدام": "باستخدام",
    "بإستيراد": "باستيراد",
    "بإستلام": "باستلام",
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

ALIF_MAQSURA_ERRORS = {
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
}

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

_SPACE_PUNCT_RE = re.compile(r"\w +[.,;!?]")


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

        section = "Linguistic"
        for field, val in ar_data.items():
            if isinstance(val, str) and word in val:
                section = _field_to_section(field)
                break

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
            break

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


def check_raw_emails(en_data: dict, ar_data: dict) -> list[dict]:
    """EN001 — Raw email addresses not wrapped in mailto: hyperlinks."""
    issues = []
    for lang, data in [("EN", en_data), ("AR", ar_data)]:
        for field, val in data.items():
            if not isinstance(val, str) or not val:
                continue
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
    """EN002 — Raw URLs appearing as plain visible text in content fields."""
    issues = []

    CONTENT_FIELDS = {
        "service_name",
        "service_description",
        "required_attachments",
        "legal_regulations",
        "fees",
        "process_time",
        "service_provider",
        "service_processes",
        "service_conditions",
    }

    for lang, data in [("EN", en_data), ("AR", ar_data)]:
        for field, val in data.items():
            if field not in CONTENT_FIELDS:
                continue
            if not isinstance(val, str) or not val:
                continue
            text = re.sub(r"\[LINK:[^\]]+\]", "", val)
            for m in _URL_RE.finditer(text):
                url = m.group()
                if "GSX-UI-PServiceDetails" in url or "GSX-UI-EServiceDetails" in url:
                    continue
                if "contenthandler" in url or "wps/portal" in url:
                    continue
                section = _field_to_section(field)
                issues.append(
                    _make_issue(
                        rule_id="EN002",
                        section=section,
                        language=lang,
                        placement=f'{section}: "{_snippet(url, 50)}"',
                        description=f"URL '{_snippet(url, 50)}' appears as plain text. It should be a descriptive hyperlink.",
                        solution=f'Make "[descriptive label]" a clickable hyperlink to {url}',
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
        core = word.lstrip("\"'(").rstrip("\"')")
        if not core:
            continue
        if i == 0:
            if core[0].islower():
                return False
        else:
            if core.lower() in _TITLE_CASE_MINOR:
                if core[0].isupper() and len(core) > 1:
                    pass
            else:
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


_CONDITION_PATTERNS_EN = re.compile(
    r"\b(must|only if|provided that|in case of|eligible|requirement|"
    r"condition|restricted to|exclusively for|limited to|applicable to|"
    r"subject to|upon|unless|except)\b",
    re.IGNORECASE,
)

_CONDITION_PATTERNS_AR = re.compile(
    r"(يشترط|بشرط|في حال|الشروط|شرط|مقتصر|حصراً|مخصص|يقتصر|"
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

    both_empty = []  # empty in EN and AR
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

    en_nums = re.findall(r"\d+", en_t)
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


_LEGAL_REF_RE = re.compile(
    r"(?:ملحقة|بموجب|وفقاً|استناداً|القرار|المرسوم|رقم|لسنة|بشأن)" r"[\s\d\(\)]+",
    re.UNICODE,
)

_FEE_LINE_RE = re.compile(r"[|\n•*،,]+")

_FEE_AMOUNT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)")


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


_KNOWN_SYSTEMS_EN = {
    "DUR system": "https://dur.nhra.bh",
    "Drug Utilisation Review": "https://dur.nhra.bh",
    "Sijilat": "https://www.sijilat.bh",
    "Labour Market Regulatory Authority": "https://www.lmra.bh",
    "LMRA": "https://www.lmra.bh",
    "National Health Portal": "https://www.health.bh",
    "Mawaeed": "https://www.mawaeed.bh",
    "Tadbeer": "https://www.tadbeer.gov.bh",
    "Afaq": "https://sew.ofoq2.gov.bh/TFBSEW2/cusLogin/login.cl?lang=en",
}


_KNOWN_SYSTEMS_AR = {
    "نظام أفق": (
        "https://sew.ofoq2.gov.bh/TFBSEW2/cusLogin/login.cl?lang=en",
        "Afaq system",
    ),
    "أفق": (
        "https://sew.ofoq2.gov.bh/TFBSEW2/cusLogin/login.cl?lang=en",
        "Afaq system",
    ),
    "نظام DUR": ("https://dur.nhra.bh", "DUR system"),
    "نظام مراجعة استخدام الدواء": ("https://dur.nhra.bh", "DUR system"),
    "سجلات": ("https://www.sijilat.bh", "Sijilat system"),
    "مواعيد": ("https://www.mawaeed.bh", "Mawaeed system"),
    "تدبير": ("https://www.tadbeer.gov.bh", "Tadbeer system"),
}

_KNOWN_SYSTEMS = _KNOWN_SYSTEMS_EN


def check_unlinked_systems(en_data: dict, ar_data: dict) -> list[dict]:
    """EN007/AR007 — Known external systems referenced without a hyperlink.

    Deduplicates by (field, url, language) so that aliases for the same system
    (e.g. 'DUR system' and 'Drug Utilisation Review') produce only ONE issue
    per field per language, not one per alias.
    """
    issues = []
    fields = ["service_processes", "service_description", "service_conditions"]

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
            context = val[max(0, idx - 5) : idx + len(system_name) + 60]
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
                        f"The '{system_name}' is referenced without a hyperlink. "
                        "Users cannot access the system without a direct link."
                    ),
                    solution=f"Add a hyperlink to '{system_name}': {system_url}",
                )
            )

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
            context = val[max(0, idx - 5) : idx + len(system_name) + 60]
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
                        "Users cannot access the system without a direct link."
                    ),
                    solution=f"Add a hyperlink to '{english_name}' in the Arabic content: {system_url}",
                )
            )

    return issues


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
    issues += check_space_before_punctuation(en_data)
    issues += check_service_name_title_case(en_data)
    issues += check_conditions_in_name_or_description(en_data, ar_data)
    issues += check_unlinked_systems(en_data, ar_data)

    # Bilingual checks
    issues += check_empty_sections(en_data, ar_data)
    issues += check_process_time_mismatch(en_data, ar_data)
    issues += check_fee_mismatch(en_data, ar_data)

    return issues


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
    desc_raw = re.sub(
        r"\s+", " ", str(iss.get("issue_description", "")).lower()
    ).strip()

    if section == "legal regulations" and "hyperlink" in desc_raw:
        placement = re.sub(
            r"\s+", " ", str(iss.get("issue_placement", "")).lower()
        ).strip()[:80]
        return f"{section}|hyperlink|{placement}"

    return f"{section}|{desc_raw[:80]}"


def validate_and_clean(issues: list[dict], entity: str, service: str) -> list[dict]:
    """
    Post-process AI issues:
      1. Fix section names to match allowed list
      2. Flag generic proposed solutions
      3. Ensure issue_placement has Section: "quote" format
      4. Fill missing entity/service
      5. Remove completely empty issues
      6. Deduplicate: same issue type on same section → merge placements into one row
    Returns cleaned list.
    """
    cleaned = []
    seen_fps = {}

    for iss in issues:
        if not iss.get("entity"):
            iss["entity"] = entity
        if not iss.get("service"):
            iss["service"] = service

        raw_section = str(iss.get("section", "")).strip()
        if raw_section not in ALLOWED_SECTIONS:
            match = next(
                (s for s in ALLOWED_SECTIONS if s.lower() == raw_section.lower()), None
            )
            iss["section"] = match or "Linguistic"

        placement = str(iss.get("issue_placement", "")).strip()
        if not placement:
            desc = str(iss.get("issue_description", ""))
            words = desc.split()[:8]
            quote = " ".join(words) if words else "see description"
            iss["issue_placement"] = f'{iss["section"]}: "{quote}…"'

        solution = str(iss.get("proposed_solution", "")).strip()
        if not solution or _GENERIC_RE.search(solution):
            iss["proposed_solution"] = (
                "[NEEDS REVIEW] " + solution
                if solution
                else "[NEEDS REVIEW] No proposed solution was provided."
            )

        if not iss.get("issue_description") and not iss.get("issue_placement"):
            continue

        for key in [
            "screenshot",
            "issue_placement",
            "issue_description",
            "proposed_solution",
        ]:
            if key not in iss:
                iss[key] = ""

        fp = _issue_fingerprint(iss)
        if fp in seen_fps:
            existing = cleaned[seen_fps[fp]]

            existing_placement = existing["issue_placement"]
            new_placement = iss["issue_placement"]
            if new_placement and new_placement not in existing_placement:
                existing["issue_placement"] = existing_placement + " | " + new_placement

            ex_lang = str(existing.get("language", "")).strip()
            new_lang = str(iss.get("language", "")).strip()
            if ex_lang != new_lang and ex_lang != "Both":
                existing["language"] = "Both"

            sol = existing["proposed_solution"]
            if "all occurrences" not in sol:
                existing["proposed_solution"] = (
                    sol + " Apply this fix across all occurrences in both EN and AR."
                )
        else:
            seen_fps[fp] = len(cleaned)
            cleaned.append(iss)

    return cleaned


def is_generic_solution(text: str) -> bool:
    """Returns True if the proposed solution is generic / non-actionable."""
    return bool(_GENERIC_RE.search(str(text)))
