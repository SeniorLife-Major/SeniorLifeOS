"""Emergency red flags in what a senior says or types, found by fixed rules (plan §5.2).

Before any doctor search, the request is checked for signs of an emergency: chest pain, stroke
signs (face droop, arm weakness, slurred speech), severe breathlessness, heavy bleeding and
fainting (the plan's list), plus seizures, self-harm, a severe allergic reaction, and a fever
together with confusion. Self-harm also shows the Tele-MANAS mental-health line. The words are listed in English, Hindi (Devanagari) and Hindi written in English
letters ("seene mein dard"). This runs on this machine, needs no model and no network, so it
works even when the AI model can't be used.

The rules lean towards "yes": "no chest pain" still counts, because a missed emergency costs far
more than an extra question. The health graph lets the model and then the person check a rule
finding; the model alone can never clear it.

Hindi has no \\b: its vowel signs are not word characters to Python's re, so Devanagari words are
matched as plain text (as in tools/document_type.py).
"""

from __future__ import annotations

import re

# Hindi written in English letters is spelled many ways: "mein", "me", "main", "mai".
IN = r"(?:mein|mei|me|main|mai)"
NOT = r"(?:nahi|nahin|nai|nhi|na)"
ING = r"(?:raha|rahi|rahe|rha|rhi|rhe)"
# Up to two words in between: "seene mein *bahut tez* dard" (found live: the plain form missed it).
GAP = r"(?:\s+\S+){0,2}"

RED_FLAGS: dict[str, list[str]] = {
    "chest_pain": [
        r"\bchest\s*(?:pain|ache|tightness|pressure|heaviness)", r"\bpain\s+in\s+(?:my\s+|the\s+|his\s+|her\s+)?chest",
        r"\bheart\s*attack",
        r"\b(?:seene|sine|seena|chhati|chati|chaati)\s+" + IN + GAP + r"\s+(?:dard|dabav|jalan|bhari)",
        r"\bdil\s+ka\s+daura", "(?:सीने|छाती) में" + GAP + r"\s+दर्द", "दिल का दौरा",
    ],
    "stroke_signs": [
        r"\bstroke\b", r"\bface\s+(?:is\s+)?(?:droop|drooping|dropping|twisted|crooked)", r"\bdrooping\s+face",
        r"\b(?:arm|leg|hand|side)\s+(?:is\s+|has\s+|went\s+|feels\s+)?(?:weak|weakness|numb|gone\s+limp|limp)",
        r"\bslurr(?:ed|ing)\s+speech", r"\b(?:can'?t|cannot|unable\s+to|not\s+able\s+to)\s+(?:speak|talk)",
        r"\bparaly[sz]", r"\blakwa\b", r"\bmunh\s+(?:tedha|tirchha)", r"\bbol\s+" + NOT + r"\s+(?:pa|paa)\s+" + ING,
        "लकवा", "मुंह टेढ़ा", "मुँह टेढ़ा", "बोल नहीं पा",
    ],
    "breathlessness": [
        r"\b(?:can'?t|cannot|unable\s+to|not\s+able\s+to)\s+breathe", r"\b(?:difficulty|trouble)\s+breathing",
        r"\bshort(?:ness)?\s+of\s+breath", r"\bbreathless", r"\bgasping", r"\bchoking",
        r"\bsaa?ns\s+" + NOT + r"\s+(?:aa|le\s+pa|li\s+ja)", r"\bsaa?ns\s+(?:phool|ful|fool|ruk)",
        r"\bdam\s+ghut", "सांस नहीं", "साँस नहीं", "सांस फूल", "साँस फूल", "दम घुट",
    ],
    "heavy_bleeding": [
        r"\bheavy\s+bleeding", r"\bbleeding\s+(?:heavily|a\s+lot|badly|won'?t\s+stop|not\s+stopping|does\s+not\s+stop)",
        r"\b(?:vomit(?:ing|ed)?|cough(?:ing|ed)?|throw(?:ing)?)(?:\s+up)?\s+blood",r"\bblood\s+in\s+(?:vomit|cough)",
        r"\bkhoo?n" + GAP + r"\s+(?:beh|bah|nikal|ruk\w*(?:\s+hi)?\s+" + NOT + r")", r"\bbahut\s+khoo?n",
        r"\bkhoo?n\s+ki\s+ulti",
        "खून" + GAP + r"\s+(?:बह|निकल)", "बहुत खून", "खून की उल्टी",
    ],
    "fainting": [
        r"\bfaint(?:ed|ing)?\b", r"\bpassed\s+out", r"\bunconscious", r"\bcollapsed", r"\bnot\s+(?:responding|waking\s+up)",
        r"\bbehosh", r"\bchakkar\s+(?:aa|kha)\s*(?:ke|kar)\s+gir", "बेहोश", "चक्कर आकर गिर",
    ],
    "seizure": [
        r"\bseizure", r"\bconvuls", r"\b(?:having|had|has|getting)\s+(?:a\s+)?fits?\b", r"\bfits?\s+(?:aa|aaye|pad)",
        r"\bmirgi", r"\bjhatke\s+(?:aa|lag)", r"\bdaura\s+(?:pad|pada|padta|aa)", "मिर्गी", "दौरा पड़", "झटके आ",
    ],
    "self_harm": [
        r"\bsuicid", r"\bkill\s+(?:myself|himself|herself)", r"\bend\s+(?:my|his|her)\s+life", r"\bwants?\s+to\s+die",
        r"\b(?:don'?t|do\s+not|doesn'?t|does\s+not)\s+want\s+to\s+live", r"\bhurt\s+(?:myself|himself|herself)",
        r"\bself[\s-]?harm", r"\bkhud\s*kushi", r"\baa?tma\s*hatya", r"\bmar\s+(?:jaana|jana|jaun|jaunga|jaungi)\b",
        r"\bjeena\s+" + NOT + r"\s+(?:chahta|chahti)", r"\bjeene\s+ka\s+mann?\s+" + NOT,
        "आत्महत्या", "खुदकुशी", "मर जाना", "जीना नहीं", "जीने का मन नहीं",
    ],
    "severe_allergy": [
        r"\banaphyla", r"\bsevere\s+allerg", r"\ballergic\s+reaction",
        r"\b(?:face|lips?|tongue|throat|eyes?)\s+(?:is\s+|are\s+|has\s+|have\s+)?(?:swell|swollen|swelling)",
        r"\bswollen\s+(?:face|lips?|tongue|throat)", r"\bswelling\s+(?:of|in|on)\s+(?:the\s+|my\s+|his\s+|her\s+)?"
        r"(?:face|lips?|tongue|throat)", r"\b(?:honth|hont|zubaan|jeebh|gala|chehra|munh)\s+(?:sooj|suj)",
        "होंठ सूज", "गला सूज", "चेहरा सूज", "जीभ सूज",
    ],
}
# Signs that are an emergency only together: every group must match (any pattern in a group).
ALL_OF: dict[str, list[list[str]]] = {
    "fever_confusion": [
        [r"\bfever", r"\bbukh?aa?r", r"\btemperature\b", "बुखार"],
        [r"\bconfus", r"\bdisorient", r"\bnot\s+making\s+sense", r"\b(?:talking|speaking)\s+nonsense",
         r"\b(?:not|doesn'?t|does\s+not|can'?t)\s+recogni[sz]", r"\bvery\s+(?:drowsy|sleepy)", r"\bbehki\s+behki",
         r"\bpehchaa?n\s+" + NOT, r"\bhosh\s+" + IN + r"\s+" + NOT, "बहकी", "पहचान नहीं", "होश में नहीं"],
    ],
}
FLAGS = (*RED_FLAGS, *ALL_OF)
_COMPILED = {flag: [re.compile(words, re.IGNORECASE) for words in entries] for flag, entries in RED_FLAGS.items()}
_COMPILED_ALL = {flag: [[re.compile(words, re.IGNORECASE) for words in group] for group in groups]
                 for flag, groups in ALL_OF.items()}
APOSTROPHES = re.compile("[‘’`´]")

# Shown with every emergency answer. India's numbers: 112 for any emergency, 108 for an ambulance.
EMERGENCY_NUMBERS = [
    {"number": "112", "for": "Any emergency (police, fire, ambulance)"},
    {"number": "108", "for": "Ambulance"},
]
# Added for self-harm: Tele-MANAS, the government's free mental-health helpline, open all day.
MENTAL_HEALTH_NUMBER = {"number": "14416", "for": "Tele-MANAS mental health helpline (free, 24 hours)"}
EMERGENCY_MESSAGE = ("This may be an emergency. Call 112 or 108 now, or go to the nearest hospital emergency. "
                     "Do not wait for a doctor's appointment.")
SELF_HARM_MESSAGE = ("You are not alone. Please talk to someone now: call Tele-MANAS on 14416 (free, any time) "
                     "or 112, or tell someone near you.")


def find_red_flags(text: str) -> list[str]:
    """The red flags (e.g. "chest_pain") in this text, in a fixed order. Empty if none."""
    plain = " ".join(APOSTROPHES.sub("'", text or "").split())
    found = {flag for flag, patterns in _COMPILED.items() if any(pattern.search(plain) for pattern in patterns)}
    found |= {flag for flag, groups in _COMPILED_ALL.items()
              if all(any(pattern.search(plain) for pattern in group) for group in groups)}
    return [flag for flag in FLAGS if flag in found]


def emergency_numbers(flags: list[str]) -> list[dict[str, str]]:
    """The numbers to show for these signs: 112 and 108 always, the mental-health line for self-harm."""
    return [*EMERGENCY_NUMBERS, *([MENTAL_HEALTH_NUMBER] if "self_harm" in flags else [])]


def emergency_message(flags: list[str]) -> str:
    return f"{SELF_HARM_MESSAGE} {EMERGENCY_MESSAGE}" if "self_harm" in flags else EMERGENCY_MESSAGE
