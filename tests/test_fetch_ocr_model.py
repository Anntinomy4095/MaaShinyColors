import base64
import hashlib
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from tools import fetch_ocr_model as fetch


class FetchOcrModelTests(unittest.TestCase):
    def setUp(self):
        self.data = b"test model"
        self.sha = hashlib.sha1(b"blob 10\0" + self.data).hexdigest()
        self.source = ("rec.onnx", "OCR/ppocr_v3/ja_jp/rec.onnx", 10, self.sha)

    def api_responses(self):
        metadata = {"name": self.source[0], "path": self.source[1], "size": 10,
                    "sha": self.sha, "git_url": f"{fetch.API_ROOT}/git/blobs/{self.sha}"}
        blob = {"size": 10, "sha": self.sha, "encoding": "base64",
                "content": base64.b64encode(self.data).decode()}
        return metadata, blob

    def test_raw_success_is_verified_without_api(self):
        with patch.object(fetch, "read_url", return_value=self.data) as read:
            data, record = fetch.fetch_file(self.source)
        self.assertEqual(data, self.data)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(record["git_blob_sha1"], self.sha)
        self.assertEqual(record["sha256"], hashlib.sha256(self.data).hexdigest())

    def test_network_failure_uses_verified_fixed_commit_blob(self):
        metadata, blob = self.api_responses()
        with patch.object(fetch, "read_url", side_effect=[urllib.error.URLError("offline"),
                          json.dumps(metadata).encode(), json.dumps(blob).encode()]) as read:
            data, record = fetch.fetch_file(self.source)
        self.assertEqual(data, self.data)
        self.assertIn(f"?ref={fetch.COMMIT}", read.call_args_list[1].args[0])
        self.assertEqual(record["url"], metadata["git_url"])

    def test_wrong_commit_path_is_rejected_before_blob_download(self):
        metadata, _ = self.api_responses()
        metadata["path"] = "different/path"
        with patch.object(fetch, "read_url", side_effect=[urllib.error.URLError("offline"),
                          json.dumps(metadata).encode()]) as read:
            with self.assertRaisesRegex(ValueError, "身份不匹配"):
                fetch.fetch_file(self.source)
        self.assertEqual(read.call_count, 2)

    def test_modified_content_is_rejected(self):
        with patch.object(fetch, "read_url", return_value=b"fake model"):
            with self.assertRaisesRegex(ValueError, "SHA-1"):
                fetch.fetch_file(self.source)

    def test_failed_download_cleans_stage_and_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "models" / "candidate"
            with patch.object(fetch, "PRIVATE_ROOT", root), patch.object(fetch, "fetch_file",
                              side_effect=[(self.data, {}), OSError("download failed")]):
                with self.assertRaises(OSError):
                    fetch.install_model(destination)
                self.assertFalse(destination.exists())
                self.assertEqual(list(destination.parent.iterdir()), [])
                destination.mkdir()
                (destination / "keep.txt").write_text("preserve")
                with self.assertRaises(FileExistsError):
                    fetch.install_model(destination)
                self.assertEqual((destination / "keep.txt").read_text(), "preserve")

    def test_success_publishes_models_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "models" / "candidate"
            with patch.object(fetch, "PRIVATE_ROOT", root), patch.object(fetch, "fetch_file",
                              return_value=(self.data, {"verified": True})):
                self.assertEqual(fetch.install_model(destination), destination.resolve())
            self.assertEqual(sorted(p.name for p in destination.iterdir()),
                             sorted(source[0] for source in fetch.SOURCES))
            provenance = json.loads(destination.with_name("candidate.provenance.json").read_text())
            self.assertEqual(provenance["commit"], fetch.COMMIT)
            self.assertEqual(len(provenance["files"]), 3)


if __name__ == "__main__":
    unittest.main()
