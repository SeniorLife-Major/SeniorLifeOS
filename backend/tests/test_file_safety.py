"""Tests for the upload checks (tools/file_safety.py) and the check_file graph node.

Risky PDFs are built by hand here, so no real malware is used. The virus scanners are
replaced by stand-ins: a fake MpCmdRun result, and a small local ClamAV-like server.
"""

import json
import os
import socket
import struct
import subprocess
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from backend.graphs.document_graph import build_document_graph
from backend.tools import file_safety
from backend.tools.file_safety import check_file
from test_graph_ocr import AADHAAR, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

EICAR_LIKE = b"STAND-IN-VIRUS-SIGNATURE"  # what the fake ClamAV server treats as a virus


def make_pdf(catalog_extra=b"", extra_objects=(), trailer_extra=b"", media_box=b"[0 0 595 842]"):
    """A small valid one-page PDF. catalog_extra goes into the catalog dictionary;
    extra_objects are raw bodies of objects 4, 5, ...; trailer_extra into the trailer."""
    bodies = [b"<< /Type /Catalog /Pages 2 0 R " + catalog_extra + b">>",
              b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
              b"<< /Type /Page /Parent 2 0 R /MediaBox " + media_box + b" >>",
              *extra_objects]
    out, offsets = bytearray(b"%PDF-1.7\n"), []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(bodies) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R " % (len(bodies) + 1) + trailer_extra + b">>\nstartxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def stream(data, compress=True):
    body = zlib.compress(data) if compress else data
    flate = b"/Filter /FlateDecode " if compress else b""
    return b"<< " + flate + b"/Length %d >>\nstream\n" % len(body) + body + b"\nendstream"


class TempFiles(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        patcher = patch("backend.tools.file_safety.find_env_file", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {})
        env.start()
        self.addCleanup(env.stop)
        for name in (file_safety.SCANNER_SETTING, file_safety.REQUIRED_SETTING, file_safety.CLAMD_SETTING):
            os.environ.pop(name, None)

    def write(self, name, data):
        path = self.folder / name
        path.write_bytes(data)
        return path


class FileTypeTests(TempFiles):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def test_good_files_pass(self):
        for name, mime in (("digital.pdf", "application/pdf"), ("scanned_two_pages.pdf", "application/pdf"),
                           ("sharp.png", "image/png"), ("phone_rotated.jpg", "image/jpeg")):
            with self.subTest(name=name):
                result = check_file(UNIT / name, mime)
                self.assertEqual((result.problems, result.reupload), ([], []))
                self.assertEqual(len(result.sha256), 64)

    def test_contents_must_match_the_claimed_type(self):
        png = (UNIT / "sharp.png").read_bytes()
        cases = [("program.jpg", b"MZ\x90\x00" + b"\x00" * 60, "image/jpeg"),   # a Windows program
                 ("photo.pdf", png, "application/pdf"),
                 ("doc.png", make_pdf(), "image/png"),
                 ("script.png", b"<script>alert(1)</script>", "image/png")]
        for name, data, mime in cases:
            with self.subTest(name=name):
                result = check_file(self.write(name, data), mime)
                self.assertEqual(result.problems, ["The file's contents don't match its type"])
                self.assertEqual(result.scan, "not_run")  # nothing else is done with such a file

    def test_checksum_is_of_the_bytes(self):
        import hashlib
        path = self.write("a.pdf", make_pdf())
        self.assertEqual(check_file(path, "application/pdf").sha256, hashlib.sha256(path.read_bytes()).hexdigest())


class RiskyPdfTests(TempFiles):
    def problems(self, data):
        result = check_file(self.write("x.pdf", data), "application/pdf")
        return result.problems

    def test_a_plain_pdf_passes(self):
        self.assertEqual(self.problems(make_pdf()), [])

    def test_javascript_when_opened(self):
        pdf = make_pdf(b"/OpenAction 4 0 R", [b"<< /S /JavaScript /JS (app.alert(1)) >>"])
        self.assertIn("The PDF contains JavaScript", self.problems(pdf))

    def test_javascript_with_a_disguised_name(self):
        pdf = make_pdf(b"/OpenAction 4 0 R", [b"<< /S /J#61vaScript /J#53 (app.alert(1)) >>"])
        self.assertIn("The PDF contains JavaScript", self.problems(pdf))

    def test_javascript_hidden_in_a_compressed_stream(self):
        hidden = stream(b"4 0 << /S /JavaScript /JS (app.alert(1)) >>")
        self.assertIn("The PDF contains JavaScript", self.problems(make_pdf(b"", [hidden])))

    def test_document_level_javascript_counted_by_pdfium(self):
        pdf = make_pdf(b"/Names << /JavaScript 4 0 R >>",
                       [b"<< /Names [(start) 5 0 R] >>", b"<< /S /JavaScript /JS (app.alert(1)) >>"])
        with patch.object(file_safety, "RISKY_PDF_NAMES", {}):  # only pdfium's own count is left
            self.assertEqual(self.problems(pdf), ["The PDF contains JavaScript"])

    def test_launch_attachments_media_and_forms(self):
        cases = {
            b"<< /S /Launch /F (cmd.exe) >>": "The PDF can start another program",
            b"<< /Type /EmbeddedFile >>": "The PDF has files attached inside it",
            b"<< /Type /RichMedia >>": "The PDF contains embedded media",
            b"<< /XFA 5 0 R >>": "The PDF has a script form (XFA)",
            b"<< /S /SubmitForm /F (http://example.invalid) >>": "The PDF can send data to a website",
        }
        for body, problem in cases.items():
            with self.subTest(problem=problem):
                self.assertIn(problem, self.problems(make_pdf(b"", [body])))

    def test_password_protected_pdf(self):
        pdf = make_pdf(b"", [b"<< /Filter /Standard /V 2 /R 3 /O <00> /U <00> /P -4 >>"], b"/Encrypt 4 0 R ")
        self.assertEqual(self.problems(pdf),
                         ["The PDF is password-protected or encrypted; please remove the password and upload it again"])

    def test_page_too_large_to_render_safely(self):
        self.assertEqual(self.problems(make_pdf(media_box=b"[0 0 3000 3000]")),
                         ["A PDF page is too large to open safely"])
        self.assertEqual(self.problems(make_pdf(media_box=b"[0 0 1191 1684]")), [])  # A2 is fine

    def test_compressed_stream_bomb_is_capped(self):
        bomb = stream(b"\0" * 80_000_000)  # 80 MB of zeros, about 80 KB compressed
        with patch.object(file_safety, "MAX_INFLATED_BYTES", 1_000_000):
            names = file_safety.pdf_names(make_pdf(b"", [bomb]))
        self.assertIn(b"/Catalog", names)

    def test_broken_and_overlong_pdfs_ask_for_a_new_upload(self):
        broken = check_file(self.write("b.pdf", b"%PDF-1.4 broken"), "application/pdf")
        self.assertEqual((broken.problems, broken.reupload), ([], ["The PDF file is damaged"]))
        ensure_fixtures()
        long = check_file(UNIT / "too_many_pages.pdf", "application/pdf")
        self.assertEqual((long.problems, long.reupload), ([], ["The PDF has more than 20 pages"]))


class ImageBombTests(TempFiles):
    def test_huge_images_are_refused_before_decoding(self):
        for width, height in ((10_000, 7_000), (30_000, 30_000)):  # over our cap; over Pillow's own cap
            with self.subTest(size=(width, height)):
                path = self.folder / "big.png"
                Image.new("1", (width, height)).save(path)
                with patch.object(Image.Image, "load", side_effect=AssertionError("pixels decoded")):
                    self.assertEqual(check_file(path, "image/png").problems,
                                     ["The image is too large to open safely"])

    def test_a_large_phone_photo_passes(self):
        path = self.folder / "photo.jpg"
        Image.new("RGB", (8160, 6120), "white").save(path, quality=30)  # 50 MP
        self.assertEqual(check_file(path, "image/jpeg").problems, [])

    def test_damaged_image_asks_for_a_new_upload(self):
        result = check_file(self.write("cut.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20), "image/png")
        self.assertEqual((result.problems, result.reupload), ([], ["The image file is damaged"]))


class FakeClamd:
    """A local server speaking ClamAV's INSTREAM protocol. A file holding EICAR_LIKE is a virus."""

    def __init__(self, answer=None):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.address = "127.0.0.1:%d" % self.server.getsockname()[1]
        self.answer, self.received = answer, b""
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        conn, _ = self.server.accept()
        with conn:
            command = conn.recv(10)
            assert command == b"zINSTREAM\0", command
            while True:
                size = struct.unpack("!L", self.read(conn, 4))[0]
                if not size:
                    break
                self.received += self.read(conn, size)
            answer = self.answer or (b"stream: Stand-In.Virus FOUND\0" if EICAR_LIKE in self.received
                                     else b"stream: OK\0")
            conn.sendall(answer)
        self.server.close()

    @staticmethod
    def read(conn, size):
        data = b""
        while len(data) < size:
            data += conn.recv(size - len(data))
        return data


class VirusScanTests(TempFiles):
    def pdf(self, payload=b""):
        return self.write("x.pdf", make_pdf(b"", [stream(payload, compress=False)] if payload else []))

    def test_no_scanner_set_is_a_warning(self):
        result = check_file(self.pdf(), "application/pdf")
        self.assertEqual(result.scan, "skipped")
        self.assertEqual(result.warnings,
                         ["Virus scan skipped (no scanner set); file type and structure were checked"])
        self.assertFalse(result.scan_unavailable_refuses)

    def test_required_scan_without_a_scanner_refuses(self):
        os.environ[file_safety.REQUIRED_SETTING] = "true"
        for scanner in ("none", "antivirus-x"):  # unknown names never count as clean
            with self.subTest(scanner=scanner):
                os.environ[file_safety.SCANNER_SETTING] = scanner
                result = check_file(self.pdf(), "application/pdf")
                self.assertTrue(result.scan_unavailable_refuses)
                self.assertEqual(result.warnings, [])

    def test_clamav_clean_and_infected(self):
        os.environ[file_safety.SCANNER_SETTING] = "clamav"
        for payload, scan in ((b"hello", "clean"), (EICAR_LIKE, "infected")):
            with self.subTest(scan=scan):
                server = FakeClamd()
                os.environ[file_safety.CLAMD_SETTING] = server.address
                path = self.pdf(payload)
                result = check_file(path, "application/pdf")
                self.assertEqual(result.scan, scan)
                self.assertEqual(server.received, path.read_bytes())  # the whole file was sent
                self.assertEqual("The virus scanner flagged this file" in result.problems, scan == "infected")

    def test_clamav_unreachable_or_odd_answer_is_never_clean(self):
        os.environ[file_safety.SCANNER_SETTING] = "clamav"
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        for address in (f"127.0.0.1:{port}", "no-port", ""):
            with self.subTest(address=address):
                os.environ[file_safety.CLAMD_SETTING] = address
                self.assertEqual(check_file(self.pdf(), "application/pdf").scan, "unavailable")
        server = FakeClamd(answer=b"INSTREAM size limit exceeded. ERROR\0")
        os.environ[file_safety.CLAMD_SETTING] = server.address
        self.assertEqual(check_file(self.pdf(), "application/pdf").scan, "unavailable")

    def test_defender_results_and_command(self):
        os.environ[file_safety.SCANNER_SETTING] = "defender"
        for code, scan in ((0, "clean"), (2, "infected"), (5, "unavailable")):
            with self.subTest(code=code), patch.object(Path, "is_file", return_value=True), \
                    patch("backend.tools.file_safety.subprocess.run",
                          return_value=subprocess.CompletedProcess([], code)) as run:
                path = self.pdf()
                self.assertEqual(check_file(path, "application/pdf").scan, scan)
                command = run.call_args.args[0]
                self.assertIn("-DisableRemediation", command)  # never deletes or moves the upload
                self.assertEqual(command[command.index("-File") + 1], str(path.resolve()))

    def test_defender_missing_or_hanging_is_unavailable(self):
        os.environ[file_safety.SCANNER_SETTING] = "defender"
        with patch.object(file_safety, "DEFENDER", self.folder / "missing.exe"):
            self.assertEqual(check_file(self.pdf(), "application/pdf").scan, "unavailable")
        with patch.object(Path, "is_file", return_value=True), \
                patch("backend.tools.file_safety.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertEqual(check_file(self.pdf(), "application/pdf").scan, "unavailable")


@patch("backend.utils.privacy.find_env_file", return_value=None)
class GraphCheckFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_upload(self, path, mime, env=None):
        with hash_key_env(FAKE_HASH_KEY), patch.dict(os.environ, env or {}), \
                patch("backend.graphs.document_graph.read_document") as reader:
            result = build_document_graph().invoke(upload_state(Path(path), mime, "identity"))
        return result, reader

    def test_refused_file_never_reaches_ocr(self, _):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "card.pdf"
            path.write_bytes(make_pdf(b"/OpenAction 4 0 R",
                                      [b"<< /S /JavaScript /JS (app.alert('%s')) >>" % AADHAAR.encode()]))
            result, reader = self.run_upload(path, "application/pdf")
        reader.assert_not_called()
        self.assertEqual(result["status"], "rejected")
        self.assertIn("The PDF contains JavaScript", result["errors"])
        self.assertIn("check_file:refused", result["events"])
        self.assertEqual(len(result["document"]["sha256"]), 64)

    def test_required_scan_that_cannot_run_fails_closed(self, _):
        result, reader = self.run_upload(UNIT / "sharp.png", "image/png",
                                         {file_safety.REQUIRED_SETTING: "true", file_safety.SCANNER_SETTING: "none"})
        reader.assert_not_called()
        self.assertEqual(result["status"], "failed_tool")
        self.assertIn("check_file:scan_unavailable", result["events"])

    def test_clean_file_goes_on_with_its_checksum_and_scan_result(self, _):
        server = FakeClamd()
        result, reader = self.run_upload(UNIT / "sharp.png", "image/png",
                                         {file_safety.SCANNER_SETTING: "clamav", file_safety.CLAMD_SETTING: server.address})
        reader.assert_called_once()
        self.assertIn("check_file:clean", result["events"])
        self.assertEqual(result["document"]["virus_scan"], "clean")

    def test_page_fixtures_skip_the_file_checks(self, _):
        from backend.demo.demo_cases import document_success_state
        with hash_key_env(FAKE_HASH_KEY):
            result = build_document_graph().invoke(document_success_state())
        self.assertIn("check_file:fixture", result["events"])
        self.assertNotIn("sha256", json.dumps(result["document"]))


if __name__ == "__main__":
    unittest.main()
