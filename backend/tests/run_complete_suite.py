"""Complete-project suite: every part of the project, run on the dataset from
make_complete_dataset.py, with a score per part. Results go to tests/datasets/results/.

Nothing is tuned to pass. Each score says exactly what it counts (see SCORE_MEANING), every
failure is listed, and parts that could not run are reported as "not run" with the reason,
never as passed.

Run from the repo root (makes the dataset first if it is missing):
    backend/.venv/Scripts/python.exe backend/tests/run_complete_suite.py
Options:
    --quick       one copy of each page, no re-reading of blacked-out pages (for a fast look)
    --fresh-ocr   read every page again instead of using the saved OCR output
    --no-db       skip the parts that need TEST_DATABASE_URL
    --no-unit     skip running the existing unit tests
    --live        also use the real Groq models (vision + LLM). Sends the FAKE pages to Groq.

Privacy while it runs: the real .env is not read except for TEST_DATABASE_URL (database
part) and, with --live only, the Groq key. A test-only hash key is used. Nothing is sent
anywhere without --live.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
for path in (str(REPO), str(TESTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

DATA = TESTS / "datasets" / "complete"
RESULTS = TESTS / "datasets" / "results"
OCR_CACHE = DATA / "ocr_cache"
SUITE_HASH_KEY = "complete-suite-hash-key-0123456789abcdef"  # test-only, never the real one
OWNER = "senior-applicant"
OTHER = "senior-stranger"
AS_OF = "2026-10-27"

SCORE_MEANING = {
    "unit_tests": "existing unit tests that passed, out of those that ran (skipped ones listed apart)",
    "ocr_reading": "printed values that appear in the OCR text (case and spacing ignored)",
    "documents_before_review": "fields whose proposed value is right before the owner reviews (a page sent back counts as wrong)",
    "documents_after_review": "fields right in the vault after the owner corrects what was wrong (what the pipeline can deliver)",
    "auto_accept_safety": "fields confirmed WITHOUT review that are right (a wrong one is a critical problem)",
    "classification": "pages whose type was found without being told (no LLM)",
    "privacy": "privacy checks passed: masking, prompt redaction, tracing, final states free of full ID numbers",
    "cloud_blackout": "pages with an ID number where the blacked-out picture (what the cloud would get) shows no readable ID",
    "file_safety": "files given the right verdict (accept / refuse)",
    "manual_entry": "typed-in cases handled right (accepted with the right values, or refused for the right reason)",
    "benefits_rules": "eligibility cases and proof-matching cases with the right outcome",
    "benefits_pipeline": "applicants (page photos -> vault -> eligibility) given the right eligibility",
    "storage_api": "vault database + API checks passed (upload, review, renew, delete, manual entry, no leaks)",
    "main_api": "main app checks passed (health, demo scenarios, workflow endpoints, error format)",
    "live_vision": "medical fields right when the Groq vision model reads the page (only with --live)",
    "live_llm_classification": "pages typed right by the LLM (only with --live)",
}


# ---------- helpers ----------

class Section:
    """Checks for one part of the project. score = passed / total, in percent."""

    def __init__(self, name: str):
        self.name, self.passed, self.total = name, 0, 0
        self.failures: list[str] = []
        self.metrics: dict[str, Any] = {}
        self.status = "run"
        self.reason = ""
        self.seconds = 0.0  # set by main() when the part finishes

    def check(self, ok: bool, what: str) -> bool:
        self.total += 1
        if ok:
            self.passed += 1
        else:
            self.failures.append(what)
        return ok

    def skip(self, reason: str) -> "Section":
        self.status, self.reason = "not_run", reason
        return self

    def result(self) -> dict[str, Any]:
        score = round(100 * self.passed / self.total, 1) if self.total and self.status == "run" else None
        return {"status": self.status, "reason": self.reason, "score": score, "passed": self.passed,
                "total": self.total, "meaning": SCORE_MEANING.get(self.name, ""),
                "seconds": round(self.seconds, 1), "metrics": self.metrics,
                "failures": self.failures}


def say(text: str) -> None:
    print(text, flush=True)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def squash(text: str) -> str:
    return " ".join(str(text).split()).lower()


def id_forms(value: str) -> set[str]:
    """Spellings of an ID number that would count as showing it in full."""
    text = str(value)
    digits = re.sub(r"\D", "", text)
    forms = {text, text.replace(" ", ""), text.upper()}
    if len(digits) == 12:  # Aadhaar
        forms |= {digits, f"{digits[:4]} {digits[4:8]} {digits[8:]}", f"{digits[:4]}-{digits[4:8]}-{digits[8:]}"}
    elif digits and len(digits) >= 9 and digits == text:  # account number
        forms.add(digits)
    return {form for form in forms if len(form) >= 6}


def leaks(blob: str, ids: list[str]) -> list[str]:
    return [value for value in ids if any(form in blob for form in id_forms(value))]


def truth_ids(entry: dict[str, Any]) -> list[str]:
    from backend.utils.privacy import ID_FIELDS
    return [str(entry["fields"][key]["shown"]) for key in ID_FIELDS if key in entry["fields"]]


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- environment ----------

@contextlib.contextmanager
def isolated_environment(live: bool):
    """The suite never reads the real .env (except where noted) and never traces."""
    env = {k: v for k, v in os.environ.items()}
    env["SENIORLIFE_HASH_KEY"] = SUITE_HASH_KEY
    env["SENIORLIFE_VIRUS_SCANNER"] = "none"  # an EICAR test needs the owner's OK; see the report
    for flag in ("LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
        env[flag] = "false"
    targets = ["backend.utils.privacy.find_env_file", "backend.tools.file_safety.find_env_file",
               "backend.storage.settings.find_env_file", "backend.utils.env.find_env_file",
               "backend.utils.tracing.find_env_file", "backend.utils.checkpoints.find_env_file"]
    if not live:
        targets += ["backend.tools.cloud_ocr.find_env_file", "backend.agents.models.find_env_file"]
    with patch.dict(os.environ, env, clear=True), contextlib.ExitStack() as stack:
        for target in targets:
            stack.enter_context(patch(target, return_value=None))
        yield


# ---------- OCR (real PaddleOCR / PDF text), saved so later runs can reuse it ----------

def read_pages(entry: dict[str, Any], fresh: bool) -> tuple[list[dict[str, Any]], float, str]:
    from backend.tools.ocr import read_document

    path = DATA / entry["path"]
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    cached = OCR_CACHE / f"{file_sha(path)}.json"
    if cached.exists() and not fresh:
        saved = load_json(cached)
        return saved["pages"], saved["seconds"], "saved"
    started = time.time()
    pages = read_document(path, entry["mime_type"])
    seconds = round(time.time() - started, 1)
    cached.write_text(json.dumps({"path": entry["path"], "seconds": seconds, "pages": pages}, ensure_ascii=False),
                      encoding="utf-8")
    return pages, seconds, "fresh"


def section_ocr(manifest: list[dict], fresh: bool, ocr: dict[str, list]) -> Section:
    import make_hard_dataset as hard

    section = Section("ocr_reading")
    by: dict[str, Counter] = defaultdict(Counter)
    sources = Counter()
    seconds = []
    for number, entry in enumerate(manifest, start=1):
        try:
            pages, took, source = read_pages(entry, fresh)
        except Exception as exc:
            section.check(False, f"{entry['path']}: OCR failed ({type(exc).__name__})")
            ocr[entry["path"]] = []
            continue
        ocr[entry["path"]] = pages
        sources[source] += 1
        if source == "fresh" and entry["mime_type"] == "image/png":
            seconds.append(took)
        lines = [squash(line) for page in pages for line in page["text"].splitlines()]
        read = 0
        for key, truth in entry["fields"].items():
            ok = any(re.search(hard.read_pattern(str(truth["shown"])), line) for line in lines)
            read += ok
            section.check(ok, f"{entry['path']}: {key} not read ('{truth['shown']}')")
            for group in (entry["layout"], entry["condition"], entry["type"]):
                by[group]["read"] += ok
                by[group]["total"] += 1
        say(f"  OCR {number:2d}/{len(manifest)} {entry['path']:60s} {source:5s} {took:5.1f}s "
            f"read {read}/{len(entry['fields'])}")
    section.metrics = {
        "ocr_source": dict(sources),
        "seconds_per_image_page_this_run": round(sum(seconds) / len(seconds), 1) if seconds else None,
        "read_percent_by_group": {group: round(100 * c["read"] / c["total"], 1) for group, c in sorted(by.items())},
    }
    return section


# ---------- documents through the graph ----------

def graph_state(entry: dict[str, Any], *, claimed: bool = True, decision: dict | None = None,
                filename: str | None = None, **extra) -> dict[str, Any]:
    state = {
        "actor_id": OWNER,
        "document_id": f"doc-{Path(entry['path']).stem}",
        "document": {"owner_id": OWNER, "owner_name": entry["person"], "filename": filename or Path(entry["path"]).name,
                     "mime_type": entry["mime_type"], "file_path": str(DATA / entry["path"]), "as_of": AS_OF},
        **extra,
    }
    if claimed:
        state["claimed_type"] = entry["type"]
    if decision:
        state["review_decision"] = decision
    return state


def run_graph(state: dict[str, Any], pages: list[dict[str, Any]]) -> dict[str, Any]:
    from backend.graphs.document_graph import build_document_graph

    with patch("backend.graphs.document_graph.read_document", return_value=[dict(p) for p in pages]):
        return build_document_graph().invoke(state)


def vault_value(result: dict[str, Any], key: str) -> Any:
    """What a confirmed run stored for a field (ID numbers are masked there)."""
    return result.get("vault_record", {}).get("confirmed_fields", {}).get(key, {}).get("value")


def field_right(result: dict[str, Any], key: str, truth: Any, confirmed: bool) -> bool:
    import make_hard_dataset as hard
    from backend.utils.privacy import ID_FIELDS, keyed_hash

    if confirmed:
        if key in ID_FIELDS:
            digest = result.get("vault_record", {}).get("identifier_hashes", {}).get(key)
            return digest == keyed_hash(key, str(truth), SUITE_HASH_KEY)
        return hard.same_value(key, vault_value(result, key), truth)
    return hard.same_value(key, result.get("normalized_fields", {}).get(key), truth)


def owner_edits(entry: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """What the owner types to correct the proposal: each wrong field as printed on the paper
    (in normal letter case when the paper prints it in capitals). Only fields the review step
    lets them change (read ones, or the type's required ones)."""
    from backend.graphs.document_graph import REQUIRED_FIELDS

    allowed = set(result.get("extracted_fields", {})) | REQUIRED_FIELDS[entry["type"]]
    edits = {}
    for key, truth in entry["fields"].items():
        if key in allowed and not field_right(result, key, truth["value"], confirmed=False):
            edits[key] = typed_value(truth)
    return edits


def typed_value(truth: dict[str, Any]) -> Any:
    value, shown = truth["value"], str(truth["shown"])
    if isinstance(value, list):
        return value
    return value if isinstance(value, str) and value.lower() == shown.lower() else shown


def section_documents(manifest: list[dict], ocr: dict[str, list], runs: dict[str, dict]) -> list[Section]:
    before, after = Section("documents_before_review"), Section("documents_after_review")
    safety, classify = Section("auto_accept_safety"), Section("classification")
    outcomes: Counter = Counter()
    groups: dict[str, Counter] = defaultdict(Counter)
    edits_needed, rows_right, rows_total = [], 0, 0
    for entry in manifest:
        pages = ocr.get(entry["path"])
        if not pages:
            for key in entry["fields"]:
                before.check(False, f"{entry['path']}: {key} (no OCR output)")
                after.check(False, f"{entry['path']}: {key} (no OCR output)")
            continue
        first = run_graph(graph_state(entry), pages)
        status = first.get("status")
        outcomes[status] += 1
        confirmed = status == "confirmed"
        runs[entry["path"]] = {"first": first}
        for key, truth in entry["fields"].items():
            ok = status in ("confirmed", "needs_review") and field_right(first, key, truth["value"], confirmed)
            before.check(ok, f"{entry['path']}: {key} wrong before review (status {status})")
            for group in (entry["layout"], entry["condition"], entry["type"]):
                groups[group]["right"] += ok
                groups[group]["total"] += 1
            if confirmed and key in first.get("vault_record", {}).get("confirmed_fields", {}):
                safety.check(ok, f"{entry['path']}: {key} confirmed WITHOUT review but wrong")
            if isinstance(truth["value"], list):
                import make_hard_dataset as hard
                got = (vault_value(first, key) if confirmed else first.get("normalized_fields", {}).get(key)) or []
                columns = hard.LIST_COMPARE[key]
                rows_total += len(truth["value"])
                rows_right += sum(any(all(g.get(c) == w.get(c) for c in columns) for g in got) for w in truth["value"])
        # The owner reviews: confirms, correcting what is wrong.
        if status == "needs_review":
            edits = owner_edits(entry, first)
            edits_needed.append(len(edits))
            decision = {"action": "confirm", "reviewer_id": OWNER, **({"edits": edits} if edits else {})}
            second = run_graph(graph_state(entry, decision=decision), pages)
        else:
            second = first
        runs[entry["path"]]["final"] = second
        final_ok = second.get("status") == "confirmed"
        for key, truth in entry["fields"].items():
            after.check(final_ok and field_right(second, key, truth["value"], True),
                        f"{entry['path']}: {key} not right in the vault after review "
                        f"(final status {second.get('status')}{'; ' + '; '.join(second.get('errors', [])) if second.get('errors') else ''})")
        # Classification with no hint (neutral file name, no claimed type, no LLM).
        guess = run_graph(graph_state(entry, claimed=False, filename="upload" + Path(entry["path"]).suffix), pages)
        classify.check(guess.get("document_type") == entry["type"],
                       f"{entry['path']}: type {guess.get('document_type') or guess.get('status')} instead of {entry['type']}")
        say(f"  graph {entry['path']:60s} {status:15s} -> {second.get('status')}")
    before.metrics = {"first_outcome": dict(outcomes),
                      "right_percent_by_group": {g: round(100 * c["right"] / c["total"], 1) for g, c in sorted(groups.items())},
                      "medical_rows_right": f"{rows_right}/{rows_total}"}
    after.metrics = {"pages_reviewed": len(edits_needed),
                     "average_fields_the_owner_had_to_correct": round(sum(edits_needed) / len(edits_needed), 2)
                     if edits_needed else 0,
                     "note": "the owner can only add fields the type requires; an optional field OCR missed "
                             "(e.g. the Aadhaar on an identity card) can't be added and counts as not right"}
    if not safety.total:
        safety.metrics["note"] = "no page was confirmed without review"
    return [before, after, safety, classify]


# ---------- privacy ----------

def section_privacy(manifest: list[dict], ocr: dict[str, list], runs: dict[str, dict]) -> Section:
    from backend.agents import llm_agents
    from backend.utils.privacy import keyed_hash, mask_identifier, mask_ids_in_text, redact
    from backend.utils.tracing import safe_view

    section = Section("privacy")
    gaps = Counter()
    for case in load_json(DATA / "privacy" / "cases.json"):
        if case.get("kind") == "redact":
            out, _ = redact(case["text"], case.get("known"))
        else:
            out = mask_ids_in_text(case["text"])
        for value in case["hide"]:
            hidden = not leaks(out, [value])
            if case.get("documented_gap"):
                gaps["documented_gap_cases"] += 1
                gaps["documented_gap_still_open"] += not hidden
            section.check(hidden, f"{case['id']}: '{value}' was not hidden"
                          + (" (documented gap: unlabelled account numbers)" if case.get("documented_gap") else ""))
        for value in case["keep"]:
            section.check(value in out, f"{case['id']}: '{value}' was changed but is not an ID")
    # Masked forms keep only the last 4 characters.
    for entry in manifest:
        for key in ("aadhaar_number", "pan_number", "account_number", "policy_number"):
            if key in entry["fields"]:
                value = str(entry["fields"][key]["value"])
                masked = mask_identifier(key, value)
                section.check(masked != value and masked.endswith(value[-4:]) and not leaks(masked, [value]),
                              f"mask_identifier({key}) shows too much")
                break
    section.check(keyed_hash("aadhaar_number", "1", "k" * 32) == keyed_hash("aadhaar_number", "1", "k" * 32)
                  and keyed_hash("aadhaar_number", "1", "k" * 32) != keyed_hash("aadhaar_number", "1", "j" * 32)
                  and keyed_hash("aadhaar_number", "1", "k" * 32) != keyed_hash("pan_number", "1", "k" * 32),
                  "keyed_hash is not stable, key-dependent and field-dependent")
    # Final states, traces and model prompts built from the real OCR text.
    prompts: list[str] = []

    class Recorder:
        def __init__(self, schema):
            self.schema = schema

        def invoke(self, prompt):
            prompts.append(prompt)  # an empty answer: only the prompt matters here
            return self.schema.model_construct(**{name: "" for name in self.schema.model_fields})

    near = 0
    with patch.object(llm_agents, "structured_model", lambda schema, method=None: Recorder(schema)):
        for entry in manifest:
            ids = truth_ids(entry)
            run = runs.get(entry["path"], {})
            final = run.get("final")
            if final is not None and ids:
                section.check(not leaks(json.dumps(final, default=str, ensure_ascii=False), ids),
                              f"{entry['path']}: full ID number left in the final state ({final.get('status')})")
            first = run.get("first")
            if first is not None and ids:
                section.check(not leaks(json.dumps(safe_view(first), default=str, ensure_ascii=False), ids),
                              f"{entry['path']}: full ID number in what tracing would send")
            text = "\n".join(page["text"] for page in ocr.get(entry["path"], []))
            if not text:
                continue
            prompts.clear()
            known = {"NAME": entry["person"]}
            from backend.graphs.document_graph import REQUIRED_FIELDS
            for call in (lambda: llm_agents.extract_missing_fields(text, entry["type"],
                                                                   sorted(REQUIRED_FIELDS[entry["type"]]), known),
                         lambda: llm_agents.choose_document_type(text, known)):
                try:
                    call()
                except Exception as exc:  # the empty stand-in answer; the prompt is already recorded
                    gaps[f"stand_in_answer_errors ({type(exc).__name__})"] += 1
            sent = "\n".join(prompts)
            section.check(not leaks(sent, ids), f"{entry['path']}: full ID number in a model prompt")
            section.check(entry["person"] not in sent, f"{entry['path']}: the owner's name in a model prompt")
            for value in ids:  # a misread ID (one digit off) is no longer redacted but is still nearly the number
                digits = re.sub(r"\D", "", value)
                for candidate in re.findall(r"\d[\d -]{8,}\d", sent):
                    got = re.sub(r"\D", "", candidate)
                    if len(got) == len(digits) and got != digits and sum(a == b for a, b in zip(got, digits)) >= len(digits) - 2:
                        near += 1
    section.metrics = {"near_miss_ids_in_prompts": near,
                       "near_miss_meaning": "misread ID numbers (at most 2 digits wrong) that reached a prompt unredacted",
                       **gaps}
    return section


def section_cloud_blackout(manifest: list[dict], ocr: dict[str, list], runs: dict[str, dict]) -> Section:
    """Paint the ID regions black exactly as the cloud/vision path does, then read the picture
    again with the real OCR: can the ID number still be read?"""
    from PIL import Image

    from backend.tools.ocr import read_document
    from backend.tools.ocr_postprocess import clean_page
    from backend.tools.vision_extract import safe_page_images
    from backend.utils.privacy import ID_FIELDS

    section = Section("cloud_blackout")
    partial = 0
    for entry in manifest:
        ids = truth_ids(entry)
        pages = ocr.get(entry["path"])
        if not ids or not pages or all(page.get("engine") == "pdf_text" for page in pages):
            continue
        local = [clean_page(page) for page in pages]
        extracted = runs.get(entry["path"], {}).get("first", {}).get("extracted_fields", {})
        boxes: dict[int, list] = {}
        for key in ID_FIELDS:
            if extracted.get(key, {}).get("box"):
                boxes.setdefault(extracted[key]["page"], []).append(extracted[key]["box"])
        try:
            images = safe_page_images(str(DATA / entry["path"]), entry["mime_type"], local, boxes)
        except Exception as exc:
            section.check(False, f"{entry['path']}: blackout failed ({type(exc).__name__})")
            continue
        text = ""
        with tempfile.TemporaryDirectory() as folder:
            for index, image in enumerate(images):
                path = Path(folder) / f"sent_{index}.png"
                Image.fromarray(image).save(path)
                text += "\n".join(page["text"] for page in read_document(path, "image/png"))
        digits_lines = [re.sub(r"\D", "", line) for line in text.splitlines()]
        for value in ids:
            digits = re.sub(r"\D", "", value)
            visible = bool(digits) and any(digits in line for line in digits_lines) or squash(value) in squash(text)
            section.check(not visible, f"{entry['path']}: '{value}' still readable after blackout")
            if digits and not visible and any(digits[-8:] in line for line in digits_lines):
                partial += 1
        say(f"  blackout {entry['path']}")
    section.metrics = {"ids_with_last_8_digits_still_readable": partial}
    return section


# ---------- file safety, manual entry, rules ----------

def section_file_safety() -> Section:
    from backend.tools.file_safety import check_file

    section = Section("file_safety")
    for case in load_json(DATA / "file_safety" / "manifest.json"):
        try:
            result = check_file(DATA / case["file"], case["mime_type"])
            verdict = "refuse" if result.problems or result.reupload else "accept"
        except Exception as exc:
            verdict = f"crash ({type(exc).__name__})"
        section.check(verdict == case["expect"], f"{case['file']}: {verdict}, expected {case['expect']}")
    section.metrics["virus_scan"] = ("not tested: needs a harmless EICAR test file, which antivirus treats as a "
                                     "virus; waiting for the owner's OK")
    return section


def section_manual_entry() -> Section:
    from backend.graphs.document_graph import build_document_graph
    from backend.utils.privacy import keyed_hash

    section = Section("manual_entry")
    for case in load_json(DATA / "manual_entry" / "cases.json"):
        state = {"actor_id": OWNER, "document_id": f"manual-{case['id']}", "claimed_type": case["type"],
                 "manual_fields": case["fields"], "document": {"owner_id": OWNER, "as_of": AS_OF}}
        try:
            result = build_document_graph().invoke(state)
        except Exception as exc:
            section.check(False, f"{case['id']}: crashed ({type(exc).__name__})")
            continue
        status = result.get("status")
        if not case["accept"]:
            errors = " | ".join(result.get("errors", []))
            section.check(status == "rejected" and all(w.lower() in errors.lower() for w in case["expect"]),
                          f"{case['id']}: expected refusal mentioning {case['expect']}, got {status}: {errors}")
            continue
        if not section.check(status == "confirmed", f"{case['id']}: expected accepted, got {status}: "
                                                    f"{'; '.join(result.get('errors', []))}"):
            continue
        stored = result["vault_record"]["confirmed_fields"]
        for key, want in case["expect"].items():
            got = stored.get(key, {}).get("value")
            if want == "MASKED":
                raw = re.sub(r"[\s-]", "", str(case["fields"][key])).upper()
                digest = result["vault_record"]["identifier_hashes"].get(key)
                ok = digest == keyed_hash(key, raw, SUITE_HASH_KEY) and not leaks(json.dumps(result, default=str), [raw])
            elif isinstance(want, list):
                ok = isinstance(got, list) and len(got) == len(want) and all(
                    all(g.get(k) == v for k, v in w.items()) for g, w in zip(got, want))
            else:
                ok = got == want
            section.check(ok, f"{case['id']}: {key} stored as {got!r}, expected {want!r}")
    return section


def section_benefits_rules() -> Section:
    from backend.graphs.benefits_graph import build_benefits_graph
    from backend.tools.document_rules import match_proofs

    section = Section("benefits_rules")
    for case in load_json(DATA / "benefits" / "cases.json"):
        state = {k: v for k, v in case["state"].items() if v is not None}
        try:
            status = build_benefits_graph().invoke(state).get("status")
        except Exception as exc:
            status = f"crash ({type(exc).__name__})"
        section.check(status in case["expect"], f"benefits {case['id']}: {status}, expected {case['expect']}")
    for case in load_json(DATA / "proofs" / "cases.json"):
        got = [[m["status"], m["document_id"]] for m in match_proofs(case["proofs"], case["documents"], case["owner"], AS_OF)]
        section.check(got == case["expect"], f"proofs {case['id']}: {got}, expected {case['expect']}")
    return section


def section_benefits_pipeline(manifest: list[dict], ocr: dict[str, list], runs: dict[str, dict]) -> Section:
    """Each applicant's three page photos -> document graph -> vault records -> benefits graph."""
    import make_complete_dataset as made
    from backend.graphs.benefits_graph import build_benefits_graph

    section = Section("benefits_pipeline")
    detail = {}
    for pack, info in made.PACKS.items():
        for condition in made.SEEN_CONDITIONS:
            if not any(e.get("pack") == pack and e["condition"] == condition for e in manifest):
                continue  # not in this run (--quick)
            for stage in ("as_proposed", "after_review"):
                documents = []
                for entry in manifest:
                    if entry.get("pack") != pack or entry["condition"] != condition:
                        continue
                    run = runs.get(entry["path"], {})
                    if stage == "as_proposed":
                        result = run.get("first", {})
                        if result.get("status") == "needs_review":  # the owner confirms without changing anything
                            result = run_graph(graph_state(entry, decision={"action": "confirm", "reviewer_id": OWNER}),
                                               ocr.get(entry["path"], []))
                    else:
                        result = run.get("final", {})
                    if result.get("vault_record"):
                        documents.append(result["vault_record"])
                state = {"actor_id": OWNER, "case_id": f"{pack}-{condition}", "case_version": 1,
                         "expected_case_version": 1, "scheme_id": "demo_senior_support", "jurisdiction": "MH",
                         "as_of": AS_OF, "profile_facts": {}, "documents": documents,
                         "review_decision": {"action": "approve", "reviewer_id": OWNER}}
                status = build_benefits_graph().invoke(state).get("status")
                key = f"{pack}/{condition}/{stage}"
                detail[key] = {"status": status, "expected": info["expected"], "documents_in_vault": len(documents)}
                section.check(status == info["expected"], f"{key}: {status} (expected {info['expected']}, "
                                                          f"{len(documents)}/3 documents reached the vault)")
    section.metrics = {"applicants": detail}
    return section


# ---------- database + API ----------

def section_storage_api(manifest: list[dict], ocr: dict[str, list], db_url: str) -> Section:
    section = Section("storage_api")
    if not db_url:
        return section.skip("TEST_DATABASE_URL is not set")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import text as sql

    import test_storage_db as dbt
    from backend.api import documents
    from backend.errors import register_global_error_handlers
    from backend.storage.db import make_engine, make_sessions
    from backend.storage.files import EncryptedFileStore
    from backend.storage.models import Base
    from backend.storage.service import DocumentService
    from backend.storage.settings import with_driver
    from backend.utils.checkpoints import close_checkpointer, make_postgres_checkpointer
    from sqlalchemy.engine import make_url

    if not (make_url(db_url).database or "").endswith("_test"):
        return section.skip("TEST_DATABASE_URL must name a database ending in _test (this part empties it)")
    engine = make_engine(db_url)
    dbt.drop_everything(engine)
    dbt.migrate(engine)
    root = Path(tempfile.mkdtemp())
    checkpointer = make_postgres_checkpointer(with_driver(db_url, ""), key=bytes(range(32)))
    service = DocumentService(make_sessions(engine), EncryptedFileStore(root, bytes(range(2, 34))), checkpointer)
    app = FastAPI()
    register_global_error_handlers(app)
    app.include_router(documents.router)
    app.dependency_overrides[documents.get_document_service] = lambda: service
    client = TestClient(app, raise_server_exceptions=False)
    logs = io.StringIO()
    handler = logging.StreamHandler(logs)
    logging.getLogger().addHandler(handler)
    owner, stranger = {"X-Actor-Id": OWNER}, {"X-Actor-Id": OTHER}

    def entry_for(path_part: str) -> dict[str, Any]:
        entry = next(e for e in load_json(DATA / "documents" / "manifest.json") if e["path"].endswith(path_part))
        if not ocr.get(entry["path"]):  # not in this run (--quick)
            ocr[entry["path"]] = read_pages(entry, False)[0]
        return entry

    def upload(entry, url="/api/v1/documents", **query):
        params = {"filename": Path(entry["path"]).name, "claimedType": entry["type"], **query}
        with patch("backend.graphs.document_graph.read_document", return_value=ocr[entry["path"]]):
            return client.post(url, params=params, content=(DATA / entry["path"]).read_bytes(),
                               headers={**owner, "Content-Type": entry["mime_type"]})

    def review_until_confirmed(doc_id, entry):
        pending = client.get(f"/api/v1/documents/{doc_id}/review", headers=owner).json()["data"]
        edits = {}
        for key, truth in entry["fields"].items():
            got = pending["candidates"].get(key)
            import make_hard_dataset as hard
            if not hard.same_value(key, got, truth["value"]) and (key in pending["candidates"] or key in pending["missingFields"]):
                edits[key] = typed_value(truth)
        with patch("backend.graphs.document_graph.read_document", return_value=ocr[entry["path"]]):
            return client.post(f"/api/v1/documents/{doc_id}/review", headers=owner,
                               json={"action": "confirm", "edits": edits})

    try:
        ident = entry_for("unseen/identity__clean.png")
        ident_photo = entry_for("unseen/identity__phone_photo.png")
        aadhaar = ident["fields"]["aadhaar_number"]["shown"]
        ids = [aadhaar]
        r = upload(ident)
        section.check(r.status_code == 201, f"upload: HTTP {r.status_code}")
        data = r.json()["data"]
        doc_id = data["documentId"]
        section.check(not leaks(r.text, ids), "upload answer shows the full Aadhaar")
        if data["status"] == "needs_review":
            section.check(client.get(f"/api/v1/documents/{doc_id}/review", headers=stranger).status_code == 404,
                          "someone else can open the review")
            r = review_until_confirmed(doc_id, ident)
        got = client.get(f"/api/v1/documents/{doc_id}", headers=owner).json()["data"]
        section.check(got["status"] == "confirmed", f"identity not confirmed after review: {got['status']}")
        section.check(got["fields"].get("name", {}).get("value") == ident["fields"]["name"]["value"],
                      "vault name differs from the paper")
        section.check(got["fields"].get("date_of_birth", {}).get("value") == ident["fields"]["date_of_birth"]["value"],
                      "vault date of birth differs from the paper")
        masked = got["fields"].get("aadhaar_number", {}).get("value", "")
        section.check(masked.endswith(aadhaar[-4:]) and not leaks(masked, ids), f"vault Aadhaar not masked: {masked!r}")
        section.check(client.get(f"/api/v1/documents/{doc_id}", headers=stranger).status_code == 404,
                      "someone else can read the document")
        # Renew with the phone photo of the same card.
        r = upload(ident_photo, url=f"/api/v1/documents/{doc_id}/versions")
        section.check(r.status_code == 201, f"renewal upload: HTTP {r.status_code}")
        if r.status_code == 201 and r.json()["data"].get("replacement"):
            r = review_until_confirmed(doc_id, ident_photo)
        got = client.get(f"/api/v1/documents/{doc_id}", headers=owner).json()["data"]
        section.check(got.get("version") == 2 and not got.get("replacement"), f"renewal not in use: version {got.get('version')}")
        section.check(not any("already in the vault" in w for w in got.get("warnings", [])),
                      "renewed card reported as a duplicate of itself")
        # A risky file is refused and not kept.
        risky = {"path": "file_safety/pdf_with_javascript.pdf", "mime_type": "application/pdf", "type": "income_certificate"}
        r = client.post("/api/v1/documents", params={"filename": "x.pdf", "claimedType": "income_certificate"},
                        content=(DATA / risky["path"]).read_bytes(), headers={**owner, "Content-Type": "application/pdf"})
        risky_data = r.json().get("data", {})
        section.check(r.status_code == 201 and risky_data.get("status") == "rejected" and not risky_data.get("fileStored"),
                      f"risky PDF: HTTP {r.status_code}, status {risky_data.get('status')}")
        section.check(client.post("/api/v1/documents", params={"filename": "a.txt"}, content=b"hello",
                                  headers={**owner, "Content-Type": "text/plain"}).status_code == 422,
                      "a text file was not refused with 422")
        # Typed in.
        typed = {"name": "Kavita Sharma", "date_of_birth": "14/08/1952", "aadhaar_number": aadhaar}
        r = client.post("/api/v1/documents/manual", headers=owner, json={"documentType": "identity", "fields": typed})
        section.check(r.status_code == 201 and r.json()["data"]["entry"] == "manual", f"manual entry: HTTP {r.status_code}")
        section.check(r.status_code == 201 and any("already in the vault" in w for w in r.json()["data"].get("warnings", [])),
                      "typing in a card already in the vault was not reported")
        manual_id = r.json()["data"]["documentId"] if r.status_code == 201 else None
        bad = aadhaar[:-1] + str((int(aadhaar[-1]) + 1) % 10)  # one digit changed: the checksum must catch it
        r = client.post("/api/v1/documents/manual", headers=owner,
                        json={"documentType": "identity", "fields": {**typed, "aadhaar_number": bad}})
        section.check(r.status_code == 422, f"mistyped Aadhaar accepted: HTTP {r.status_code}")
        section.check(not leaks(r.text, [bad]), "the mistyped Aadhaar is repeated back in full")
        # Nothing stored holds a full ID number.
        with engine.connect() as connection:
            stored = "\n".join(json.dumps([*row], default=str) for table in Base.metadata.sorted_tables
                               for row in connection.execute(table.select()))
            checkpoints = connection.execute(sql("SELECT string_agg(encode(convert_to(coalesce(checkpoint::text,''),'UTF8'),'escape'),'') FROM checkpoints")).scalar() or ""
        section.check(not leaks(stored, ids), "a full Aadhaar is stored in a vault table")
        section.check(not leaks(checkpoints, ids), "a full Aadhaar is readable in the checkpoints")
        files_blob = b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())
        section.check(not any(form.encode() in files_blob for value in ids for form in id_forms(value)),
                      "a full Aadhaar is readable in the stored files")
        section.check(not leaks(logs.getvalue(), ids), "a full Aadhaar was logged")
        # Delete everything.
        listed = client.get("/api/v1/documents", headers=owner).json()["data"]
        section.check(len(listed) >= 2, f"list shows {len(listed)} documents")
        for item in listed:
            client.delete(f"/api/v1/documents/{item['documentId']}", headers=owner)
        section.check(client.get(f"/api/v1/documents/{doc_id}", headers=owner).status_code == 404,
                      "a deleted document can still be read")
        section.check(client.get("/api/v1/documents", headers=owner).json()["data"] == [], "documents left after deleting all")
        section.check(not any(p.is_file() for p in root.rglob("*")), "stored files left after deleting all")
        with engine.connect() as connection:
            runs_left = connection.execute(sql("SELECT count(*) FROM workflow_runs")).scalar_one()
        section.check(runs_left == 0, f"{runs_left} workflow runs left after deleting all")
        section.metrics["manual_document_created"] = bool(manual_id)
    except Exception as exc:
        section.check(False, f"crashed: {type(exc).__name__}: {exc}")
        section.metrics["traceback"] = traceback.format_exc().splitlines()[-6:]
    finally:
        logging.getLogger().removeHandler(handler)
        close_checkpointer(checkpointer)
        dbt.drop_everything(engine)
        dbt.migrate(engine)
        engine.dispose()
        shutil.rmtree(root, ignore_errors=True)
    return section


def section_main_api(manifest: list[dict], ocr: dict[str, list]) -> Section:
    from fastapi.testclient import TestClient

    section = Section("main_api")
    from backend.main import app

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/v1/health")
    section.check(r.status_code == 200 and r.json().get("data", {}).get("serviceStatus") == "UP", f"health: {r.status_code}")
    r = client.get("/api/v1/demo/scenarios")
    scenarios = r.json().get("data", []) if r.status_code == 200 else []
    section.check(bool(scenarios), f"demo scenarios: HTTP {r.status_code}")
    for scenario in scenarios:
        r = client.post(f"/api/v1/demo/scenarios/{scenario['scenarioId']}/execute")
        status = (r.json().get("data") or {}).get("status") if r.status_code == 200 else f"HTTP {r.status_code}"
        section.check(status == scenario["expectedStatus"],
                      f"demo {scenario['scenarioId']}: {status}, the app says it should be {scenario['expectedStatus']}")
    r = client.post("/api/v1/demo/scenarios/no-such-scenario/execute")
    section.check(r.status_code == 404 and r.json().get("status") == "ERROR", f"unknown demo scenario: HTTP {r.status_code}")
    r = client.post("/api/v1/workflows/benefits/run", json={"state": {}})
    section.check(r.status_code == 200 and r.json()["data"].get("status") == "blocked", f"empty benefits state: {r.status_code}")
    r = client.post("/api/v1/workflows/document-intake/run", json={})
    section.check(r.status_code == 422 and r.json().get("status") == "ERROR" and "error" in r.json(),
                  f"bad request body: HTTP {r.status_code}, not the error format")
    # The old open endpoint, given a real page reading (open item 3, left to teammates).
    entry = next(e for e in manifest if e["path"].endswith("unseen/identity__clean.png"))
    pages = ocr.get(entry["path"])
    if pages:
        state = graph_state(entry)
        state["document"].pop("file_path")
        state["document"]["pages"] = pages
        state["document"]["size_bytes"] = (DATA / entry["path"]).stat().st_size
        r = client.post("/api/v1/workflows/document-intake/run", json={"state": state})
        section.check(r.status_code == 200, f"document-intake run: HTTP {r.status_code}")
        section.check(not leaks(r.text, truth_ids(entry)),
                      "old /api/v1/workflows/document-intake/run returns the full Aadhaar (open item 3, left to teammates)")
    return section


# ---------- unit tests ----------

def section_unit_tests() -> Section:
    section = Section("unit_tests")
    process = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(TESTS), "-p", "test_*.py"],
                             cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = process.stderr[-4000:]
    ran = int(re.search(r"Ran (\d+) tests?", tail).group(1)) if re.search(r"Ran (\d+) tests?", tail) else 0
    counts = {kind: int(m.group(1)) if (m := re.search(rf"{kind}=(\d+)", tail.splitlines()[-1] if tail else "")) else 0
              for kind in ("failures", "errors", "skipped")}
    failed_names = re.findall(r"^(?:FAIL|ERROR): (.+)$", process.stderr, re.MULTILINE)
    run_count = ran - counts["skipped"]
    section.passed = run_count - counts["failures"] - counts["errors"]
    section.total = run_count
    section.failures = failed_names
    section.metrics = {"ran": ran, **counts}
    return section


# ---------- live (Groq) ----------

def section_live(manifest: list[dict], ocr: dict[str, list]) -> list[Section]:
    from backend.tools.cloud_ocr import engine_key

    vision, classify = Section("live_vision"), Section("live_llm_classification")
    if not engine_key("groq"):
        return [vision.skip("GROQ_API_KEY is not set"), classify.skip("GROQ_API_KEY is not set")]
    from backend.agents.llm_agents import choose_document_type

    for entry in manifest:
        if entry["type"] in ("lab_report", "prescription", "medical_bill") and entry["mime_type"] == "image/png":
            result = run_graph(graph_state(entry, cloud_ocr_consent=True, medical_cloud_consent=True), ocr[entry["path"]])
            used = any(event.startswith("vision_extract:found") for event in result.get("events", []))
            for key, truth in entry["fields"].items():
                vision.check(used and field_right(result, key, truth["value"], result.get("status") == "confirmed"),
                             f"{entry['path']}: {key} wrong with the vision model"
                             + ("" if used else f" (vision not used: {result.get('warnings')})"))
        if entry["layout"] in ("unseen", "text_pdf", "scanned_pdf"):
            text = "\n".join(page["text"] for page in ocr.get(entry["path"], []))
            try:
                got = choose_document_type(text, {"NAME": entry["person"]}).document_type
            except Exception as exc:
                got = f"error {type(exc).__name__}"
            classify.check(got == entry["type"], f"{entry['path']}: LLM said {got}")
    return [vision, classify]


# ---------- report ----------

def write_report(sections: list[Section], args: argparse.Namespace, started: float) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results = {s.name: s.result() for s in sections}
    scored = [r["score"] for r in results.values() if r["score"] is not None]
    critical = []
    for name in ("auto_accept_safety", "privacy", "cloud_blackout", "file_safety"):
        for failure in results.get(name, {}).get("failures", []):
            critical.append(f"[{name}] {failure}")
    summary = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "mode": {"quick": args.quick, "live": args.live, "fresh_ocr": args.fresh_ocr, "db": not args.no_db,
                 "unit_tests": not args.no_unit},
        "dataset": load_json(DATA / "dataset_summary.json"),
        "overall_score": round(sum(scored) / len(scored), 1) if scored else None,
        "overall_meaning": "plain average of the parts that ran; each part's score is explained in 'meaning'",
        "scores": {name: r["score"] for name, r in results.items()},
        "not_run": {name: r["reason"] for name, r in results.items() if r["status"] == "not_run"},
        "critical_problems": critical,
        "not_covered": ["ci/ scripts, Dockerfile, deployment/ and frontend/ (not part of the backend code)",
                        "virus scanning with a real antivirus (needs an EICAR test file and the owner's OK)",
                        "Google Cloud Vision (no key used)"],
        "minutes": round((time.time() - started) / 60, 1),
    }
    full = {"summary": summary, "sections": results}
    json_path = RESULTS / f"complete_{stamp}.json"
    json_path.write_text(json.dumps(full, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    lines = [f"# Complete-project suite — {summary['run_at']}", "",
             f"**Overall score: {summary['overall_score']}** (average of the parts that ran)", "",
             "| Part | Score | Passed / total | What the score counts |", "|---|---|---|---|"]
    for name, r in results.items():
        score = "not run — " + r["reason"] if r["status"] == "not_run" else f"{r['score']}"
        lines.append(f"| {name} | {score} | {r['passed']} / {r['total']} | {r['meaning']} |")
    lines += ["", "## Critical problems", ""] + ([f"- {c}" for c in critical] or ["- none"])
    for name, r in results.items():
        if r["metrics"] or r["failures"]:
            lines += ["", f"## {name}", ""]
            for key, value in r["metrics"].items():
                lines.append(f"- {key}: {json.dumps(value, ensure_ascii=False, default=str)}")
            if r["failures"]:
                lines.append(f"- failures ({len(r['failures'])}):")
                lines += [f"  - {f}" for f in r["failures"][:80]]
                if len(r["failures"]) > 80:
                    lines.append(f"  - ... {len(r['failures']) - 80} more in the JSON file")
    lines += ["", "## Not covered", ""] + [f"- {item}" for item in summary["not_covered"]]
    md_path = RESULTS / f"complete_{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.copyfile(json_path, RESULTS / "latest.json")
    shutil.copyfile(md_path, RESULTS / "latest.md")
    return md_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("--quick", "--fresh-ocr", "--no-db", "--no-unit", "--live"):
        parser.add_argument(flag, action="store_true")
    args = parser.parse_args()
    started = time.time()
    if not (DATA / "documents" / "manifest.json").exists():
        import make_complete_dataset
        say("Making the dataset...")
        make_complete_dataset.generate()
    from backend.storage.settings import TEST_DATABASE_URL, setting
    db_url = "" if args.no_db else setting(TEST_DATABASE_URL)  # read before .env is shut off; never printed
    manifest = load_json(DATA / "documents" / "manifest.json")
    if args.quick:
        manifest = [e for e in manifest if e["condition"] in ("scan", "clean", "digital")]
    sections: list[Section] = []

    def run(step: str, function: Callable[[], Any]) -> None:
        say(f"\n== {step} ==")
        try:
            began = time.time()
            made = function()
            made = made if isinstance(made, list) else [made]
            for section in made:  # parts made by one step share its time
                section.seconds = time.time() - began
            sections.extend(made)
        except Exception as exc:
            broken = Section(step)
            broken.check(False, f"the part crashed: {type(exc).__name__}: {exc}")
            broken.metrics["traceback"] = traceback.format_exc().splitlines()[-8:]
            sections.append(broken)
            say(f"  crashed: {type(exc).__name__}: {exc}")

    if not args.no_unit:
        run("unit_tests", section_unit_tests)  # before the isolated environment: tests set up their own
    ocr: dict[str, list] = {}
    runs: dict[str, dict] = {}
    with isolated_environment(args.live):
        run("ocr_reading", lambda: section_ocr(manifest, args.fresh_ocr, ocr))
        run("documents", lambda: section_documents(manifest, ocr, runs))
        run("privacy", lambda: section_privacy(manifest, ocr, runs))
        if args.quick:
            sections.append(Section("cloud_blackout").skip("--quick"))
        else:
            run("cloud_blackout", lambda: section_cloud_blackout(manifest, ocr, runs))
        run("file_safety", section_file_safety)
        run("manual_entry", section_manual_entry)
        run("benefits_rules", section_benefits_rules)
        run("benefits_pipeline", lambda: section_benefits_pipeline(manifest, ocr, runs))
        if args.no_db:
            sections.append(Section("storage_api").skip("--no-db"))
        else:
            run("storage_api", lambda: section_storage_api(manifest, ocr, db_url))
        run("main_api", lambda: section_main_api(manifest, ocr))
        if args.live:
            run("live", lambda: section_live(manifest, ocr))
        else:
            sections += [Section("live_vision").skip("needs --live (sends the fake pages to Groq)"),
                         Section("live_llm_classification").skip("needs --live (sends the fake page text to Groq)")]
    if args.no_unit:
        sections.insert(0, Section("unit_tests").skip("--no-unit"))
    report = write_report(sections, args, started)
    say(f"\nResults: {report}")
    say((RESULTS / "latest.md").read_text(encoding="utf-8").split("## Critical problems")[0])


if __name__ == "__main__":
    main()
