"""Offline regressions for the Windows POC's trusted missing-hash handling."""
import copy
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'windows_poc_prepare', Path(__file__).resolve().parents[1] / 'packaging/windows-poc/prepare.py')
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class WheelHashesTest(unittest.TestCase):
    def setUp(self):
        name, version, url = next(iter(prepare.MISSING_WHEEL_HASHES))
        self.entry = {'metadata': {'name': name, 'version': version},
                      'download_info': {'url': url, 'archive_info': {}}}

    def test_missing_hash_download_is_verified(self):
        data = b'fixture wheel bytes'
        sha = hashlib.sha256(data).hexdigest()
        key = next(iter(prepare.MISSING_WHEEL_HASHES))
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(prepare.MISSING_WHEEL_HASHES, {key: sha}), patch.object(
                    prepare, 'urlopen', return_value=io.BytesIO(data)) as download:
                self.assertEqual(prepare.wheel_sha256(self.entry, directory), sha)
                download.assert_called_once_with(self.entry['download_info']['url'], timeout=120)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_wrong_download_fails(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                prepare, 'urlopen', return_value=io.BytesIO(b'wrong wheel')):
            with self.assertRaisesRegex(ValueError, 'Hash mismatch'):
                prepare.wheel_sha256(self.entry, directory)

    def test_unknown_missing_hash_fails_without_download(self):
        for field, value in [('name', 'other'), ('version', '3.1.7')]:
            entry = copy.deepcopy(self.entry)
            entry['metadata'][field] = value
            with patch.object(prepare, 'urlopen') as download:
                with self.assertRaisesRegex(ValueError, 'No trusted SHA256'):
                    prepare.wheel_sha256(entry, '.')
                download.assert_not_called()

    def test_untrusted_source_fails(self):
        self.entry['download_info']['url'] = 'https://example.com/jinja2-3.1.6-py3-none-any.whl'
        with self.assertRaisesRegex(ValueError, 'Untrusted wheel source'):
            prepare.wheel_sha256(self.entry, '.')

    def test_existing_hash_preserved_and_conflicting_known_hash_fails(self):
        sha = next(iter(prepare.MISSING_WHEEL_HASHES.values()))
        self.entry['download_info']['archive_info'] = {'hashes': {'sha256': sha}}
        with patch.object(prepare, 'urlopen') as download:
            self.assertEqual(prepare.wheel_sha256(self.entry, '.'), sha)
            download.assert_not_called()
        self.entry['download_info']['archive_info']['hashes']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'disagrees'):
            prepare.wheel_sha256(self.entry, '.')

    def test_invalid_hash_fails(self):
        self.entry['download_info']['archive_info'] = {'hashes': {'sha256': 'bad'}}
        with self.assertRaisesRegex(ValueError, 'Invalid wheel SHA256'):
            prepare.wheel_sha256(self.entry, '.')


if __name__ == '__main__':
    unittest.main()
