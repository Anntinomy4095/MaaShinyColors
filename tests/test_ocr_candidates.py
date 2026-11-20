import copy
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import fetch_ocr_candidates as fc


def record():
    return {"repository": fc.REPOSITORY, "commit": "a" * 40, "files": [
        {"name": n, "path": "OCR/x/" + n, "bytes": 3, "git_blob_sha1": "b" * 40, "sha256": "c" * 64}
        for n in fc.MODEL_FILES]}


class CandidateTests(unittest.TestCase):
    def test_valid_provenance(self):
        fc.validate_provenance(record())

    def test_invalid_provenance(self):
        bad = record(); bad["commit"] = "main"
        with self.assertRaises(ValueError): fc.validate_provenance(bad)
        bad = record(); bad["files"].pop()
        with self.assertRaises(ValueError): fc.validate_provenance(bad)
        bad = copy.deepcopy(record()); bad["files"][0]["sha256"] = "zz"
        with self.assertRaises(ValueError): fc.validate_provenance(bad)

    def test_blob_verification(self):
        data = b"hello"
        sha = hashlib.sha1(b"blob 5\0hello").hexdigest()
        fc.verify_bytes(data, 5, sha)
        with self.assertRaises(ValueError): fc.verify_bytes(data, 4, sha)
        with self.assertRaises(ValueError): fc.verify_bytes(data, 5, "0" * 40)


if __name__ == "__main__":
    unittest.main()
