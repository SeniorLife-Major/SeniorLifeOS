# 6. Test Cases

> SeniorLife OS — how the system was verified, with "Input vs. Expected Output" tables and results.
> Automated test run: 20 Nov 2026 — **backend 917 / 917 tests passed** (Python `unittest`, ~96 s),
> **frontend 232 / 232 tests passed** in 25 files (Vitest).

---

## 6.1 Testing approach

SeniorLife OS was verified at four levels:

| Level | What it checks | Tool | Size |
|---|---|---|---|
| Unit tests | Single functions: emergency rules, document-type rules, date and ID checks, masking, blur check | Python `unittest` | 917 backend tests |
| Integration / API tests | Whole LangGraph workflows and FastAPI endpoints (upload → read → review → save, sign-in, checklist match) on a throwaway database | `unittest` + FastAPI TestClient + Alembic migrations | included in the 917 |
| Frontend tests | Screens and labels: status chips, emergency screen, checklist, review screen | Vitest + React Testing Library (jsdom) with a fake backend | 232 tests, 25 files |
| Dataset evaluation | Accuracy on generated document images and hand-written cases, scored against known answers | `run_complete_suite.py`, `make_hard_dataset.py --score` | 63 + 54 + 35 page images, 120+ text cases |

All test data is fictional. Every generated page carries the word "SAMPLE". No real person's documents were used.

---

## 6.2 Test Case 1: "I don't know" document type (automatic classification)

**Feature:** On *Add a document* the user can press **"I don't know"** instead of picking a type. The backend then works out the type in this order:

1. The file name.
2. The words on the page, using English and Hindi keyword rules (no AI model). A word in the heading counts double. A type is chosen only if it scores at least 4 points and leads the next type by at least 2.
3. For a photo, the vision AI model, but only if the user agreed to both cloud reading and medical reading.

If none of these is confident, the system **asks the user** instead of guessing.

| TC | Input (page text / file) | Expected output | Actual | Result |
|---|---|---|---|---|
| 1.1 | Bilingual Aadhaar: "भारत सरकार / GOVERNMENT OF INDIA … जन्म तिथि/DOB … मेरा आधार, मेरी पहचान" | `identity` (ID card) | identity | Pass |
| 1.2 | "INCOME TAX DEPARTMENT … Permanent Account Number Card" | `identity` | identity | Pass |
| 1.3 | Hindi only: "आय प्रमाण पत्र … परिवार की वार्षिक आय ₹ 1,10,000" | `income_certificate` | income_certificate | Pass |
| 1.4 | "Ayushman Bharat – Pradhan Mantri Jan Arogya Yojana … PMJAY ID" | `health_insurance` | health_insurance | Pass |
| 1.5 | Post-office savings passbook text | `bank_passbook` | bank_passbook | Pass |
| 1.6 | "Rx … Tab. Metformin 500 mg" | `prescription` (medical type) | prescription | Pass |
| 1.7 | Hospital final bill | `medical_bill` | medical_bill | Pass |
| 1.8 | Electricity bill (has "bill" and "total" but nothing medical) | No type; ask the user ("Type not known") | needs_classification | Pass |
| 1.9 | Leave letter / ration card (not a supported type) | No type; ask the user | needs_classification | Pass |
| 1.10 | Photo, type "I don't know", no medical-reading consent | Not sent to the AI; user asked to choose the type | needs_classification | Pass |

**Measured results**

| Test set | Score |
|---|---|
| Held-out page texts (36 cases in wording the rules were *not* written from; 33 typed, 3 that should go back to the user) | **36 / 36 (100%)**, wrong type given: 0 |
| Generated document pages, rules only (no AI) | **56 / 63 (88.9%)**. The 7 misses were ID-card photos; for each one the system asked the user rather than giving a wrong type. |
| Pages typed by the AI model (live run) | **24 / 24 (100%)** |

Design point: the classifier is built to **never give a wrong type silently**. When it is unsure it shows "Type not known" and the user chooses.

---

## 6.3 Test Case 2: Emergency detection (chest pain etc. → Emergency screen)

**Feature:** Before any doctor search, the request is checked against fixed **red-flag rules**. They need no AI and no internet, so they keep working when the AI service is down. The rules cover chest pain, stroke signs, breathlessness, heavy bleeding, fainting, seizure, self-harm, severe allergy, and fever together with confusion. They match English, Hindi (Devanagari) and Hindi typed in English letters.

If a flag is found, the search stops and the **Emergency screen** shows large call buttons for **112** (any emergency) and **108** (ambulance). The AI model can *add* an emergency the rules missed but can **never clear** one the rules found; only the user can.

| TC | Input (what the senior types) | Expected output | Actual | Result |
|---|---|---|---|---|
| 2.1 | "I have chest pain since morning" | Emergency screen, flag `chest_pain`, call 112 / 108 | As expected | Pass |
| 2.2 | "Seene mein bahut dard ho raha hai, pasina aa raha hai" (Hinglish) | Emergency, `chest_pain` | As expected | Pass |
| 2.3 | "मुझे सीने में दर्द हो रहा है" (Hindi) | Emergency, `chest_pain` | As expected | Pass |
| 2.4 | "my mother's face is drooping on one side" | Emergency, `stroke_signs` | As expected | Pass |
| 2.5 | "saans nahi aa rahi" | Emergency, `breathlessness` | As expected | Pass |
| 2.6 | "He FAINTED and now has CHEST PAIN" (mixed case, extra spaces) | Emergency, flags `chest_pain`, `fainting` | As expected | Pass |
| 2.7 | "I don't want to live anymore" | Emergency + **Tele-MANAS 14416** mental-health line shown first | As expected | Pass |
| 2.8 | "high fever since morning" (fever alone) | **Not** an emergency → normal doctor search | No flag | Pass |
| 2.9 | "high fever since morning, now he seems confused" | Emergency, `fever_confusion` | As expected | Pass |
| 2.10 | "Mujhe dil ke doctor ko dikhana hai" (wants a heart doctor) | Not an emergency → cardiologist search | No flag | Pass |
| 2.11 | "seene ka x-ray karwana hai" / "khoon ki jaanch karwani hai" | Not an emergency (look-alike words) | No flag | Pass |
| 2.12 | "no chest pain, just a routine heart checkup" | Flagged anyway (rules lean towards "yes") | `chest_pain` | Pass (by design) |
| 2.13 | Rule finds chest pain, AI model thinks it is not happening now | User asked "Do you want to call emergency services (112 or 108) now?"; the search continues only on "No" | As expected | Pass |
| 2.14 | Rule finds chest pain, AI service is down | Still an emergency (the rules work alone) | As expected | Pass |

**Coverage:** 80 emergency phrases across all 9 flags and 3 languages are all detected. 20 ordinary doctor requests raise no flag (0 false alarms on that set).

---

## 6.4 Test Case 3: Document status labels (Ready, Expired, Missing)

**Feature:** Every saved document gets a plain-language label. On **"Do I have what I need?"** the user ticks what an office asked for, and each item is marked **Ready**, **Needs your check**, **Being read**, **Expired** or **Not in your documents** (missing), with the next step to take. Examples assume today is 20 Nov 2026.

### a) Document list labels

| TC | Input (document state) | Expected label | Actual | Result |
|---|---|---|---|---|
| 3.1 | Confirmed insurance card, expiry 2029-08-28 | **Ready**, "Valid till 28 Aug 2029" | As expected | Pass |
| 3.2 | Confirmed insurance card, expiry 2025-03-31 | **Expired**, "Ended 31 Mar 2025" | As expected | Pass |
| 3.3 | Confirmed income certificate, no expiry, issued 2026-04-02 | **Ready**, "Issued 2 Apr 2026" | As expected | Pass |
| 3.4 | Read, waiting for the owner to check the details | **Please check** | As expected | Pass |
| 3.5 | Still being read in the background | **Reading** ("You can leave this screen") | As expected | Pass |
| 3.6 | Blurred photo sent back | **Photo not clear**, "Please add a clearer photo" | As expected | Pass |
| 3.7 | Type could not be found | **Type not known** | As expected | Pass |
| 3.8 | Mixed list | Ordered: to check → being read → problems → expired → ready | As expected | Pass |

### b) "Do I have what I need?" checklist

| TC | Office asks for | What the vault holds | Expected | Actual | Result |
|---|---|---|---|---|---|
| 3.9 | Proof of age | Confirmed ID card with date of birth | **Ready** | As expected | Pass |
| 3.10 | Health cover | Insurance card that has expired | **Expired** + button "Add the new copy" | As expected | Pass |
| 3.11 | Bank details | No passbook | **Not in your documents** + button "Add it" | As expected | Pass |
| 3.12 | Proof of income | Income certificate waiting for review | **Needs your check** + "Check it now" | As expected | Pass |
| 3.13 | Health cover | One expired card **and** one valid card | **Ready** (a usable document beats an expired one) | As expected | Pass |
| 3.14 | Proof of identity | ID card that was rejected or failed to read | **Not in your documents** | As expected | Pass |
| 3.15 | Health cover | Confirmed card but a required field (e.g. policy number) is empty | Not Ready (counted as unconfirmed) | As expected | Pass |
| 3.16 | Health cover | Old card confirmed, its renewal waiting for review | **Ready** (the old copy stays in use) | As expected | Pass |

---

## 6.5 Other important test cases

### a) Photo quality and file safety

| TC | Input | Expected output | Result |
|---|---|---|---|
| 4.1 | Sharp phone photo of a document | Accepted for reading | Pass |
| 4.2 | Blurred photo (sharpness score below 35; blurred test pages score 12–26, clear ones 47+) | Marked blurry → sent to review or asked for again | Pass |
| 4.3 | PDF with 21 pages | Refused: "The PDF has more than 20 pages" | Pass |
| 4.4 | PDF containing JavaScript, including JavaScript hidden in a compressed stream | Refused | Pass |
| 4.5 | A program renamed to `.png`; a web page renamed to `.jpg` | Refused (actual file contents checked, not the extension) | Pass |
| 4.6 | "Pixel bomb" PNG (68 bytes that opens to gigapixels) | Refused | Pass |
| 4.7 | Empty or cut-off PNG / PDF | Refused | Pass |

File-safety dataset: **19 / 19 files** got the right verdict (4 safe files accepted, 15 unsafe files refused).

### b) Typing details in by hand (validation)

| TC | Input | Expected output | Result |
|---|---|---|---|
| 5.1 | Date of birth "14/08/1952" | Stored as 1952-08-14 | Pass |
| 5.2 | Date of birth "31/02/1952" | Refused: impossible date | Pass |
| 5.3 | Aadhaar "650483028029" | Refused: fails the Verhoeff check digit | Pass |
| 5.4 | PAN "ABCDE12345" | Refused: wrong PAN format | Pass |
| 5.5 | Annual income "145000" | Stored as the number 145000 | Pass |
| 5.6 | Unknown field "blood_group" on an ID card | Refused | Pass |

Manual-entry dataset: **49 / 49** cases right.

### c) Privacy and security

| TC | Input | Expected output | Result |
|---|---|---|---|
| 6.1 | Valid Aadhaar "4407 5696 3712" saved | Stored only masked ("XXXX XXXX 3712") plus a hash; the full number is in no API answer, table or log | Pass |
| 6.2 | Page text sent to the AI model | Owner's name and ID numbers replaced by placeholders such as `[AADHAAR_1]` first | Pass |
| 6.3 | Wrong password vs. a login that doesn't exist | Same error message, so account existence is not revealed | Pass |
| 6.4 | 5 wrong passwords within 15 minutes | Login blocked until the window ends, even for the right password | Pass |
| 6.5 | Request with a forged or foreign token | Refused | Pass |

Privacy dataset: **221 / 222** checks passed. Known limitation: an account number printed with **no label** next to it is not hidden.

### d) Evaluation suite summary (parts that do not depend on the OCR engine)

| Part | Passed / total | Score |
|---|---|---|
| Classification on held-out wording | 36 / 36 | 100% |
| AI classification (live) | 24 / 24 | 100% |
| Fields auto-confirmed without review that were right | 8 / 8 | 100% (no wrong value saved without the user's check) |
| Fields right after the owner's review | 233 / 233 | 100% |
| File safety | 19 / 19 | 100% |
| Manual entry | 49 / 49 | 100% |
| Vault database + API checks | 26 / 26 | 100% |
| Privacy checks | 221 / 222 | 99.5% |

*Source: complete-project evaluation suite run of 31 Oct 2026.*

---

## 6.6 OCR test dataset

The system reads Indian documents such as Aadhaar cards, income certificates, passbooks and prescriptions. Real ones cannot be used for testing because they hold personal data, so a **synthetic dataset was generated in code** (Python with Pillow, OpenCV and NumPy):

- Every name, Aadhaar, PAN and amount is fictional.
- Aadhaar numbers carry a valid Verhoeff check digit, so they pass the real format checks.
- Every page is stamped **SAMPLE**.
- Each page is saved with its **ground truth**: the exact printed value and the value the system should store.

### a) Datasets

| Dataset | Pages | What it contains | Used for |
|---|---|---|---|
| **OCR benchmark** | 35 images + 2 PDFs | 7 document types × 5 versions: **clean, blurred, rotated, low light, phone photo**; plus one text PDF and one scanned PDF | Choosing the OCR engine |
| **Hard dataset** | 9 clean originals + 54 damaged images | 9 document types × 6 real-world capture conditions (below), realistic layouts | Stress-testing reading and field extraction |
| **Complete dataset** | 63 pages | 39 pages in known layouts with capture damage, 18 pages in **unseen layouts** (label wording the code was never tuned on), 4 text PDFs, 2 scanned PDFs; three applicant "packs" (ID + income + residence for one person) | Final end-to-end scoring |
| **Unit test files** | 12 files | Sharp and blurred photo, rotated phone photo, broken PNG/PDF, oversized image, PDF with no pages, PDF with too many pages, 2- and 4-page scans | Edge cases in automated tests |

**Document types covered:** ID card (Aadhaar/PAN), income certificate, residence proof, health insurance card, bank passbook, disability certificate, lab report, prescription, medical bill.

**Realistic layouts and clutter in the hard dataset:**

- **Layouts:** unlabelled values (ID card), paragraphs (certificates), label above value (insurance card), dotted leaders (passbook), tables (lab report).
- **Language and formats:** Hindi + English text; dates such as DD/MM/YYYY and 31-Mar-2027; Indian amounts such as "Rs. 2,66,000/-" and amounts in words ("Two Lakh Sixty Six Thousand").
- **Clutter:** security-pattern backgrounds, watermark, rotated rubber stamps overlapping text, signatures, photo box, QR code.

### b) Capture conditions (hard dataset)

| Condition | How it was made | What it simulates |
|---|---|---|
| **Scan** | Tilt of ±1°, light noise, JPEG quality 85 | Flatbed scanner |
| **Photocopy** | Grayscale, harsh **contrast** boost (faint backgrounds vanish), thicker strokes, speckle, toner streaks, tilt of ±1.5°, JPEG 75 | Xerox copy, very common for Indian certificates |
| **Phone photo** | **Perspective tilt** (paper shot at an **angle** on a table, with table texture around it), uneven **lighting** (75–105%), slight blur, noise, JPEG 55 | Normal mobile capture |
| **Phone, shadow + dim** | Perspective, a **hand shadow** over part of the page, **low light** (45–75% brightness), heavy noise, JPEG 50 | Photo taken indoors in poor light |
| **Phone, glare + shake** | Perspective, bright **glare** spot (flash or tube light), **motion blur** from shaking hands, JPEG 55 | Shiny laminated card, shaky hands |
| **Worn paper** | **Faded ink**, **fold creases**, a tea/water **stain**, tilt of ±2.5°, low resolution, JPEG 60 | Old certificate kept folded for years |

The generator uses a fixed random seed, so it can **recreate exactly the same dataset** every time.

### c) How the OCR was scored

- **Read %:** the printed value appears in the OCR text (letter case and spaces ignored).
- **Parsed %:** the system stored the *correct final value* in the right field, e.g. "Rs. 2,66,000/-" → 266000.
- **CER:** character error rate against the known text (engine benchmark).

### d) Results

**Engine comparison (OCR benchmark, 35 pages, CPU)**

| Engine | Fields exact | CER | Time / page |
|---|---|---|---|
| RapidOCR (PP-OCRv4 mobile) | 72% | 5.7% | 2.8 s |
| PaddleOCR 3.7 (PP-OCRv6 medium) | **97%** | **1.4%** | 24.6 s |

**PaddleOCR on the hard dataset (54 damaged pages)**

| Condition | Read % | Parsed % (rules only) | Parsed % (rules + AI fill-in) |
|---|---|---|---|
| Scan | 94 | 66 | 89 |
| Photocopy | 94 | 66 | 86 |
| Phone photo | 97 | 66 | 89 |
| Shadow + dim | 97 | 69 | 89 |
| Glare + shake | 97 | 57 | 83 |
| Worn paper | 91 | 54 | 74 |
| **Overall** | **95** | **63** | **85** |

The text itself was read well (95%), but turning it into the right fields was harder. The weakest case was ID cards, where the name is printed with no label.

**Vision AI model vs. local OCR (same pages, before the user's review)**

| Test | Local OCR (PaddleOCR + rules) | Vision model |
|---|---|---|
| 18-page sample, one page per layout per type | 36 / 70 fields (51.4%) | **66 / 70 (94.3%)** |
| ↳ unseen layouts only | 12 / 35 (34.3%) | **35 / 35 (100%)** |
| 19 pages from the full live run | 27 / 58 (47%) | **58 / 58 (100%)** |
| Full live suite (29 Oct 2026) | — | **62 / 65 (95.4%)** |

*Vision-model figures were measured during development (October 2026).*

**Final design decision:** the vision model did far better, especially on layouts it had never seen. The system therefore reads photos and scanned pages with the vision model only, and PaddleOCR was removed. PDFs that store their text are still read directly on the server, which is exact and sends nothing out. Every value is still shown to the owner to confirm before it is saved.
