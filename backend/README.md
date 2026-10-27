# SeniorLife OS: first two LangGraph workflows

This local mentor demo contains two separate `StateGraph` workflows:

1. **Document Intake:** validate → classify → extract → validate fields → human review → publish a confirmed vault record.
2. **Benefits Case Manager:** choose a curated scheme → retrieve/validate its source → evaluate confirmed facts → match documents in a subgraph → identify gaps → draft and review a manual handoff package.

The design uses `TypedDict` states, small node functions, conditional edges, subgraphs, `invoke()`, and `interrupt()` as in the practice code. Each result contains an `events` list showing the node path.

## Setup

With Python 3.11 on Windows PowerShell. Create the venv inside `backend/`, then run everything **from the repository root** (the code is imported as the `backend` package):

```powershell
py -3.11 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
backend\.venv\Scripts\python.exe backend\run_demo.py
backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
```

If `py` is unavailable, create the venv with another installed Python 3.11 executable. `backend/.venv` is Git-ignored. Docker and CI use Python 3.12.

### Folder layout

| Folder | What is in it |
|---|---|
| `graphs/` | the two LangGraph workflows (`document_graph.py`, `benefits_graph.py`) |
| `tools/` | OCR, value parsing, layout rules, medical tables, Google Vision |
| `agents/` | LLM calls and the model providers (Groq, OpenAI, mock) |
| `utils/` | privacy (masking, hashing, redaction), tracing filter, `.env` loading |
| `storage/` | the document vault: database tables, migrations, encrypted file store, the service that runs the document graph and saves the result |
| `demo/` | sample states; `api/` exposes them (and the document vault) over HTTP |
| `core/`, `config/` | settings from `config/application.yml`, filled from environment variables |
| `errors/`, `common/` | standard error responses and shared schemas |
| `tests/` | unit tests and the dataset scripts |

### API server

```powershell
backend\.venv\Scripts\python.exe backend\run_server.py
```

This starts FastAPI on port 8000 (`SERVER_PORT`). API docs are at `http://localhost:8000/docs`, and the demo routes are under `/api/v1/demo`. Settings come from `backend/config/application.yml`; each `${NAME:default}` there is read from the environment or `.env`. The `/actuator` admin routes are on by default (`ACTUATOR_ENABLED`) and have no login yet, so do not expose the server outside your machine.

## Direct `invoke()` examples

The fixture functions in [demo_cases.py](demo/demo_cases.py) return ordinary dictionaries you can inspect and edit:

```python
from pprint import pprint
from backend.graphs.document_graph import build_document_graph
from backend.graphs.benefits_graph import build_benefits_graph
from backend.demo.demo_cases import document_success_state, benefits_success_state

document_graph = build_document_graph()
benefits_graph = build_benefits_graph()

initial_state = document_success_state()
document_result = document_graph.invoke(initial_state)
print(document_result["status"])
pprint(document_result["events"])
pprint(document_result["vault_record"])

benefits_state = benefits_success_state(document_result["vault_record"])
benefits_result = benefits_graph.invoke(benefits_state)
print(benefits_result["status"])
pprint(benefits_result["events"])
pprint(benefits_result["criterion_results"])
pprint(benefits_result["application_package"])
```

To watch node updates as they happen, use the same handwritten state with `stream()`:

```python
for update in document_graph.stream(initial_state, stream_mode="updates"):
    print(update)
```

Call `stream()` **instead of** `invoke()` for a given demonstration run; running both executes the workflow twice. `run_demo.py` uses `invoke()` and prints the accumulated node path plus intermediate extraction, assessment, and document-match fields.

### Handwritten Document Intake state

`pages[].text` simulates OCR output. The malformed date exercises human correction. Prefilling `review_decision` is an explicit senior decision for a one-call demo.

```python
initial_state = {
    "actor_id": "senior-meera",
    "document_id": "doc-income-001",
    "claimed_type": "income_certificate",
    "document": {
        "owner_id": "senior-meera",
        "owner_name": "Meera Rao",
        "filename": "income_certificate_photo.jpg",
        "mime_type": "image/jpeg",
        "size_bytes": 145000,
        "as_of": "2026-09-22",
        "pages": [{
            "page": 1,
            "quality": 0.92,
            "text": "Name: Meera Rao\nAnnual income: ₹1,20,000\nIssue date: 2026/06/01\nIssuer: Demo District Office",
        }],
    },
    "review_decision": {
        "action": "confirm",
        "reviewer_id": "senior-meera",
        "edits": {"issue_date": "2026-06-01"},
    },
    "simulate_ocr_failures": 1,
}
result = document_graph.invoke(initial_state)
print(result["status"], result["events"])
```

**Document edge case:** Change `initial_state["document"]["pages"][0]["quality"]` to `0.25`. Result: `needs_reupload`; there is no `vault_record`. Remove `review_decision` instead to see `needs_review` with no publication.

### Handwritten Benefits state

The income record is the exact output of Document Intake. Two other previously reviewed documents supply identity and residence evidence.

```python
income_record = document_graph.invoke(initial_state)["vault_record"]
identity_record = {
    "document_id": "doc-identity-001", "owner_id": "senior-meera",
    "type": "identity", "status": "confirmed", "expiry_date": None,
    "confirmed_fields": {
        "name": {"value": "Meera Rao", "source_ref": "document:doc-identity-001:page:1:line:1"},
        "date_of_birth": {"value": "1958-05-15", "source_ref": "document:doc-identity-001:page:1:line:2"},
    },
}
residence_record = {
    "document_id": "doc-residence-001", "owner_id": "senior-meera",
    "type": "residence_proof", "status": "confirmed", "expiry_date": None,
    "confirmed_fields": {
        "name": {"value": "Meera Rao", "source_ref": "document:doc-residence-001:page:1:line:1"},
        "residence_state": {"value": "MH", "source_ref": "document:doc-residence-001:page:1:line:2"},
        "issue_date": {"value": "2026-02-01", "source_ref": "document:doc-residence-001:page:1:line:3"},
    },
}
benefits_state = {
    "actor_id": "senior-meera", "case_id": "benefit-case-001",
    "case_version": 1, "expected_case_version": 1,
    "scheme_id": "demo_senior_support", "jurisdiction": "MH",
    "as_of": "2026-09-22", "profile_facts": {},
    "documents": [identity_record, residence_record, income_record],
    "review_decision": {"action": "approve", "reviewer_id": "senior-meera"},
}
result = benefits_graph.invoke(benefits_state)
print(result["status"], result["events"])
```

**Benefits edge case:** Remove `income_record` from `documents`. The income criterion becomes `unknown`, the requirement becomes `missing`, status is `needs_input`, and no package is created. For a conflict, add a different confirmed `annual_income` to `profile_facts`; the run stops with `blocked_conflict`.

## Actual pause and resume at review

Omit a prefilled `review_decision`, set `use_interrupt=True`, and compile with an **encrypted** checkpointer. Use the same `thread_id` to resume.

The paused state holds page text, names and full ID numbers, so the document graph refuses a plain checkpointer. `make_checkpointer()` encrypts every checkpoint with AES using `SENIORLIFE_CHECKPOINT_KEY` (64 hex characters) from the environment or `.env`. Make a key with `python -c "import secrets; print(secrets.token_hex(32))"`. Without a key, no checkpointer is made. A checkpoint saved without encryption, or with another key, can't be read. `run_and_forget(graph, input, config)` deletes a run's checkpoints once it ends.

```python
from langgraph.types import Command
from backend.graphs.document_graph import build_document_graph
from backend.demo.demo_cases import document_success_state
from backend.utils.checkpoints import make_checkpointer

graph = build_document_graph(checkpointer=make_checkpointer())
state = document_success_state()
state.pop("review_decision")
state["use_interrupt"] = True
config = {"configurable": {"thread_id": "doc-demo-1"}}

pending = graph.invoke(state, config)
print(pending["__interrupt__"])
result = graph.invoke(Command(resume={
    "action": "confirm", "reviewer_id": "senior-meera",
    "edits": {"issue_date": "2026-06-01"},
}), config)
print(result["status"])
```

The Benefits graph (Part 2, not changed) supports the same pattern with `build_benefits_graph(checkpointer=InMemorySaver())` and does not require encryption yet; resume with `{"action": "approve", "reviewer_id": "senior-meera"}`. Without a decision or `use_interrupt`, the graphs stop at `needs_review`/`awaiting_review` without publishing or committing.

## Document vault (database, stored files, API)

Uploads are kept in PostgreSQL plus an encrypted file folder, and paused reviews survive a restart.

**Setup (once):**

1. Create two databases in your PostgreSQL, e.g. `seniorlife_os` and `seniorlife_os_test` (the test one must end in `_test`; its tests empty it).
2. Add to `.env` (or the environment):

   | Setting | What it is |
   |---|---|
   | `DATABASE_URL` | `postgresql+asyncpg://USER:PASSWORD@localhost:5432/seniorlife_os` (any `postgresql` URL works; psycopg is used) |
   | `TEST_DATABASE_URL` | the same for `seniorlife_os_test`; without it the database tests are skipped |
   | `SENIORLIFE_STORAGE_KEY` | 64 hex characters, encrypts stored files: `python -c "import secrets; print(secrets.token_hex(32))"` |
   | `SENIORLIFE_STORAGE_DIR` | optional; default `var/documents` in the repository (git-ignored) |
   | `SENIORLIFE_CHECKPOINT_KEY`, `SENIORLIFE_HASH_KEY` | as above (paused reviews, ID hashes) |
   | `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_ECHO` | optional; connections kept open (5) and extra under load (10), and SQL logging (off; values are never logged). Read through `config/application.yml` (`database:`), which has no built-in URL or password |

   Never change the storage or checkpoint key after data is stored: old files and paused reviews can't be read with a new key.
3. Create the tables: `backend\.venv\Scripts\alembic.exe -c backend\alembic.ini upgrade head`. LangGraph's checkpoint tables are created by the checkpointer on first start; migrations never touch them.

**API** (`api/documents.py`; sign-in isn't built yet, so the caller names themselves with the `X-Actor-Id` header, which is refused when `APP_ENV=production`):

| Call | What it does |
|---|---|
| `POST /api/v1/documents?filename=card.png&claimedType=identity` | upload; the file is the raw body, its type the `Content-Type` (PDF, PNG, JPEG, at most 5 MB). Options: `ownerName`, `cloudOcrConsent`, `medicalCloudConsent`, `useLlm` |
| `GET /api/v1/documents?type=identity` | my documents (no field values) |
| `GET /api/v1/documents/{id}` | one document with its confirmed fields (ID numbers masked) |
| `GET /api/v1/documents/{id}/review` | the values waiting for my review, with full ID numbers so I can compare them with the card |
| `POST /api/v1/documents/{id}/review` | `{"action": "confirm" or "reject", "edits": {...}}` |
| `POST /api/v1/documents/manual` | type the details in, no file: `{"documentType": "identity", "fields": {...}, "ownerName": "..."}` (see below) |
| `POST /api/v1/documents/{id}/versions?filename=new.png` | replace a confirmed document with a newer copy (e.g. a renewed certificate); raw body and options as for upload |
| `POST /api/v1/documents/{id}/versions/manual` | replace it by typing in the new details: `{"fields": {...}}` |
| `DELETE /api/v1/documents/{id}` | delete the document and everything stored for it |

```powershell
curl.exe -X POST "http://localhost:8000/api/v1/documents?filename=card.png&claimedType=identity" -H "X-Actor-Id: senior-meera" -H "Content-Type: image/png" --data-binary "@card.png"
```

Someone else's document is always "not found". Responses carry `Cache-Control: no-store`. A refused review (for example a required field left empty) can be sent again without reading the file again.

During review the owner can also **fill in a required field OCR missed**, such as a name printed without a label. The review view lists these as `missingFields`; send them in `edits` like any correction. The value is checked like a read one, and is stored with `source_ref` `document:<id>:entered_by_owner` and `correctedByUser: true`. Only fields that were read or that the document type requires can be edited; anything else is refused.

**Typing a document in, with no file** (`POST /api/v1/documents/manual`): for when a photo isn't possible or typing is easier.
- Allowed fields: the type's required fields, plus `aadhaar_number` and `pan_number` for `identity`, `issuing_authority` and `expiry_date` for income and residence certificates, `expiry_date` for a disability certificate. Medical lists (`tests`, `medicines`, `items`) are sent as lists of rows.
- Each value gets the same checks as a value read from a page (Aadhaar checksum, PAN shape, dates, amounts, no future dates). Anything wrong gives `422` with the reasons in `details.errors` (never the values) and nothing is stored.
- Typing in counts as the owner's confirmation: the document is saved `confirmed` at once, ID numbers masked and hashed, with `entry: "manual"`, no file, and every field's `source_ref` ending in `entered_by_owner`. A scheme that needs the paper itself can still ask for it.
- A card already in the vault (same ID hash) is reported in `warnings`, as for uploads.
- Needs the `0002_manual_entry` migration (`alembic ... upgrade head`).

**Renewing a document** (`POST /{id}/versions`, or `/versions/manual` to type it in): for a renewed income certificate, a new insurance year, and so on.
- Only a confirmed document, and only when no newer version is already being read or waiting for review (`409` otherwise). The document type stays the same.
- The new copy is read and reviewed like an upload. Until it is confirmed, the old version stays in use: the document shows the old fields, and the new one under `replacement` (its version, status, flags, warnings, errors).
- Once the new one is confirmed, the old version is marked `replaced`, and its fields, page text, ID hashes and file are removed. Only its version number, dates and status are kept.
- If the new copy is rejected, unreadable or fails, its file is removed and the old version stays in use. The same card is not reported as "already in the vault" for its own renewal.

**Deleting a document** (`DELETE /{id}`): removes every version, field, page text, ID hash, run, paused checkpoint and stored file. The audit trail keeps that it was deleted (audit events hold no values). Refused with `409` only while the file is being read at that moment.
- Needs the `0003_versions_hold_files` migration: each version now holds its own file details, so a renewal's file can sit next to the old one until it is confirmed.

**What is kept where:**

| Where | What |
|---|---|
| file folder | the upload, AES-256-GCM encrypted under a random name; only decrypted to a private temporary folder while OCR reads it. Deleted when the run ends without a confirmed version (rejected, unreadable, unknown type, failed) |
| database | documents, versions, status, review flags, warnings, events; confirmed fields and page text with ID numbers masked; keyed ID hashes (duplicates are only looked for among the same owner's documents); an audit trail without values. SQL errors never show the values sent |
| encrypted checkpoint (PostgreSQL) | everything a paused review needs, including unmasked page text and full ID numbers. Deleted when the run ends |

The owner's earlier confirmed documents are passed to the graph, so a repeated ID card and an organization name one or two letters away from an earlier one are flagged.

## Connection and local-demo boundaries

Document Intake returns `vault_record` only after the owner confirms extracted fields. Put this record in the Benefits state's `documents` list. Benefits checks owner, confirmation, required fields, expiry, and provenance. A later confirmed upload can be passed to a *new* Benefits run for reassessment; the two graphs do not share one checkpoint.

Shared document rules live in `tools/document_rules.py`: `match_requirements()` checks a fixed document type, and `match_proofs()` checks a need such as `proof_of_age` against every document type that can prove it (`PROOFS`). The Benefits graph does not use `match_proofs()` yet.

The sample states use handwritten `pages[].text`. A real image or PDF can be read with local OCR (`document.file_path`, see "Local OCR" below), after the file checks below. The field parser understands labelled values (see "Reading values" below). Values written inside sentences or printed with no label, such as certificate paragraphs or the number on an ID card, are not extracted yet. `Demo Senior Support Pension` and its `demo://` source are **fictional test data**, not real government eligibility advice. No government website is queried or application submitted. The graphs themselves never touch a database: uploads sent through the document vault API are saved by `storage/service.py` after the graph runs (see "Document vault" below). Benefits packages, tasks, and event trails exist only in the returned state, and no external action is taken. `InMemorySaver` and `make_checkpointer()` lose state after process exit; the vault uses the PostgreSQL checkpointer. The local demo uses deterministic extraction and a curated mock catalogue to run without API keys. Its agentic structure is visible in evidence-dependent routing, retries, a document-match subgraph, provenance, and approval boundaries, rather than one generated answer.

Optional structured LLM selection nodes are included for document classification and scheme resolution. Set `use_llm=True` and omit `claimed_type` or `scheme_id` respectively. The provider is chosen by `SENIORLIFE_LLM_PROVIDER` (see `agents/models.py`):

| Provider | Needs | Model |
|---|---|---|
| `groq` (default) | `GROQ_API_KEY` | `SENIORLIFE_LLM_MODEL`, or `openai/gpt-oss-20b` if unset |
| `openai` | `OPENAI_API_KEY` | `SENIORLIFE_LLM_MODEL` (required) |

These variables are read from the environment, or from `backend/.env` / the repository root `.env` (shell variables win). LangSmith tracing settings are read the same way; see "LangSmith tracing" below.

### ID numbers in the vault

Aadhaar, PAN, account, and insurance policy numbers are stored masked (`XXXX XXXX 9012`) plus a keyed hash (HMAC-SHA256) for duplicate detection; the full number is never stored. This needs `SENIORLIFE_HASH_KEY` (at least 32 characters) in the environment or `.env`. Without it, a document that contains an ID number stops with `failed_tool` instead of being published. Documents without ID numbers don't need the key. Never change the key after data is stored, or old hashes stop matching. Groq uses `langchain-groq` (`ChatGroq`); OpenAI uses `langchain-openai` (`ChatOpenAI`). Both are in `requirements.txt`. Every prompt is redacted first (`utils/privacy.py`). The returned choice is still restricted to the local catalogue. The default sample states never call an external model.

### File checks (`tools/file_safety.py`)

Before anything reads an uploaded file, the `check_file` node checks it:
- **Real type:** the first bytes must match the claimed type.
- **Risky PDFs:** JavaScript, launch actions, attached files, media, script forms and password protection are refused. Hidden names and compressed streams are opened before searching.
- **Size bombs:** images over 60 MP and PDF pages too large to render are refused before decoding.
- **Virus scan:** `SENIORLIFE_VIRUS_SCANNER` = `none` (default, with a warning), `defender` (Windows Defender's `MpCmdRun`, which never removes the file) or `clamav` (a ClamAV daemon at `SENIORLIFE_CLAMD=host:port`). With `SENIORLIFE_SCAN_REQUIRED=true`, an upload is refused when no scan could be made; use this in production.
- **Results:** a dangerous file → `rejected`; a damaged file, an empty PDF or one over 20 pages → `needs_reupload`. The file's SHA-256 and scan result are kept in `document.sha256` and `document.virus_scan`.
- **Not sure:** depending on the machine's settings, Windows Defender may send file details to Microsoft's cloud protection. ClamAV keeps everything on the server.

### Local OCR (`tools/ocr.py`)

`read_document(path, mime_type)` reads a PNG, JPEG, or PDF on this machine with PaddleOCR (English, PP-OCRv6 medium) and returns pages in the same shape as the `pages[]` fixtures above, plus per-line `confidence` and `box`. PDFs that store text are read without OCR. Scanned pages are rendered at 300 DPI. Blurry pages are capped at quality 0.85 so they go to review.

The document graph uses it when the upload names a real file instead of page fixtures. Set `document.file_path` and leave out `pages`. The size is checked on disk, and the `read_file` node runs OCR before classification. A broken or blank file gives `needs_reupload`. An OCR engine crash is retried up to 3 times and then gives `failed_tool`. Each field takes its own line's confidence and keeps that line's `box`. Before fields are read, OCR text is tidied by `tools/ocr_postprocess.py`: odd dashes and quotes, full-width characters, and `Rs.`/`INR` before an amount become `₹`. Labels match even with dropped spaces or dotted leaders (`PolicyNo:`, `IFSC Code ......:`).

**Reading values (`tools/demo_tools.py`, `tools/layout.py`):**
- **Formats understood:**
  - dates such as `23/12/2026`, `23.12.2026` and `28-Aug-2029` (always day first; `2026/06/01` and two-digit years are refused);
  - amounts such as `Rs. 2,56,000/-` (`2.56.000` is refused);
  - state names such as `Kerala` → `KL`;
  - names with titles removed (`Smt. Anita Rao` → `Anita Rao`).
- **Layouts found:**
  - several `Label: value` pairs on one line;
  - a label with no colon (`Name Meera Rao`);
  - labels above their values on a two-column card (paired by position).
- **Repairs:** if a value fails its check, look-alike characters are swapped where the field's shape requires it (`DEM00206791` → `DEMO0206791`), and text after a date, amount or ID is cut off. Names are never changed.
- **Review:** anything found by a layout rule or repaired is **always sent to the owner for review**.
- **Organization names** (issuing authority, insurer, bank, lab, provider) are flagged for review, even when OCR was sure (`tools/free_text.py`), if:
  - they look cut off (`Medical Board, Demo D`);
  - a word is a near-misspelling of a common organization word (`Distriet`) or of a listed Indian district, town, bank or insurer (`Thame`, `Bajej`, `Heatlh`). The lists are in `tools/data/` (from Wikipedia, sources in each file);
  - the name is one or two letters away from the same field on one of the owner's earlier documents (passed in as `vault_organization_names`);
  - cloud OCR read the name differently from a sure local reading (the local reading is kept, the owner decides).
- **Model fallback:** with `use_llm=True`, required fields the rules missed (values inside sentences, values with no label) are requested from the model through the `llm_extract` node.
  - The text is redacted first.
  - An answer is kept only if it appears word for word in the document.
  - Every answer is sent to review.
  - If the model fails, the fields stay missing for the owner; nothing is guessed.
- **ID numbers on the page:** after review, ID numbers are masked everywhere in the stored page text, including numbers printed with no label (any checksum-valid Aadhaar number or valid PAN).
- **Runs that stop early:** only a review pause (`needs_review`) keeps full ID numbers, so the owner can check them against the card. Every other stop (`rejected`, `needs_reupload`, `needs_classification`, `failed_*`) masks ID fields, edits and page text in the result (`mask_on_stop`). Start a new run from the original upload, not from a masked result.

### Medical documents (`tools/medical.py`)

| Type | What is read |
|---|---|
| `lab_report` | patient name, lab name, report date, `tests` rows (name, value, unit, reference range, high/low flag) |
| `prescription` | patient name, doctor, date, `medicines` rows (name, form, strength, frequency, duration) |
| `medical_bill` | patient name, provider, bill date, total, `items` rows (description, amount) |

- **Tilted photos:** lab tables are read column by column, with the tilt corrected, so a value stays on its own test.
- **Review:** every row of a list is always sent to the owner for review. Bill items must add up to the total.
- **Flags:** high and low come only from the range printed on that row.
- **Consent:** medical pages are never sent to the cloud model, not even to work out their type, unless the state sets `medical_cloud_consent: True`. Without it, they are classified on this machine.
- **Read by the vision model (`tools/vision_extract.py`):** with both `cloud_ocr_consent` and `medical_cloud_consent`, a scanned or photographed prescription, lab report or medical bill is read by the Groq vision model (`GROQ_API_KEY`, `SENIORLIFE_VISION_MODEL`) straight into fields, because it reads handwriting and free layouts far better than local OCR.
  - Local OCR still runs first, to find ID numbers (Aadhaar, PAN, account, policy), ABHA health IDs and QR codes; they are painted black before the picture is sent. At most 3 pages, in one request.
  - The model's values are used, and **every one is sent to review** (it gives no confidence and can make up a plausible value, such as a wrong dose). Where local OCR read the same field differently, the flag says "the local reading differs".
  - ID numbers in its answer are masked; ID fields are always the local reading.
  - A handwritten page that local OCR reads badly is no longer sent back; a **blurry** photo still is (its ID numbers couldn't be found to black them out).
  - No key, an error or an unreadable answer: a warning, and the local reading carries on as before. A PDF with stored text is read only on this machine. The cloud is asked only once per document.
- **Samples:** `lab_report_state()`, `prescription_state()` and `medical_bill_state()` in `demo/demo_cases.py`.

### Cloud OCR backup (`tools/cloud_ocr.py`)

If local OCR leaves a required field missing, unsure, cut off or misread, or a page blurry, the file can be read once more in the cloud.
- **Engine:** `SENIORLIFE_CLOUD_OCR` picks one; it never switches to another service on its own.

  | Value | Service | Key |
  |---|---|---|
  | `google` (default) | Google Cloud Vision | `GOOGLE_VISION_API_KEY` |
  | `groq` | vision model on Groq (`SENIORLIFE_VISION_MODEL`, default `qwen/qwen3.8-27b`, a preview model) | `GROQ_API_KEY` |

  The Groq model returns plain text only, with no positions or confidence, so its values count as unsure and table layouts are read from the text. It took 11–88 s per page in testing.
- **Conditions:** the state sets `cloud_ocr_consent: True` (medical documents also need `medical_cloud_consent: True`), and the engine's key is set in the environment or `.env`.
- **Before sending:** image regions that show ID numbers, and any QR code, are painted black. The key goes in a request header, never in the URL.
- **Results:**
  - Cloud values only replace weak local ones, never ID numbers, and are always sent to review.
  - The cloud text is stored separately (`document.cloud_pages`) and masked the same way.
  - If anything fails, the local result is kept.

### LangSmith tracing

`LANGSMITH_TRACING=true` plus `LANGSMITH_API_KEY` (in the shell or `.env`) turns on tracing. `run_demo.py` prints whether it is on. Everything is filtered before it leaves the machine (`utils/tracing.py`):
- **What is sent:** node path, status, routes, review flags, confidence, line numbers and timings.
- **What is hidden:** every other value, including names, ID numbers, dates of birth, OCR text and LLM prompts, which show as `[hidden]`.

The filter is installed when the document graph is built and whenever `.env` loads a tracing key, so it also covers tracing switched on by environment variables alone.

```python
state = {
    "actor_id": "senior-meera", "document_id": "doc-upload-001", "claimed_type": "health_insurance",
    "document": {"owner_id": "senior-meera", "owner_name": "Meera Rao", "filename": "card.pdf",
                 "mime_type": "application/pdf", "file_path": "path/to/card.pdf", "as_of": "2026-09-22"},
}
result = build_document_graph().invoke(state)
```

The first run downloads the models to `~/.paddlex`. Expect roughly 15–50 s per page on a laptop CPU.

```python
from backend.tools.ocr import read_document
pages = read_document("backend/tests/datasets/ocr_benchmark/pages/identity__clean.png", "image/png")
print(pages[0]["text"])
```

Test data (images, PDFs, JSON) lives in `backend/tests/datasets/`, which is **not in Git**. `backend/tests/test_ocr.py` recreates its own small fixtures there. The real-PaddleOCR tests need the benchmark set in `backend/tests/datasets/ocr_benchmark/` and run only on request (about 5 minutes):

```powershell
$env:SENIORLIFE_RUN_OCR_TESTS = "1"; backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p test_ocr.py -v
```

`backend/tests/make_hard_dataset.py` makes a harder set of fake documents in `backend/tests/datasets/ocr_hard/`. They have realistic layouts, Hindi and English text, stamps and signatures, and photo, photocopy and worn-paper damage. Add `--score` to run PaddleOCR on them (about 25 minutes), or `--rescore` to re-score from the saved OCR text.

### Complete-project suite

`backend/tests/run_complete_suite.py` tests every part of the backend on a new fake dataset and gives each part a score from 0 to 100. Here, "every part" means OCR, the document graph before and after review, classification, privacy and prompt redaction, cloud blackout, file checks, manual entry, benefit rules, page-to-eligibility runs, the vault database and API, the main app, and the existing unit tests. `backend/tests/make_complete_dataset.py` makes the dataset in `backend/tests/datasets/complete/`, and the suite makes it on first use if it is missing. Results go to `backend/tests/datasets/results/` as `complete_<time>.json` / `.md`, plus `latest.json` / `latest.md`. Each score says what it counts, and every failure is listed.

```powershell
backend\.venv\Scripts\python.exe backend\tests\run_complete_suite.py            # about 10 minutes once the OCR output is saved (first run about 45)
backend\.venv\Scripts\python.exe backend\tests\run_complete_suite.py --quick    # fewer pages, no blackout re-reading
```

Other options:
- `--fresh-ocr` reads every page again.
- `--no-db` skips the vault part. That part needs `TEST_DATABASE_URL` naming a database whose name ends in `_test`, and it empties that database.
- `--no-unit` skips the unit tests.
- `--live` also scores the Groq vision model and LLM. It sends the fake pages to Groq and uses the API quota.

The suite never reads `.env`, except for `TEST_DATABASE_URL` and, with `--live`, the Groq key. It uses its own test hash key and turns tracing off.

See the [workflow technical blueprint](../docs/SeniorLife-OS-Workflow-Technical-Blueprint.md) for the intended real integrations.
