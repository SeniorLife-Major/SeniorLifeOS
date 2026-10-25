"""Checks on an uploaded file before anything reads it (plan step D1: "malware/file-type/size").

1. **Real type:** the first bytes must match the claimed type (a ".jpg" that is really
   an .exe is refused).
2. **Risky PDFs:** JavaScript, launch actions, attached files, rich media, forms that run
   scripts, and password protection are refused. Names are decoded (/J#61vaScript) and
   compressed streams are opened (with a size cap) before searching, and pdfium's own
   counts of document JavaScript and attachments are checked too.
3. **Size bombs:** images and PDF pages too large to open safely are refused before
   any pixels are decoded.
4. **Virus scan** behind one function, set by SENIORLIFE_VIRUS_SCANNER:
   - "none" (default): skipped, with a warning;
   - "defender": Windows Defender's MpCmdRun, without removing anything;
   - "clamav": a ClamAV daemon at SENIORLIFE_CLAMD (host:port), over its INSTREAM protocol.
   SENIORLIFE_SCAN_REQUIRED=true refuses uploads when no scan could be made (production).

Not sure: Windows Defender may send file details to Microsoft's cloud protection,
depending on the machine's settings; ClamAV keeps everything on the server.
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
import struct
import subprocess
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..utils.env import find_env_file, load_env_file

SIGNATURES = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}
MAX_IMAGE_PIXELS = 60_000_000  # a 50 MP phone photo fits; a 5 MB "bomb" opening to gigapixels does not
MAX_PDF_PAGES = 20  # same as tools/ocr.py
MAX_PDF_RENDER_PIXELS = 40_000_000  # one page at 300 DPI; A4 is 8.7 M, A3 17.4 M
PDF_RENDER_DPI = 300
MAX_INFLATED_BYTES = 50_000_000  # total opened from compressed PDF streams while searching
RISKY_PDF_NAMES = {
    b"/JavaScript": "contains JavaScript",
    b"/JS": "contains JavaScript",
    b"/Launch": "can start another program",
    b"/EmbeddedFile": "has files attached inside it",
    b"/EmbeddedFiles": "has files attached inside it",
    b"/RichMedia": "contains embedded media",
    b"/XFA": "has a script form (XFA)",
    b"/SubmitForm": "can send data to a website",
    b"/ImportData": "can load data from another file",
}
SCANNER_SETTING = "SENIORLIFE_VIRUS_SCANNER"
REQUIRED_SETTING = "SENIORLIFE_SCAN_REQUIRED"
CLAMD_SETTING = "SENIORLIFE_CLAMD"
DEFENDER = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender" / "MpCmdRun.exe"
SCAN_TIMEOUT_SECONDS = 120
CLAMD_CHUNK = 64 * 1024

NAME = re.compile(rb"/[A-Za-z0-9#]+")
HEX_IN_NAME = re.compile(rb"#([0-9A-Fa-f]{2})")
STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


@dataclass
class FileCheck:
    """What the checks found. problems refuse the file; reupload asks for a new upload
    (a broken or overlong file is a mistake, not a danger); warnings don't stop anything."""
    sha256: str = ""
    problems: list[str] = field(default_factory=list)
    reupload: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scan: str = "not_run"  # clean, infected, skipped, unavailable
    scan_unavailable_refuses: bool = False


def setting(name: str, default: str = "") -> str:
    load_env_file(find_env_file())
    return os.environ.get(name, "").strip() or default


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def type_matches(head: bytes, mime_type: str) -> bool:
    return any(head.startswith(signature) for signature in SIGNATURES.get(mime_type, ()))


def image_problems(path: Path) -> tuple[list[str], list[str]]:
    """(problems, reupload). Reads only the image header: the size is known before any
    pixel is decoded."""
    try:
        with Image.open(path) as image:
            width, height = image.size
    except Image.DecompressionBombError:
        return ["The image is too large to open safely"], []
    except Exception:  # Pillow raises several error types for broken files
        return [], ["The image file is damaged"]
    if width * height > MAX_IMAGE_PIXELS:
        return ["The image is too large to open safely"], []
    return [], []


def pdf_names(data: bytes) -> set[bytes]:
    """Every /Name in the file, with #xx escapes decoded, including names inside compressed
    streams (object streams can hide them). Stops opening streams after MAX_INFLATED_BYTES."""
    chunks, budget = [data], MAX_INFLATED_BYTES
    for match in STREAM.finditer(data):
        if budget <= 0:
            break
        try:
            inflater = zlib.decompressobj()
            opened = inflater.decompress(match.group(1), budget)
        except zlib.error:
            continue
        budget -= len(opened)
        chunks.append(opened)
    names = set()
    for chunk in chunks:
        for name in NAME.findall(chunk):
            names.add(HEX_IN_NAME.sub(lambda m: bytes([int(m.group(1), 16)]), name))
    return names


def pdf_problems(path: Path) -> tuple[list[str], list[str]]:
    """(problems, reupload)."""
    data = path.read_bytes()
    names = pdf_names(data)
    if b"/Encrypt" in names:
        return ["The PDF is password-protected or encrypted; please remove the password and upload it again"], []
    problems = list(dict.fromkeys(f"The PDF {why}" for name, why in RISKY_PDF_NAMES.items() if name in names))
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception:  # pdfium raises PdfiumError for broken or locked files
        return problems, ["The PDF file is damaged"]
    try:
        if raw.FPDFDoc_GetJavaScriptActionCount(pdf.raw) > 0:
            problems.append("The PDF contains JavaScript")
        if raw.FPDFDoc_GetAttachmentCount(pdf.raw) > 0:
            problems.append("The PDF has files attached inside it")
        reupload = []
        if len(pdf) == 0:
            reupload.append("The PDF has no pages")
        if len(pdf) > MAX_PDF_PAGES:
            reupload.append(f"The PDF has more than {MAX_PDF_PAGES} pages")
        scale = PDF_RENDER_DPI / 72
        for index in range(min(len(pdf), MAX_PDF_PAGES)):
            width, height = pdf.get_page_size(index)
            if width * height * scale * scale > MAX_PDF_RENDER_PIXELS:
                problems.append("A PDF page is too large to open safely")
                break
    finally:
        pdf.close()
    return list(dict.fromkeys(problems)), reupload


def scan_with_defender(path: Path) -> str:
    if not DEFENDER.is_file():
        return "unavailable"
    try:
        done = subprocess.run([str(DEFENDER), "-Scan", "-ScanType", "3", "-File", str(path.resolve()),
                               "-DisableRemediation"], capture_output=True, timeout=SCAN_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return {0: "clean", 2: "infected"}.get(done.returncode, "unavailable")


def scan_with_clamav(path: Path, address: str) -> str:
    """ClamAV's INSTREAM: the file is sent in size-prefixed chunks; the answer ends in OK or FOUND."""
    host, _, port = address.rpartition(":")
    if not host or not port.isdigit():
        return "unavailable"
    try:
        with socket.create_connection((host, int(port)), timeout=SCAN_TIMEOUT_SECONDS) as conn:
            conn.sendall(b"zINSTREAM\0")
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(CLAMD_CHUNK), b""):
                    conn.sendall(struct.pack("!L", len(block)) + block)
            conn.sendall(struct.pack("!L", 0))
            answer = b""
            while not answer.endswith(b"\0"):
                part = conn.recv(4096)
                if not part:
                    break
                answer += part
    except OSError:
        return "unavailable"
    answer = answer.rstrip(b"\0").strip()
    if answer.endswith(b"FOUND"):
        return "infected"
    return "clean" if answer.endswith(b"OK") else "unavailable"


def virus_scan(path: Path) -> str:
    scanner = setting(SCANNER_SETTING, "none").lower()
    if scanner == "none":
        return "skipped"
    if scanner == "defender":
        return scan_with_defender(path)
    if scanner == "clamav":
        return scan_with_clamav(path, setting(CLAMD_SETTING))
    return "unavailable"  # an unknown name never means "clean"


def check_file(path: str | Path, mime_type: str) -> FileCheck:
    """Run every check on an uploaded file. Nothing here decodes pixels or renders pages."""
    path = Path(path)
    result = FileCheck(sha256=sha256_of(path))
    with open(path, "rb") as handle:
        head = handle.read(16)
    if not type_matches(head, mime_type):
        result.problems.append("The file's contents don't match its type")
        return result  # nothing else is meaningful, and the file is not opened further
    problems, reupload = pdf_problems(path) if mime_type == "application/pdf" else image_problems(path)
    result.problems.extend(problems)
    result.reupload.extend(reupload)
    result.scan = virus_scan(path)
    required = setting(REQUIRED_SETTING).lower() in {"1", "true", "yes"}
    if result.scan == "infected":
        result.problems.append("The virus scanner flagged this file")
    elif result.scan in {"skipped", "unavailable"}:
        if required:
            result.scan_unavailable_refuses = True
        else:
            result.warnings.append("Virus scan " + ("skipped (no scanner set)" if result.scan == "skipped"
                                                     else "unavailable") + "; file type and structure were checked")
    return result
