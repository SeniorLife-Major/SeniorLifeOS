"""The benefit schemes the benefits graph checks (benefits Part 2). All of them are MADE UP for the
project demonstration: they are shaped like Indian welfare schemes (a health cover at 70, old-age,
widow and disability pensions), but their names, ages, amounts and rules are not any real
programme's and are not advice.

A scheme is data, not code:
- criteria: one fact compared with a value (>=, <=, ==, or "in" a list). A fact comes from a
  confirmed document field (FACT_DOCUMENT says which kind of paper gives it), or from linking two
  papers (graphs/benefits_graph.link_documents: a death certificate naming the owner as the spouse
  makes "widowed" true; a ration card naming the owner gives the household's card category).
- required_documents: the papers to take when applying (tools/document_rules.match_requirements).
- declarations: what no paper shows, so the person says it themselves ("I have not married
  again"). A result that rests on one is "likely", never more.
- exclusive_group: schemes of which a person can get only one (one monthly pension).
- listed: shown on "Schemes for me". The first demo scheme (demo_senior_support) stays for the
  older demos and tests; its papers can no longer be added in the app.

Every scheme carries its source; the graph refuses a source that is not approved for the demo
or is more than a year old.
"""

from __future__ import annotations

import re
from typing import Any

from .demo_tools import parse_person_name
from .ocr_postprocess import lookalike_key

NATIONAL = "IN"  # a scheme for every state
POOR_HOUSEHOLD = ["AAY", "BPL", "PHH"]  # ration card categories counted as poor in this demo
CATALOGUE_DATE = "2026-11-01"


def _source(scheme_id: str) -> dict[str, Any]:
    return {
        "source_id": f"demo-catalogue-v2:{scheme_id}",
        "publisher": "SeniorLife OS demo catalogue (made up for the project)",
        "url": f"demo://schemes/{scheme_id}/v2",
        "retrieved_at": CATALOGUE_DATE,
        "version": "v2",
        "approved_for_demo": True,
    }


def _poor_household() -> dict[str, Any]:
    return {"id": "poor_household", "fact": "ration_card_category", "operator": "in", "value": POOR_HOUSEHOLD,
            "label": "A poor household on the ration card (AAY, BPL or PHH)"}


NO_OTHER_PENSION = {"key": "no_other_pension", "label": "I do not get another government pension"}
APPLY_AT = "your ward office, gram panchayat or the district social welfare office"

SCHEMES: dict[str, dict[str, Any]] = {
    "demo_senior_support": {
        "scheme_id": "demo_senior_support",
        "name": "Demo Senior Support Pension (fictional)",
        "summary": "The first demo scheme: a pension for Maharashtra residents aged 60 or more.",
        "benefit": "A monthly pension (demo)",
        "jurisdiction": "MH",
        "listed": False,
        "source": {
            "source_id": "demo-source-v1",
            "publisher": "SeniorLife OS fictional demo catalogue",
            "url": "demo://schemes/demo_senior_support/v1",
            "retrieved_at": "2026-09-22",
            "version": "v1",
            "approved_for_demo": True,
        },
        "criteria": [
            {"id": "age", "fact": "age", "operator": ">=", "value": 60, "label": "Aged 60 or more"},
            {"id": "annual_income", "fact": "annual_income", "operator": "<=", "value": 200000,
             "label": "Yearly income up to ₹2,00,000"},
            {"id": "residence_state", "fact": "residence_state", "operator": "==", "value": "MH",
             "label": "Lives in Maharashtra"},
        ],
        "required_documents": ["identity", "income_certificate", "residence_proof"],
        "declarations": [],
    },
    "senior_health_cover": {
        "scheme_id": "senior_health_cover",
        "name": "Senior Health Cover 70+ (demo)",
        "summary": "Free hospital care for everyone aged 70 or more, whatever their income.",
        "benefit": "Hospital care up to ₹5,00,000 a year, free at listed hospitals",
        "jurisdiction": NATIONAL,
        "listed": True,
        "source": _source("senior_health_cover"),
        "criteria": [{"id": "age", "fact": "age", "operator": ">=", "value": 70, "label": "Aged 70 or more"}],
        "required_documents": ["identity"],
        "declarations": [],
        "apply_at": "a listed hospital or a Common Service Centre",
    },
    "old_age_pension": {
        "scheme_id": "old_age_pension",
        "name": "Old Age Pension (demo)",
        "summary": "A monthly pension for people aged 60 or more from a poor household.",
        "benefit": "₹1,000 a month into your bank account",
        "jurisdiction": NATIONAL,
        "listed": True,
        "exclusive_group": "monthly_pension",
        "source": _source("old_age_pension"),
        "criteria": [
            {"id": "age", "fact": "age", "operator": ">=", "value": 60, "label": "Aged 60 or more"},
            _poor_household(),
        ],
        "required_documents": ["identity", "ration_card", "bank_passbook"],
        "declarations": [NO_OTHER_PENSION],
        "apply_at": APPLY_AT,
    },
    "widow_pension": {
        "scheme_id": "widow_pension",
        "name": "Widow Pension (demo)",
        "summary": "A monthly pension for a woman aged 40 to 79 whose husband has died, from a poor household.",
        "benefit": "₹1,200 a month into your bank account",
        "jurisdiction": NATIONAL,
        "listed": True,
        "exclusive_group": "monthly_pension",
        "source": _source("widow_pension"),
        "criteria": [
            {"id": "woman", "fact": "gender", "operator": "==", "value": "female", "label": "A woman"},
            {"id": "age_from", "fact": "age", "operator": ">=", "value": 40, "label": "Aged 40 or more"},
            {"id": "age_to", "fact": "age", "operator": "<=", "value": 79, "label": "Aged 79 or less"},
            {"id": "widowed", "fact": "widowed", "operator": "==", "value": True,
             "label": "Her husband has died (his death certificate names her)"},
            _poor_household(),
        ],
        "required_documents": ["identity", "death_certificate", "ration_card", "bank_passbook"],
        "declarations": [{"key": "not_remarried", "label": "I have not married again"}, NO_OTHER_PENSION],
        "apply_at": APPLY_AT,
    },
    "disability_pension": {
        "scheme_id": "disability_pension",
        "name": "Disability Pension (demo)",
        "summary": "A monthly pension for people aged 18 to 79 with a severe disability (80% or more), "
                   "from a poor household.",
        "benefit": "₹1,500 a month into your bank account",
        "jurisdiction": NATIONAL,
        "listed": True,
        "exclusive_group": "monthly_pension",
        "source": _source("disability_pension"),
        "criteria": [
            {"id": "age_from", "fact": "age", "operator": ">=", "value": 18, "label": "Aged 18 or more"},
            {"id": "age_to", "fact": "age", "operator": "<=", "value": 79, "label": "Aged 79 or less"},
            {"id": "severe_disability", "fact": "disability_percent", "operator": ">=", "value": 80,
             "label": "Disability of 80% or more"},
            _poor_household(),
        ],
        "required_documents": ["identity", "disability_certificate", "ration_card", "bank_passbook"],
        "declarations": [NO_OTHER_PENSION],
        "apply_at": APPLY_AT,
    },
}
DEMO_SCHEMES = SCHEMES  # the older name (tools/demo_tools.py)
EXCLUSIVE_GROUPS = {"monthly_pension": "You can get only one monthly pension. Choose the one that suits you best."}
OPERATORS = {">=", "<=", "==", "in"}

# Facts read straight from a confirmed field of the same name, on any document.
DIRECT_FACTS = ("date_of_birth", "gender", "annual_income", "residence_state", "disability_percent")
# The kind of paper that gives each fact, to say what to add when a fact is unknown.
FACT_DOCUMENT = {
    "age": "identity", "date_of_birth": "identity", "gender": "identity",
    "annual_income": "income_certificate", "residence_state": "ration_card",
    "disability_percent": "disability_certificate", "ration_card_category": "ration_card",
    "widowed": "death_certificate",
}
# The paper's name inside a sentence ("Add your ...").
DOCUMENT_WORDS = {
    "identity": "ID card", "income_certificate": "income certificate", "residence_proof": "residence proof",
    "health_insurance": "health insurance card", "bank_passbook": "bank passbook",
    "disability_certificate": "disability certificate", "death_certificate": "husband's death certificate",
    "ration_card": "ration card",
}


def get_scheme(scheme_id: str) -> dict[str, Any] | None:
    """A narrow, allowlisted catalogue lookup."""
    return SCHEMES.get(scheme_id)


def listed_schemes(jurisdiction: str = NATIONAL) -> list[dict[str, Any]]:
    """The schemes shown on "Schemes for me": national ones, and the state's own."""
    return [scheme for scheme in SCHEMES.values()
            if scheme.get("listed") and scheme["jurisdiction"] in (NATIONAL, jurisdiction)]


def covers(scheme: dict[str, Any], jurisdiction: str) -> bool:
    return scheme["jurisdiction"] in (NATIONAL, jurisdiction)


# ---- comparing names on two papers ---------------------------------------------------------

def plain_name(name: Any) -> str:
    """'Smt. KAMLA  Devi.' -> 'kamla devi': no titles, dots, case or extra spaces."""
    cleaned = parse_person_name(str(name or "")) or ""
    return " ".join(cleaned.replace(".", " ").split()).casefold()


def edit_distance(first: str, second: str) -> int:
    previous = list(range(len(second) + 1))
    for i, a in enumerate(first, start=1):
        current = [i]
        for j, b in enumerate(second, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def compare_names(first: Any, second: Any) -> str:
    """same, close (a spelling or OCR difference, or one name inside the other) or different.
    Names are never corrected: "close" means the person checks it."""
    a, b = plain_name(first), plain_name(second)
    if not a or not b:
        return "different"
    if a == b:
        return "same"
    if lookalike_key(a) == lookalike_key(b):
        return "close"
    if edit_distance(a, b) <= max(1, min(len(a), len(b)) // 8):
        return "close"
    words_a, words_b = set(a.split()), set(b.split())
    if words_a <= words_b or words_b <= words_a:
        return "close"
    return "different"


def names_listed(text: Any) -> list[str]:
    """'Raju Prasad, Kamla Devi; Sita' -> each name."""
    return [name for name in (part.strip() for part in re.split(r"[,;\n]", str(text or ""))) if name]
