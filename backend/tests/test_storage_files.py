"""Tests: encrypted file store, storage settings and file names (no database needed)."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.storage.files import EncryptedFileStore, StoredFileError
from backend.storage.service import safe_filename
from backend.storage.settings import (
    STORAGE_KEY,
    StorageSettingError,
    database_url,
    setting,
    storage_dir,
    storage_key,
    with_driver,
)
from test_privacy import fake_aadhaar

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
SECRET_TEXT = b"Name: Meera Rao, Aadhaar 2341 2341 2346, a page only the owner should read"


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = EncryptedFileStore(self.root, KEY)

    def blob_path(self, storage_key):
        return self.root / storage_key[:2] / storage_key

    def test_round_trip(self):
        stored = self.store.save(SECRET_TEXT)
        self.assertEqual(self.store.read(stored.storage_key), SECRET_TEXT)
        self.assertEqual(stored.size_bytes, len(SECRET_TEXT))
        self.assertEqual(len(stored.sha256), 64)

    def test_what_is_on_disk_is_encrypted(self):
        stored = self.store.save(SECRET_TEXT)
        on_disk = self.blob_path(stored.storage_key).read_bytes()
        self.assertNotIn(b"Meera", on_disk)
        self.assertNotIn(b"2341", on_disk)
        self.assertEqual(list(self.root.rglob("*.part")), [])  # no half-written leftovers

    def test_same_file_twice_gets_new_names_and_new_bytes(self):
        first, second = self.store.save(SECRET_TEXT), self.store.save(SECRET_TEXT)
        self.assertNotEqual(first.storage_key, second.storage_key)
        self.assertNotEqual(self.blob_path(first.storage_key).read_bytes(),
                            self.blob_path(second.storage_key).read_bytes())

    def test_another_key_cannot_read(self):
        stored = self.store.save(SECRET_TEXT)
        with self.assertRaises(StoredFileError):
            EncryptedFileStore(self.root, OTHER_KEY).read(stored.storage_key)

    def test_a_changed_byte_is_refused(self):
        stored = self.store.save(SECRET_TEXT)
        path = self.blob_path(stored.storage_key)
        blob = bytearray(path.read_bytes())
        blob[-1] ^= 1
        path.write_bytes(bytes(blob))
        with self.assertRaises(StoredFileError):
            self.store.read(stored.storage_key)

    def test_a_file_moved_to_another_name_is_refused(self):
        first, second = self.store.save(SECRET_TEXT), self.store.save(b"another document")
        shutil.copy(self.blob_path(first.storage_key), self.blob_path(second.storage_key))
        with self.assertRaises(StoredFileError):
            self.store.read(second.storage_key)

    def test_names_that_are_not_storage_names_are_refused(self):
        for name in ("../../etc/passwd", "..", "A" * 32, "abc", "0" * 31 + "/", ""):
            with self.subTest(name=name), self.assertRaises(StoredFileError):
                self.store.read(name)

    def test_missing_file(self):
        stored = self.store.save(SECRET_TEXT)
        self.store.delete(stored.storage_key)
        self.assertFalse(self.store.exists(stored.storage_key))
        with self.assertRaises(StoredFileError):
            self.store.read(stored.storage_key)

    def test_plain_copy_only_lives_inside_the_block(self):
        stored = self.store.save(SECRET_TEXT)
        with self.store.decrypted_copy(stored.storage_key, ".png") as path:
            self.assertEqual(path.read_bytes(), SECRET_TEXT)
            self.assertEqual(path.suffix, ".png")
            folder = path.parent
        self.assertFalse(folder.exists())
        with self.assertRaises(RuntimeError):
            with self.store.decrypted_copy(stored.storage_key) as path:
                folder = path.parent
                raise RuntimeError("OCR crashed")
        self.assertFalse(folder.exists())

    def test_key_must_be_32_bytes(self):
        with self.assertRaises(ValueError):
            EncryptedFileStore(self.root, b"short")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.env_file = folder / ".env"
        clean = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", STORAGE_KEY, "SENIORLIFE_STORAGE_DIR",
                                                                     "SOME_OTHER_SECRET")}
        patcher = patch.dict(os.environ, clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_env_file(self, text):
        self.env_file.write_text(text, encoding="utf-8")
        patcher = patch("backend.storage.settings.find_env_file", return_value=self.env_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reading_one_setting_leaves_the_rest_of_env_alone(self):
        self.use_env_file(f"{STORAGE_KEY}={'ab' * 32}\nSOME_OTHER_SECRET=do-not-load\n")
        self.assertEqual(storage_key(), bytes.fromhex("ab" * 32))
        self.assertNotIn("SOME_OTHER_SECRET", os.environ)
        self.assertNotIn(STORAGE_KEY, os.environ)

    def test_the_environment_wins_over_the_file(self):
        self.use_env_file("SENIORLIFE_STORAGE_DIR=/from/file\n")
        with patch.dict(os.environ, {"SENIORLIFE_STORAGE_DIR": "/from/env"}):
            self.assertEqual(setting("SENIORLIFE_STORAGE_DIR"), "/from/env")
        self.assertEqual(storage_dir(), Path("/from/file"))

    def test_missing_or_malformed_key_never_shows_the_value(self):
        self.use_env_file("")
        with self.assertRaisesRegex(StorageSettingError, "is not set"):
            storage_key()
        bad = "zz-not-hex-but-secret-zz"
        self.use_env_file(f"{STORAGE_KEY}={bad}\n")
        with self.assertRaises(StorageSettingError) as caught:
            storage_key()
        self.assertNotIn(bad, str(caught.exception))
        self.assertIn("64 hex", str(caught.exception))

    def test_database_url_checks_and_never_shows_the_password(self):
        self.use_env_file("")
        with self.assertRaisesRegex(StorageSettingError, "DATABASE_URL is not set"):
            database_url()
        self.use_env_file("DATABASE_URL=mysql://user:pw-secret@localhost/x\n")
        with self.assertRaises(StorageSettingError) as caught:
            database_url()
        self.assertNotIn("pw-secret", str(caught.exception))
        self.use_env_file("DATABASE_URL=postgresql+asyncpg://user:pw-secret@localhost:5432/seniorlife_os\n")
        url = database_url()
        self.assertEqual(with_driver(url, "psycopg"), "postgresql+psycopg://user:pw-secret@localhost:5432/seniorlife_os")
        self.assertEqual(with_driver(url, ""), "postgresql://user:pw-secret@localhost:5432/seniorlife_os")

    def test_default_storage_folder_is_git_ignored(self):
        self.use_env_file("")
        folder = storage_dir()
        repo = Path(__file__).resolve().parents[2]
        self.assertEqual(folder, repo / "var" / "documents")
        self.assertIn("var/", (repo / ".gitignore").read_text(encoding="utf-8").split())


class FilenameTests(unittest.TestCase):
    def test_only_the_last_part_without_control_characters(self):
        self.assertEqual(safe_filename("C:\\fakepath\\card.png"), "card.png")
        self.assertEqual(safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(safe_filename("scan\x00\n.pdf"), "scan.pdf")
        self.assertEqual(safe_filename(""), "upload")
        self.assertEqual(safe_filename("../"), "upload")
        self.assertEqual(len(safe_filename("a" * 400 + ".png")), 255)

    def test_id_numbers_in_the_name_are_masked(self):
        number = fake_aadhaar(5, separator="")
        name = safe_filename(f"aadhaar_{number}.png")
        self.assertNotIn(number, name)
        self.assertIn(number[-4:], name)
        self.assertEqual(safe_filename("pan_HAVPC1814K.pdf"), "pan_XXXXXX814K.pdf")


if __name__ == "__main__":
    unittest.main()
