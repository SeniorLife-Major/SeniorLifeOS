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
| `demo/` | sample states; `api/` exposes them over HTTP |
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

Omit a prefilled `review_decision`, set `use_interrupt=True`, and compile with an in-memory checkpointer. Use the same `thread_id` to resume:

```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from backend.graphs.document_graph import build_document_graph
from backend.demo.demo_cases import document_success_state

graph = build_document_graph(checkpointer=InMemorySaver())
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

The Benefits graph supports the same pattern with `build_benefits_graph(checkpointer=InMemorySaver())`; resume with `{"action": "approve", "reviewer_id": "senior-meera"}`. Without a decision or `use_interrupt`, the graphs stop at `needs_review`/`awaiting_review` without publishing or committing.

## Connection and local-demo boundaries

Document Intake returns `vault_record` only after the owner confirms extracted fields. Put this record in the Benefits state's `documents` list. Benefits checks owner, confirmation, required fields, expiry, and provenance. A later confirmed upload can be passed to a *new* Benefits run for reassessment; the two graphs do not share one checkpoint.

Shared document rules live in `tools/document_rules.py`: `match_requirements()` checks a fixed document type, and `match_proofs()` checks a need such as `proof_of_age` against every document type that can prove it (`PROOFS`). The Benefits graph does not use `match_proofs()` yet.

The sample states use handwritten `pages[].text`. A real image or PDF can be read with local OCR (`document.file_path`, see "Local OCR" below), but files are **not** scanned for malware. The field parser understands labelled values (see "Reading values" below). Values written inside sentences or printed with no label, such as certificate paragraphs or the number on an ID card, are not extracted yet. `Demo Senior Support Pension` and its `demo://` source are **fictional test data**, not real government eligibility advice. No government website is queried or application submitted. Packages, tasks, and event trails exist only in the returned state; there is no database or external action. `InMemorySaver` loses state after process exit. The local demo uses deterministic extraction and a curated mock catalogue to run without API keys. Its agentic structure is visible in evidence-dependent routing, retries, a document-match subgraph, provenance, and approval boundaries, rather than one generated answer.

Optional structured LLM selection nodes are included for document classification and scheme resolution. Set `use_llm=True` and omit `claimed_type` or `scheme_id` respectively. The provider is chosen by `SENIORLIFE_LLM_PROVIDER` (see `agents/models.py`):

| Provider | Needs | Model |
|---|---|---|
| `groq` (default) | `GROQ_API_KEY` | `SENIORLIFE_LLM_MODEL`, or `openai/gpt-oss-20b` if unset |
| `openai` | `OPENAI_API_KEY` | `SENIORLIFE_LLM_MODEL` (required) |

These variables are read from the environment, or from `backend/.env` / the repository root `.env` (shell variables win). LangSmith tracing settings are read the same way; see "LangSmith tracing" below.

### ID numbers in the vault

Aadhaar, PAN, account, and insurance policy numbers are stored masked (`XXXX XXXX 9012`) plus a keyed hash (HMAC-SHA256) for duplicate detection; the full number is never stored. This needs `SENIORLIFE_HASH_KEY` (at least 32 characters) in the environment or `.env`. Without it, a document that contains an ID number stops with `failed_tool` instead of being published. Documents without ID numbers don't need the key. Never change the key after data is stored, or old hashes stop matching. Groq uses `langchain-groq` (`ChatGroq`); OpenAI uses `langchain-openai` (`ChatOpenAI`). Both are in `requirements.txt`. Every prompt is redacted first (`utils/privacy.py`). The returned choice is still restricted to the local catalogue. The default sample states never call an external model.

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
- **Organization names** (issuing authority, insurer, bank, lab, provider) are flagged for review if they look cut off (`Medical Board, Demo D`) or contain a near-misspelling of a common word (`Distriet`), even when OCR was sure (`tools/free_text.py`).
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

See the [workflow technical blueprint](../docs/SeniorLife-OS-Workflow-Technical-Blueprint.md) for the intended real integrations.
