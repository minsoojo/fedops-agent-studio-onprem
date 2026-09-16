from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from studio_runtime.folder import prepare_project_data_directory
from studio_runtime.folder import open_project_data_directory
from studio_runtime.folder_bridge import FolderBridgeServer
from studio_runtime.validation_data import build_validation_archive, validation_selection


class ValidationDataTests(unittest.TestCase):
    def test_host_bridge_opens_separate_validation_folder(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = root / 'token'
            token.write_text('test-only')
            server = FolderBridgeServer(('127.0.0.1', 0), root, 'test-only')
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with patch('studio_runtime.folder_bridge.open_directory') as opened:
                    result = open_project_data_directory(root / 'accounts/account-test/.local-data', 'task',
                        local_project_id='x', purpose='validation',
                        bridge_url=f'http://127.0.0.1:{server.server_port}', token_file=token,
                        bridge_relative_local_data_root=Path('accounts/account-test/.local-data'))
                    self.assertTrue(result['opened'])
                    self.assertTrue(result['containerPath'].endswith('/task/validation'))
                    opened.assert_called_once()
            finally:
                server.shutdown(); server.server_close(); worker.join(2)

    def test_separate_folder_and_deterministic_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            training = prepare_project_data_directory(root, 'task', local_project_id='x')
            validation = prepare_project_data_directory(root, 'task', local_project_id='x', purpose='validation')
            self.assertNotEqual(training['containerPath'], validation['containerPath'])
            data = Path(validation['containerPath'])
            (data / 'holdout').mkdir()
            (data / 'holdout/a.csv').write_text('x,y\n1,2')
            a = build_validation_archive(data, 'holdout', root / 'a.zip')
            b = build_validation_archive(data, 'holdout', root / 'b.zip')
            self.assertEqual(a, b)
            with zipfile.ZipFile(root / 'a.zip') as archive:
                self.assertEqual(archive.namelist(), ['a.csv'])
            self.assertEqual(a['fileCount'], 1)

    def test_rejects_traversal_empty_hidden_symlink_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'data'
            root.mkdir()
            for relative in ('../x', '/etc', 'a\\b', 'a//b', 'a\nb'):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    validation_selection(root, relative)
            output = Path(tmp) / 'a.zip'
            with self.assertRaisesRegex(ValueError, 'empty'):
                build_validation_archive(root, '', output)
            (root / '.secret').write_text('private')
            with self.assertRaisesRegex(ValueError, 'Hidden'):
                build_validation_archive(root, '', output)
            (root / '.secret').unlink()
            (root / 'link').symlink_to(Path(tmp))
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                build_validation_archive(root, '', output)
            (root / 'link').unlink()
            (root / 'a.csv').write_text('123')
            with patch('studio_runtime.validation_data.MAX_BYTES', 2), self.assertRaises(ValueError):
                build_validation_archive(root, '', output)
