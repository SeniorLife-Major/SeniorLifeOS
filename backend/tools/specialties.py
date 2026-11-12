"""The doctor specialties the health graph can point to, and a local fallback that guesses one.

SPECIALTIES is the fixed list: the model may only answer with one of these keys, so it cannot
invent a specialty. When the need is vague or several problems are mentioned, the answer is a
general physician (plan §18, recommendation 12: safer than guessing a specialist).

specialty_from_text is used only when the model can't be (no consent, no key, an error): a few
common words in English, Hindi, and Hindi written in English letters. It gives a specialty only
when exactly one matches; it never diagnoses, it only says which kind of doctor treats that part
of the body or that named condition.
"""

from __future__ import annotations

import re

GENERAL_PHYSICIAN = "general_physician"

SPECIALTIES: dict[str, str] = {
    GENERAL_PHYSICIAN: "General Physician (Internal Medicine)",
    "geriatrician": "Geriatrician (doctor for older adults)",
    "cardiologist": "Cardiologist (heart)",
    "neurologist": "Neurologist (brain and nerves)",
    "orthopedic": "Orthopaedic doctor (bones and joints)",
    "rheumatologist": "Rheumatologist (arthritis and joint inflammation)",
    "pulmonologist": "Pulmonologist (lungs and breathing)",
    "gastroenterologist": "Gastroenterologist (stomach, liver and digestion)",
    "nephrologist": "Nephrologist (kidneys)",
    "urologist": "Urologist (urine, bladder and prostate)",
    "endocrinologist": "Endocrinologist (diabetes and thyroid)",
    "dermatologist": "Dermatologist (skin)",
    "ophthalmologist": "Ophthalmologist (eyes)",
    "ent": "ENT doctor (ear, nose and throat)",
    "psychiatrist": "Psychiatrist (mental health)",
    "gynecologist": "Gynaecologist (women's health)",
    "oncologist": "Oncologist (cancer)",
    "general_surgeon": "General Surgeon",
    "dentist": "Dentist (teeth)",
}

# specialty -> words. English words are matched as whole words; Devanagari as plain text.
SPECIALTY_WORDS: dict[str, list[str]] = {
    GENERAL_PHYSICIAN: [r"\bgeneral\s+physician", r"\bphysician\b", r"\bfamily\s+doctor", r"\bfever\b", r"\bbukhar\b",
                        "बुखार"],
    "geriatrician": [r"\bgeriatric", r"\bold\s+age\s+(?:doctor|specialist)"],
    "cardiologist": [r"\bheart\b", r"\bcardio", r"\bdil\b", r"\bpalpitation", "दिल", "हृदय"],
    "neurologist": [r"\bneuro", r"\bmigraine", r"\btremor", r"\bparkinson", r"\bseizure", r"\bnumbness", "नस"],
    "orthopedic": [r"\bortho", r"\bknee", r"\bjoint", r"\bbone", r"\bback\s*pain", r"\bhip\b", r"\bfracture",
                   r"\bghut(?:na|ne|no)", r"\bhaddi", r"\bkamar\b", "घुटन", "हड्डी", "कमर"],
    "rheumatologist": [r"\brheumat", r"\barthritis", r"\bgathiya", "गठिया"],
    "pulmonologist": [r"\bpulmo", r"\blungs?\b", r"\basthma", r"\bcough", r"\bkhansi", r"\bkhaansi", "खांसी", "दमा"],
    "gastroenterologist": [r"\bgastro", r"\bstomach", r"\bacidity", r"\bliver\b", r"\bdigestion", r"\bconstipation",
                           r"\bpet\s+(?:me|mein|main)\s+dard", r"\bpet\s+dard", "पेट"],
    "nephrologist": [r"\bnephro", r"\bkidney", r"\bdialysis", r"\bgurd[ae]", "गुर्द", "किडनी"],
    "urologist": [r"\burolog", r"\burine", r"\burination", r"\bprostate", r"\bpeshab", "पेशाब"],
    "endocrinologist": [r"\bendocrin", r"\bdiabet", r"\bsugar\b", r"\bthyroid", r"\bmadhumeh", "शुगर", "मधुमेह",
                        "थायराइड"],
    "dermatologist": [r"\bdermat", r"\bskin\b", r"\brash", r"\bitch", r"\bkhujli", r"\btwacha", "खुजली", "त्वचा"],
    "ophthalmologist": [r"\bophthalm", r"\beyes?\b", r"\bvision\b", r"\bcataract", r"\baa?nkh", r"\bmotiyabind",
                        "आंख", "आँख", "मोतियाबिंद"],
    "ent": [r"\bent\b", r"\bears?\b", r"\bhearing", r"\bthroat", r"\bnose\b", r"\bsinus", r"\bkaan\b", r"\bgala\b",
            "कान", "गला", "नाक"],
    "psychiatrist": [r"\bpsychiatr", r"\bdepress", r"\banxiety", r"\bmental\s+health"],
    "gynecologist": [r"\bgyn(?:a)?ec", r"\bmenopause"],
    "oncologist": [r"\boncolog", r"\bcancer", r"\btumou?r"],
    "general_surgeon": [r"\bsurgeon\b", r"\bhernia", r"\bsurgery"],
    "dentist": [r"\bdentist", r"\bteeth\b", r"\btooth", r"\bdaa?nt\b", "दांत", "दाँत"],
}
_COMPILED = {key: [re.compile(words, re.IGNORECASE) for words in entries] for key, entries in SPECIALTY_WORDS.items()}

# Words that say the person wants to see a doctor, though not which kind.
WANTS_DOCTOR = re.compile(r"\bdoctor|\bdr\b|\bspecialist|\bclinic|\bhospital|\bdikha|\bdikhana|\bilaj|\bilaaj|"
                          r"\bcheck\s*-?up|\bconsult|डॉक्टर|डाक्टर|दिखाना|इलाज", re.IGNORECASE)


def matching_specialties(text: str) -> list[str]:
    """Every specialty whose words appear in the text, in SPECIALTIES order."""
    return [key for key in SPECIALTIES if any(pattern.search(text or "") for pattern in _COMPILED.get(key, []))]


def specialty_from_text(text: str) -> str | None:
    """One specialty if exactly one matches; a general physician if several match, or if none
    match but the person asks for a doctor; None if the text is not about seeing a doctor."""
    found = matching_specialties(text)
    if len(found) == 1:
        return found[0]
    if found or WANTS_DOCTOR.search(text or ""):
        return GENERAL_PHYSICIAN
    return None
