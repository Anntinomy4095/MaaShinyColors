"""Batch replay must keep shared offline evidence immutable and hash-bound."""
import argparse
from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tools import replay_daily_navigation_batch as batch


class DailyNavigationBatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        for name in ('manifest.json', 'sources.json', 'foreground.json', 'contracts.json', 'sample.png'):
            (self.base / name).write_bytes(name.encode())
        self.args = argparse.Namespace(manifest=self.base / 'manifest.json', sources=self.base / 'sources.json',
                                       foreground_manifest=self.base / 'foreground.json',
                                       foreground_contracts=self.base / 'contracts.json')
        self.original = batch.file_hash(self.base / 'sample.png')
        self.data = {'samples': [{'id': 'one', 'image': 'sample.png'}],
                     'scenes': [{'name': 'SC.Test.Anchor', 'recognition': 'ColorMatch'}]}
        self.truth = {'sample.png': {'sha256': self.original, 'actions': ['home-mission']}}

    def cache(self, recognizer):
        with patch.object(batch, 'load_contract', return_value=(self.data, self.truth, 'daily-digest')), \
             patch.object(batch, 'load_configuration', return_value=({'scenes': []}, {}, 'foreground-digest', {})), \
             patch.object(batch, 'template_path', return_value=self.base / 'sample.png'), \
             patch.object(batch, 'interpret_foreground', return_value={'state': 'no_known_popup'}), \
             patch.object(batch, 'read_png', return_value=np.zeros((720, 1280, 3), dtype=np.uint8)) as decode:
            result = batch.cache_samples(self.args, recognizer)
            return result, decode.call_count

    def test_shared_image_is_decoded_once_and_cannot_be_made_writeable(self):
        recognizer = Mock(input_attempts=0)
        recognizer.recognize.return_value = {'scene': 'SC.Test.Anchor', 'hit': False}
        (samples, templates, hashes, digest, foreground_digest), decode_count = self.cache(recognizer)
        self.assertEqual(decode_count, 1)
        self.assertEqual(recognizer.recognize.call_count, 1)
        self.assertIs(samples[0].image, templates['SC.Test.Anchor.png'])
        with self.assertRaises(ValueError):
            samples[0].image.setflags(write=True)
        with self.assertRaises(FrozenInstanceError):
            samples[0].hits_json = 'changed'
        self.assertEqual(samples[0].sha256, self.original)
        self.assertEqual(samples[0].actions, ('home-mission',))
        self.assertEqual((digest, foreground_digest), ('daily-digest', 'foreground-digest'))
        batch.check_files(hashes)

    def test_original_source_hash_is_required_before_decoding(self):
        self.truth['sample.png']['sha256'] = hashlib.sha256(b'other').hexdigest()
        with self.assertRaisesRegex(ValueError, '原始哈希'):
            self.cache(Mock(input_attempts=0))

    def test_sample_changed_by_recognition_is_refused(self):
        recognizer = Mock(input_attempts=0)
        def recognize(*args):
            (self.base / 'sample.png').write_bytes(b'changed')
            return {'scene': 'SC.Test.Anchor', 'hit': False}
        recognizer.recognize.side_effect = recognize
        with self.assertRaisesRegex(ValueError, '样本发生变化'):
            self.cache(recognizer)

    def test_recognition_controller_input_cannot_produce_evidence(self):
        recognizer = Mock(input_attempts=1)
        recognizer.recognize.return_value = {'scene': 'SC.Test.Anchor', 'hit': False}
        with self.assertRaisesRegex(ValueError, '输入请求'):
            self.cache(recognizer)


if __name__ == '__main__':
    unittest.main()
