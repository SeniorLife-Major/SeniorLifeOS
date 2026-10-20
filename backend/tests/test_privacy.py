"""Tests for identifier validation and redaction. All identifiers are fake."""

import json
import os
import random
import unittest
from unittest.mock import MagicMock, patch

from backend.demo.demo_cases import document_success_state
from backend.graphs.document_graph import build_document_graph
from backend.tools.demo_tools import normalize_field
from backend.agents.llm_agents import DocumentTypeChoice, SchemeChoice, choose_document_type, choose_scheme
from backend.utils.privacy import (
    is_valid_aadhaar,
    is_valid_ifsc,
    is_valid_pan,
    keyed_hash,
    load_hash_key,
    mask_identifier,
    redact,
    verhoeff_check_digit,
)


FAKE_HASH_KEY = "fake-test-hash-key-0123456789abcdef"


def fake_aadhaar(seed: int, separator: str = " ") -> str:
    """Generate a fake 12-digit number that passes the Aadhaar format and checksum."""
    rng = random.Random(seed)
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    digits = body + verhoeff_check_digit(body)
    return separator.join([digits[0:4], digits[4:8], digits[8:12]])


def break_checksum(number: str) -> str:
    """Change the last digit. Verhoeff catches every single-digit change."""
    return number[:-1] + str((int(number[-1]) + 1) % 10)


class ValidatorTests(unittest.TestCase):
    def test_verhoeff_known_answers(self):
        # Published Verhoeff examples: 236 -> 3, 12345 -> 1.
        self.assertEqual(verhoeff_check_digit("236"), "3")
        self.assertEqual(verhoeff_check_digit("12345"), "1")

    def test_generated_fake_aadhaar_is_valid(self):
        for seed in range(20):
            number = fake_aadhaar(seed)
            self.assertTrue(is_valid_aadhaar(number), number)
            self.assertTrue(is_valid_aadhaar(number.replace(" ", "")))
            self.assertTrue(is_valid_aadhaar(number.replace(" ", "-")))

    def test_wrong_checksum_or_start_digit_is_invalid(self):
        number = fake_aadhaar(1)
        self.assertFalse(is_valid_aadhaar(break_checksum(number)))
        self.assertFalse(is_valid_aadhaar("1" + number[1:]))
        self.assertFalse(is_valid_aadhaar(number[:-1]))

    def test_pan_format(self):
        self.assertTrue(is_valid_pan("ABCPE1234F"))
        self.assertFalse(is_valid_pan("ABCXE1234F"))  # X is not a holder type
        self.assertFalse(is_valid_pan("ABCPE12345"))
        self.assertFalse(is_valid_pan("abcpe1234f"))

    def test_ifsc_format(self):
        self.assertTrue(is_valid_ifsc("SBIN0001234"))
        self.assertTrue(is_valid_ifsc("HDFC0ABC123"))
        self.assertFalse(is_valid_ifsc("SBIN1001234"))  # fifth character must be 0
        self.assertFalse(is_valid_ifsc("SBIN000123"))


class StorageProtectionTests(unittest.TestCase):
    def test_masking_keeps_only_last_four(self):
        aadhaar = fake_aadhaar(5, separator="")
        self.assertEqual(mask_identifier("aadhaar_number", aadhaar), f"XXXX XXXX {aadhaar[-4:]}")
        self.assertEqual(mask_identifier("pan_number", "ABCPE1234F"), "XXXXXX234F")
        self.assertEqual(mask_identifier("account_number", "123456789012345"), "XXXXXXXXXXX2345")

    def test_keyed_hash_is_stable_and_depends_on_key_and_field(self):
        aadhaar = fake_aadhaar(6, separator="")
        first = keyed_hash("aadhaar_number", aadhaar, FAKE_HASH_KEY)
        self.assertEqual(first, keyed_hash("aadhaar_number", aadhaar, FAKE_HASH_KEY))
        self.assertNotEqual(first, keyed_hash("aadhaar_number", aadhaar, FAKE_HASH_KEY + "x"))
        self.assertNotEqual(first, keyed_hash("account_number", aadhaar, FAKE_HASH_KEY))
        self.assertNotIn(aadhaar, first)
        self.assertEqual(len(first), 64)

    @patch("backend.utils.privacy.find_env_file", return_value=None)
    def test_hash_key_must_be_set_and_long_enough(self, _):
        env = {k: v for k, v in os.environ.items() if k != "SENIORLIFE_HASH_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SENIORLIFE_HASH_KEY"):
                load_hash_key()
        with patch.dict(os.environ, {**env, "SENIORLIFE_HASH_KEY": "too-short"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "at least 32"):
                load_hash_key()
        with patch.dict(os.environ, {**env, "SENIORLIFE_HASH_KEY": FAKE_HASH_KEY}, clear=True):
            self.assertEqual(load_hash_key(), FAKE_HASH_KEY)


def identity_state(aadhaar: str) -> dict:
    """An identity card upload that needs no human review when the Aadhaar is valid."""
    state = document_success_state()
    state["document_id"] = "doc-identity-aadhaar"
    state["claimed_type"] = "identity"
    state.pop("review_decision")
    state["simulate_ocr_failures"] = 0
    state["document"]["filename"] = "identity_card.jpg"
    state["document"]["pages"][0]["text"] = f"Name: Meera Rao\nDate of birth: 1958-05-15\nAadhaar: {aadhaar}"
    return state


class IdentifierExtractionTests(unittest.TestCase):
    def test_id_numbers_are_normalized_or_rejected(self):
        aadhaar = fake_aadhaar(8)
        self.assertEqual(normalize_field("aadhaar_number", aadhaar), aadhaar.replace(" ", ""))
        self.assertIsNone(normalize_field("aadhaar_number", break_checksum(aadhaar)))
        self.assertEqual(normalize_field("pan_number", " abcpe1234f "), "ABCPE1234F")
        self.assertIsNone(normalize_field("pan_number", "ABCXE1234F"))
        self.assertEqual(normalize_field("account_number", "1234 5678 9012"), "123456789012")
        self.assertIsNone(normalize_field("account_number", "12345"))

    def test_bad_aadhaar_checksum_is_sent_to_review(self):
        result = build_document_graph().invoke(identity_state(break_checksum(fake_aadhaar(9))))
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("aadhaar_number: failed format or checksum check", result["review_flags"])
        self.assertNotIn("vault_record", result)


def hash_key_env(key: str | None):
    """Environment with SENIORLIFE_HASH_KEY set to key (or removed), ignoring the real .env."""
    env = {k: v for k, v in os.environ.items() if k != "SENIORLIFE_HASH_KEY"}
    if key is not None:
        env["SENIORLIFE_HASH_KEY"] = key
    return patch.dict(os.environ, env, clear=True)


@patch("backend.utils.privacy.find_env_file", return_value=None)
class VaultProtectionTests(unittest.TestCase):
    def assert_nowhere(self, number: str, result: dict):
        dumped = json.dumps(result, default=str)
        self.assertNotIn(number, dumped)
        self.assertNotIn(number.replace(" ", ""), dumped)

    def test_vault_stores_masked_aadhaar_and_keyed_hash_only(self, _):
        aadhaar = fake_aadhaar(30)
        with hash_key_env(FAKE_HASH_KEY):
            result = build_document_graph().invoke(identity_state(aadhaar))
        self.assertEqual(result["status"], "confirmed")
        self.assertIn("protect_identifiers:protected", result["events"])
        stored = result["vault_record"]["confirmed_fields"]["aadhaar_number"]["value"]
        self.assertEqual(stored, f"XXXX XXXX {aadhaar[-4:]}")
        expected_hash = keyed_hash("aadhaar_number", aadhaar.replace(" ", ""), FAKE_HASH_KEY)
        self.assertEqual(result["vault_record"]["identifier_hashes"], {"aadhaar_number": expected_hash})
        self.assert_nowhere(aadhaar, result)

    def test_number_corrected_in_review_is_also_hidden(self, _):
        aadhaar = fake_aadhaar(31)
        state = identity_state(break_checksum(aadhaar))
        state["review_decision"] = {"action": "confirm", "reviewer_id": "senior-meera", "edits": {"aadhaar_number": aadhaar}}
        with hash_key_env(FAKE_HASH_KEY):
            result = build_document_graph().invoke(state)
        self.assertEqual(result["status"], "confirmed")
        field = result["vault_record"]["confirmed_fields"]["aadhaar_number"]
        self.assertTrue(field["corrected_by_user"])
        self.assertEqual(field["value"], f"XXXX XXXX {aadhaar[-4:]}")
        self.assert_nowhere(aadhaar, result)

    def test_missing_hash_key_blocks_publishing(self, _):
        with hash_key_env(None):
            result = build_document_graph().invoke(identity_state(fake_aadhaar(32)))
        self.assertEqual(result["status"], "failed_tool")
        self.assertIn("protect_identifiers:no_key", result["events"])
        self.assertIn("SENIORLIFE_HASH_KEY", result["errors"][0])
        self.assertNotIn("vault_record", result)

    def test_same_aadhaar_uploaded_again_is_flagged(self, _):
        aadhaar = fake_aadhaar(33)
        with hash_key_env(FAKE_HASH_KEY):
            first = build_document_graph().invoke(identity_state(aadhaar))["vault_record"]
            state = identity_state(aadhaar)
            state["document_id"] = "doc-identity-again"
            state["vault_identifier_hashes"] = {first["identifier_hashes"]["aadhaar_number"]: first["document_id"]}
            second = build_document_graph().invoke(state)
        self.assertEqual(second["status"], "confirmed")
        self.assertIn("aadhaar_number already in the vault (document doc-identity-aadhaar)", second["warnings"])

    def test_documents_without_id_numbers_need_no_key(self, _):
        state = document_success_state()
        with hash_key_env(None):
            result = build_document_graph().invoke(state)
        self.assertEqual(result["status"], "confirmed")
        self.assertIn("protect_identifiers:none", result["events"])


class RedactTests(unittest.TestCase):
    def test_hides_each_identifier_type(self):
        aadhaar = fake_aadhaar(7)
        text = (
            f"Name: Meera Rao\nAadhaar: {aadhaar}\nPAN: ABCPE1234F\n"
            "A/c No: 123456789012345\nIFSC: SBIN0001234\n"
            "Mobile: +91 9876543210\nEmail: meera.rao@example.com"
        )
        safe, mapping = redact(text, {"NAME": "Meera Rao"})
        for secret in (aadhaar, "ABCPE1234F", "123456789012345", "9876543210", "meera.rao@example.com", "Meera Rao"):
            self.assertNotIn(secret, safe)
        for token in ("[NAME_1]", "[AADHAAR_1]", "[PAN_1]", "[ACCOUNT_1]", "[PHONE_1]", "[EMAIL_1]"):
            self.assertIn(token, safe)
        self.assertIn("A/c No: [ACCOUNT_1]", safe)
        self.assertIn("SBIN0001234", safe)  # IFSC is a public branch code
        self.assertEqual(mapping["[AADHAAR_1]"], aadhaar)
        self.assertEqual(mapping["[NAME_1]"], "Meera Rao")

    def test_policy_number_after_label_is_hidden(self):
        safe, mapping = redact("Insurer: Demo Health\nPolicy No: HLT-2026/004512\nPlan: GOLDPLAN")
        self.assertIn("Policy No: [POLICY_1]", safe)
        self.assertEqual(mapping["[POLICY_1]"], "HLT-2026/004512")
        self.assertIn("Plan: GOLDPLAN", safe)

    def test_policy_word_without_a_number_is_kept(self):
        text = "Policy: GOLDPLAN\nThis policy covers 5 members"
        self.assertEqual(redact(text), (text, {}))

    def test_twelve_digits_with_bad_checksum_are_kept(self):
        not_aadhaar = break_checksum(fake_aadhaar(3))
        safe, mapping = redact(f"Receipt number: {not_aadhaar}")
        self.assertIn(not_aadhaar, safe)
        self.assertEqual(mapping, {})

    def test_same_value_gets_same_placeholder(self):
        first, second = fake_aadhaar(11), fake_aadhaar(12)
        safe, mapping = redact(f"{first} then {second} then {first}")
        self.assertEqual(safe, "[AADHAAR_1] then [AADHAAR_2] then [AADHAAR_1]")
        self.assertEqual(len(mapping), 2)

    def test_known_name_is_matched_case_insensitively(self):
        safe, _ = redact("MEERA RAO signed for meera rao", {"NAME": "Meera Rao"})
        self.assertEqual(safe, "[NAME_1] signed for [NAME_1]")

    def test_text_without_identifiers_is_unchanged(self):
        text = "Annual income: ₹1,20,000\nIssue date: 2026-06-01\nIssuer: Demo District Office"
        safe, mapping = redact(text, {"NAME": ""})
        self.assertEqual(safe, text)
        self.assertEqual(mapping, {})


def fake_model(answer):
    """A stand-in for the structured chat model that records every prompt it receives."""
    prompts = []
    agent = MagicMock()
    agent.invoke.side_effect = lambda prompt: prompts.append(prompt) or answer
    return agent, prompts


class LLMGuardrailTests(unittest.TestCase):
    def test_graph_sends_no_identifiers_to_the_llm(self):
        aadhaar = fake_aadhaar(21)
        state = document_success_state()
        state.pop("claimed_type")
        state["use_llm"] = True
        state["document"]["pages"][0]["text"] = f"Name: Meera Rao\nDate of birth: 1958-05-15\nAadhaar: {aadhaar}"
        model, prompts = fake_model(DocumentTypeChoice(document_type="identity", reason="Has an ID number"))
        with patch("backend.agents.llm_agents.structured_model", return_value=model):
            result = build_document_graph().invoke(state)
        self.assertIn("classify_document:identity", result["events"])
        self.assertEqual(len(prompts), 1)
        self.assertNotIn(aadhaar, prompts[0])
        self.assertNotIn(aadhaar.replace(" ", ""), prompts[0])
        self.assertNotIn("Meera Rao", prompts[0])
        self.assertIn("Aadhaar: [AADHAAR_1]", prompts[0])
        self.assertIn("Name: [NAME_1]", prompts[0])

    def test_identifier_in_llm_reason_is_hidden(self):
        aadhaar = fake_aadhaar(22)
        model, _ = fake_model(DocumentTypeChoice(document_type="identity", reason=f"Found number {aadhaar}"))
        with patch("backend.agents.llm_agents.structured_model", return_value=model):
            choice = choose_document_type("Some document text")
        self.assertNotIn(aadhaar, choice.reason)
        self.assertIn("[AADHAAR_1]", choice.reason)

    def test_scheme_need_text_is_redacted(self):
        model, prompts = fake_model(SchemeChoice(scheme_id="demo_senior_support", reason="Pension request"))
        with patch("backend.agents.llm_agents.structured_model", return_value=model):
            choose_scheme("Pension help please, call me on 9876543210", [{"scheme_id": "demo_senior_support", "name": "Demo"}])
        self.assertNotIn("9876543210", prompts[0])
        self.assertIn("[PHONE_1]", prompts[0])

    def test_llm_is_not_called_when_redaction_fails(self):
        state = document_success_state()
        state.pop("claimed_type")
        state["use_llm"] = True
        model_factory = MagicMock()
        with patch("backend.agents.llm_agents.redact", side_effect=RuntimeError("redaction broke")), \
                patch("backend.agents.llm_agents.structured_model", model_factory):
            result = build_document_graph().invoke(state)
        self.assertEqual(result["status"], "failed_tool")
        self.assertIn("classify_document:llm_error", result["events"])
        model_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
