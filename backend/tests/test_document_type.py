"""Tests for guessing a document's type from its words on this machine (tools/document_type.py).

These texts are made up for the tests; the held-out set in datasets/classify_heldout is
scored by the complete suite instead and is not used here.
"""

import unittest
from unittest.mock import patch

from backend.graphs.document_graph import build_document_graph
from backend.tools.document_type import type_from_text, type_scores
from test_graph_ocr import AADHAAR, ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env


class TypeFromTextTests(unittest.TestCase):
    def test_each_type_is_found_from_its_usual_words(self):
        cases = {
            "identity": "GOVERNMENT OF INDIA\nAadhaar\nName: Meera Rao\nDOB: 01/01/1950",
            "income_certificate": "INCOME CERTIFICATE\nOffice of the Tehsildar\nAnnual income: Rs. 1,20,000",
            "residence_proof": "DOMICILE CERTIFICATE\nMeera Rao is a resident of Kerala",
            "health_insurance": "Health Insurance\nPolicy No: DH/2026/1234\nSum Insured: Rs 3,00,000",
            "bank_passbook": "DEMO GRAMIN BANK\nPassbook\nAccount No: 123456789012\nIFSC: DGBK0123456",
            "disability_certificate": "Disability Certificate\nMedical Board\nLocomotor 40%",
            "lab_report": "Demo Diagnostics Laboratory\nTest   Result   Unit\nHaemoglobin   13.0   g/dL",
            "prescription": "Dr. Demo MBBS\nRx\nTab. Amlodipine 5 mg once a day",
            "medical_bill": "Demo Hospital\nBill\nParticulars   Amount\nConsultation   500.00\nTotal 500.00",
        }
        for expected, text in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(type_from_text(text), expected)

    def test_heading_counts_more_than_a_body_mention(self):
        # An income certificate that also prints the Aadhaar number is still an income certificate.
        text = f"INCOME CERTIFICATE\nName: Meera Rao\nAadhaar: {AADHAAR}\nAnnual income: Rs. 90,000"
        self.assertEqual(type_from_text(text), "income_certificate")

    def test_pan_card_is_identity_despite_the_word_income(self):
        text = "INCOME TAX DEPARTMENT\nPermanent Account Number\nMEERA RAO\nDate of Birth 01/01/1950"
        self.assertEqual(type_from_text(text), "identity")

    def test_hindi_heading_is_understood(self):
        self.assertEqual(type_from_text("निवास प्रमाण पत्र\nश्रीमती मीरा राव"), "residence_proof")

    def test_page_with_no_telling_words_is_left_to_the_owner(self):
        self.assertIsNone(type_from_text("Name: Meera Rao\nDate: 01/01/2026"))
        self.assertIsNone(type_from_text(""))

    def test_two_types_equally_likely_is_left_to_the_owner(self):
        text = "Passbook\nDisability\nName: Meera Rao"
        scores = type_scores(text)
        self.assertEqual(scores["bank_passbook"], scores["disability_certificate"])
        self.assertIsNone(type_from_text(text))

    def test_a_bill_that_is_not_medical_is_not_a_medical_bill(self):
        self.assertIsNone(type_from_text("WATER BILL\nConnection No: 4455\nTotal 300.00"))
        self.assertEqual(type_scores("WATER BILL\nTotal 300.00")["medical_bill"], 0)


@patch("backend.utils.privacy.find_env_file", return_value=None)
class GraphTypeFromTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def classify(self, lines):
        # No claimed type, a file name with no hint, no model: only the page's words can tell.
        with patch("backend.graphs.document_graph.read_document", return_value=[ocr_page(lines)]), \
                hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(upload_state(UNIT / "sharp.png", "image/png"))

    def test_type_comes_from_the_page_words(self, _):
        result = self.classify([("DOMICILE CERTIFICATE", 0.99), ("Name: Meera Rao", 0.99),
                                ("State: Kerala", 0.99), ("Issue date: 01/06/2026", 0.99)])
        self.assertEqual(result["document_type"], "residence_proof")
        self.assertIn("classify_document:residence_proof", result["events"])

    def test_unclear_page_asks_the_owner_to_choose(self, _):
        result = self.classify([("Name: Meera Rao", 0.99), ("Date: 01/06/2026", 0.99)])
        self.assertEqual(result["status"], "needs_classification")
        self.assertIn("classify_document:unknown", result["events"])


if __name__ == "__main__":
    unittest.main()
