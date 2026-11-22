"""Which kind of doctor a lab report points to (doctor search from a lab report).

Fixed rules, no model and no network (the owner's choice, lab-report-doctor-search Decision 2):

- A result counts only when it is outside the normal range printed on the report itself
  (lab_flag in tools/medical.py: high or low), or, for a result printed as a word, when the word
  says something was found (FOUND_WORDS: positive, reactive, present). The app has no normal
  ranges of its own. A number with no range that can be read is not compared (unjudged).
- TEST_RULES says which specialist looks after each known test. A result outside its range on a
  test the table doesn't know points to a general physician.
- Every result outside its range points to one specialist: that specialist. To more than one: a
  general physician, who can look at them together (the rule doctor search already uses when
  several problems are described). None outside its range: a general physician, saying so.
- VERY_ABNORMAL: values so far out that the person should see a doctor today (Decision 4). Each
  cut-off is checked only in the units it is written for (UNIT_SCALES); a value in units this
  table doesn't know is never guessed at. A very abnormal value counts even when no range is
  printed next to it. Troponin above its printed range, or found, always counts.

The cut-offs are typical "critical value" limits used by hospital labs, chosen for this project
and not reviewed by a doctor; they only decide when to say "Please see a doctor today".

The specialist is only the kind of doctor to see first; nothing here is a diagnosis.
"""

from __future__ import annotations

import re
from typing import Any

from .medical import lab_flag, parse_number
from .specialties import GENERAL_PHYSICIAN, SPECIALTIES

# Words that say a test found something ("Urine sugar: Present"). The other words a result may be
# printed as (negative, nil, absent, non-reactive, normal, trace) are not counted.
FOUND_WORDS = {"positive", "reactive", "present"}

# (rule, specialist, test name pattern), first match wins: the urine tests come before the blood
# tests with the same words (urine albumin is the kidneys', serum albumin the liver's).
TEST_RULES: list[tuple[str, str, str]] = [
    ("urine_protein", "nephrologist", r"micro\s*albumin|albumin\s*/?\s*creatinine|\bacr\b|proteinuria"
                                      r"|\b(?:urine|urinary)\b.*\b(?:protein|albumin)\b"
                                      r"|\b(?:protein|albumin)\b.*\b(?:urine|urinary)\b"),
    ("urine_sugar", "endocrinologist", r"\b(?:urine|urinary)\b.*\b(?:sugar|glucose|ketones?)\b"
                                       r"|\b(?:sugar|glucose|ketones?)\b.*\b(?:urine|urinary)\b"),
    ("urine_infection", GENERAL_PHYSICIAN, r"\bpus\s+cells?\b|\bnitrites?\b|leu[ck]ocyte\s+esterase"
                                           r"|\b(?:urine|urinary)\b.*\b(?:culture|bacteria|leu[ck]ocytes?)\b"),
    ("hba1c", "endocrinologist", r"\bhb\s*a1c\b|\ba1c\b|glyc(?:osyl)?ated"),
    ("glucose", "endocrinologist", r"glucose|\bsugar\b|\bfbs\b|\bppbs\b|\brbs\b|\bfbg\b|\bppbg\b"),
    ("thyroid", "endocrinologist", r"\btsh\b|thyroid|thyroxine|triiodothyronine|\bf?t[34]\b"),
    ("calcium", "endocrinologist", r"\bcalcium\b"),
    ("lipids", "cardiologist", r"cholesterol|triglyceride|lipid|\bv?ldl\b|\bhdl\b|\btg\b"),
    ("heart", "cardiologist", r"troponin|\bbnp\b|pro\s*bnp|\bck\s*mb\b|\bcpk\s*mb\b"),
    ("kidney", "nephrologist", r"creatinine|\burea\b|\bbun\b|\be?gfr\b|cystatin"),
    ("liver", "gastroenterologist", r"\bsgot\b|\bsgpt\b|\bast\b|\balt\b|bilirubin|alkaline\s+phosphatase|\balp\b"
                                    r"|\bggtp?\b|gamma\s*glutamyl|\balbumin\b|\bglobulin\b|total\s+protein"
                                    r"|\ba\s*/\s*g\b|\bhbsag\b|hepatitis|\bhcv\b"),
    ("stool", "gastroenterologist", r"\bstool\b|occult\s+blood|pylori"),
    ("prostate", "urologist", r"\bpsa\b|prostate"),
    ("joints", "rheumatologist", r"\bra\s+factor\b|\brf\b|rheumatoid|\banti\s*ccp\b|\bccp\b|uric\s+acid|\bana\b"
                                 r"|anti\s*nuclear|\bhla\s*b\s*27\b"),
    ("skin", "dermatologist", r"\bkoh\b|fung(?:al|us|i)\b"),
    ("blood_count", GENERAL_PHYSICIAN, r"ha?emoglobin|\bhb\b|\bhgb\b|\brbc\b|\bwbc\b|\btlc\b|\bdlc\b|platelet|\bplt\b"
                                       r"|\bmch?c?\b|\bmcv\b|\brdw\b|ha?ematocrit|\bpcv\b|\bhct\b|neutrophil"
                                       r"|lymphocyte|eosinophil|monocyte|basophil|leu[ck]ocyte"
                                       r"|(?:red|white)\s+(?:blood\s+)?cell|\besr\b|sedimentation"),
    ("inflammation", GENERAL_PHYSICIAN, r"\bcrp\b|c\s*reactive"),
    ("vitamins_iron", GENERAL_PHYSICIAN, r"vitamin|\bvit\b|\bb12\b|\bd3\b|\b25\s*oh\b|cobalamin|fol(?:ate|ic)"
                                         r"|ferritin|\biron\b|\btibc\b|transferrin"),
    ("salts", GENERAL_PHYSICIAN, r"sodium|potassium|chloride|magnesium|phosph|bicarbonate|electrolyte|\bna\b|\bk\b"),
    ("clotting", GENERAL_PHYSICIAN, r"\binr\b|prothrombin|\bpt\b|\baptt\b|d\s*dimer"),
]
_RULES = [(rule, specialty, re.compile(pattern)) for rule, specialty, pattern in TEST_RULES]

# Units, as plain_unit() writes them, and what one of them is in the cut-off's units.
COUNT = {"/cumm": 1, "cumm": 1, "/ul": 1, "/mm3": 1, "/mm^3": 1,
         "10^3/ul": 1000, "x10^3/ul": 1000, "10^3/cumm": 1000, "x10^3/cumm": 1000, "thou/ul": 1000, "k/ul": 1000,
         "10^9/l": 1000, "x10^9/l": 1000, "lakh/cumm": 100_000, "lakhs/cumm": 100_000}
SALT = {"mmol/l": 1, "meq/l": 1}

# Values so far out that the person should see a doctor today: (rule, test name pattern, units,
# below, above). None: no limit on that side.
VERY_ABNORMAL: list[tuple[str, str, dict[str, float], float | None, float | None]] = [
    ("glucose", r".", {"mg/dl": 1, "mmol/l": 18.0}, 54, 400),  # blood sugar, mg/dL
    ("salts", r"potassium|\bk\b", SALT, 3.0, 6.0),  # mmol/L
    ("salts", r"sodium|\bna\b", SALT, 125, 155),  # mmol/L
    ("blood_count", r"ha?emoglobin|\bhb\b|\bhgb\b", {"g/dl": 1, "gm/dl": 1, "gm%": 1, "g%": 1, "g/l": 0.1}, 7.0, 20.0),
    ("blood_count", r"platelet|\bplt\b", COUNT, 50_000, 1_000_000),  # per microlitre
    ("blood_count", r"\bwbc\b|\btlc\b|leu[ck]ocyte|white\s+(?:blood\s+)?cell", COUNT, 2_000, 30_000),
    ("kidney", r"creatinine", {"mg/dl": 1, "umol/l": 1 / 88.4}, None, 5.0),  # mg/dL
    ("calcium", r".", {"mg/dl": 1, "mmol/l": 4.008}, 7.0, 12.0),  # mg/dL
    ("liver", r"\bsgot\b|\bsgpt\b|\bast\b|\balt\b", {"u/l": 1, "iu/l": 1}, None, 1000),
    ("clotting", r"\binr\b", {"": 1, "ratio": 1}, None, 5.0),
]
_VERY_ABNORMAL = [(rule, re.compile(pattern), units, below, above)
                  for rule, pattern, units, below, above in VERY_ABNORMAL]

# What the person is told (fixed sentences; the result screen shows them as they are).
ONE_SPECIALIST = "Your lab report has results outside the normal range printed on it that {article} {label} looks after: {tests}."
SEVERAL = ("Your lab report has results outside the normal range for more than one kind of doctor ({labels}). "
           "A general physician can look at them together first.")
GENERAL_ONLY = ("Your lab report has results outside the normal range printed on it: {tests}. "
                "A general physician can look at them first.")
ALL_NORMAL = ("None of the results on your lab report is outside the normal range printed on it. "
              "A general physician can go through the report with you.")
NONE_COMPARED = ("We could not compare the results on your lab report with a normal range. "
                 "A general physician can go through the report with you.")
SEE_TODAY = "Some results are far outside the normal range. Please see a doctor today."


def plain_name(name: Any) -> str:
    """'S.G.P.T (ALT)' -> 'sgpt alt', 'HbA1c' -> 'hba1c': for matching only."""
    text = str(name or "").casefold().replace(".", "")
    return " ".join(re.sub(r"[-(),:;_+*\[\]]", " ", text).split())


def plain_unit(unit: Any) -> str:
    """'x 10³/µL' -> 'x10^3/ul', 'cells/cmm' -> '/cumm', 'mEq/L' -> 'meq/l'."""
    text = re.sub(r"\s+", "", str(unit or "").casefold())
    text = text.replace("µ", "u").replace("μ", "u").replace("³", "^3").replace("cells", "")
    return text.replace("cmm", "cumm").replace("cuumm", "cumm")


def rule_for(name: Any) -> tuple[str | None, str]:
    """(rule, specialist) for a test name; (None, general physician) when the table doesn't know it."""
    text = plain_name(name)
    for rule, specialty, pattern in _RULES:
        if pattern.search(text):
            return rule, specialty
    return None, GENERAL_PHYSICIAN


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_number(str(value or "").replace(",", "").strip())


def very_abnormal(rule: str | None, name: Any, value: float | None, unit: Any) -> str | None:
    """'high' or 'low' when the value is past a VERY_ABNORMAL cut-off in units it knows; else None."""
    if rule is None or value is None:
        return None
    text, units = plain_name(name), plain_unit(unit)
    for cut_rule, pattern, known, below, above in _VERY_ABNORMAL:
        if cut_rule != rule or not pattern.search(text) or units not in known:
            continue
        scaled = value * known[units]
        if below is not None and scaled < below:
            return "low"
        if above is not None and scaled > above:
            return "high"
    return None


def finding_of(row: Any) -> dict[str, Any] | None:
    """One result outside its range: {test, flag (high, low, or the word found), specialty, known,
    see_today}. None for a result inside its range, or one that can't be compared (see unjudged)."""
    if not isinstance(row, dict):
        return None
    name = " ".join(str(row.get("name") or "").split())
    if not name:
        return None
    rule, specialty = rule_for(name)
    value = _number(row.get("value"))
    word = str(row.get("value") or "").strip().casefold() if value is None else ""
    flag = lab_flag(value, row.get("ref_range")) if value is not None else None
    today = very_abnormal(rule, name, value, row.get("unit"))
    if today:
        flag = flag if flag in ("high", "low") else today
    elif rule == "heart" and re.search(r"troponin", plain_name(name)) and (flag == "high" or word in FOUND_WORDS):
        today = flag or word
    if word in FOUND_WORDS:
        flag = word
    if flag not in ("high", "low") and word not in FOUND_WORDS:
        return None
    return {"test": name, "flag": flag, "specialty": specialty, "known": rule is not None, "see_today": bool(today)}


def compared(row: Any) -> bool:
    """Could this result be compared at all: a number with a range we can read, or a word."""
    if not isinstance(row, dict) or not str(row.get("name") or "").strip():
        return False
    value = _number(row.get("value"))
    if value is None:
        return bool(str(row.get("value") or "").strip())
    return lab_flag(value, row.get("ref_range")) is not None


def _article(label: str) -> str:
    return "an" if label[:1].casefold() in "aeiou" else "a"


def _tests(findings: list[dict[str, Any]]) -> str:
    return ", ".join(f"{item['test']} ({item['flag']})" for item in findings)


def _labels(keys: list[str]) -> str:
    labels = [SPECIALTIES[key] for key in keys]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def read_lab_results(rows: Any) -> dict[str, Any]:
    """The kind of doctor for a lab report's test rows (the "tests" field of a confirmed report):
    {findings, compared, unjudged, specialties, specialty, see_today, reason}."""
    findings: list[dict[str, Any]] = []
    judged = unjudged = 0
    for row in rows if isinstance(rows, list) else []:
        finding = finding_of(row)
        if finding is not None:
            findings.append(finding)
        if compared(row):
            judged += 1
        elif finding is None and isinstance(row, dict) and str(row.get("name") or "").strip():
            unjudged += 1  # a number with no range we can read (a very abnormal one is still a finding)
    found = {item["specialty"] for item in findings}
    specialties = [key for key in SPECIALTIES if key in found]
    if not findings:
        specialty = GENERAL_PHYSICIAN
        reason = ALL_NORMAL if judged else NONE_COMPARED
    elif len(specialties) > 1:
        specialty = GENERAL_PHYSICIAN
        reason = SEVERAL.format(labels=_labels(specialties))
    elif specialties == [GENERAL_PHYSICIAN]:
        specialty = GENERAL_PHYSICIAN
        reason = GENERAL_ONLY.format(tests=_tests(findings))
    else:
        specialty = specialties[0]
        label = SPECIALTIES[specialty]
        reason = ONE_SPECIALIST.format(article=_article(label), label=label, tests=_tests(findings))
    return {"findings": findings, "compared": judged, "unjudged": unjudged, "specialties": specialties,
            "specialty": specialty, "see_today": any(item["see_today"] for item in findings), "reason": reason}
