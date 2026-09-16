from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from studio_api.features.workspace.router import upload_validation_data, validation_owner
from studio_api.features.workspace.schemas import ValidationUploadRequest

MODULE = 'studio_api.features.workspace.router'


class ValidationUploadTests(unittest.TestCase):
    def test_consent_must_be_explicit_true(self):
        for value in (False, 'true', 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ValidationUploadRequest(consent=value)
        self.assertTrue(ValidationUploadRequest(consent=True).consent)

    def test_participant_is_denied_before_data_access(self):
        with patch(MODULE + '.require_account'), patch(MODULE + '.project_for', return_value=(
                {'taskBinding': {'workspaceRole': 'participant', 'runtimeKey': 'a'}}, Path('/unused'))):
            with self.assertRaises(HTTPException) as caught:
                validation_owner(None, 'local:a')
            self.assertEqual(caught.exception.status_code, 403)

    def test_authoritative_permission_rechecked_before_reading_files(self):
        account = SimpleNamespace(workspace_root=Path('/unused/projects'))
        with patch(MODULE + '.validation_owner', return_value=(account, Path('/unused/task'), 'task-key')), \
                patch(MODULE + '.require_fedops_session', return_value={}), \
                patch(MODULE + '.fedops_request', side_effect=PermissionError('denied')), \
                patch(MODULE + '.prepare_project_data_directory') as prepare:
            with self.assertRaises(HTTPException) as caught:
                upload_validation_data('local:x', None, ValidationUploadRequest(consent=True))
            self.assertEqual(caught.exception.status_code, 403)
            prepare.assert_not_called()

    def test_upload_uses_snapshot_and_cleans_temp_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validation = root / '.local-data/federated-tasks/task/validation'
            validation.mkdir(parents=True)
            (validation / 'a.csv').write_text('x,y\n1,2')
            account = SimpleNamespace(workspace_root=root / 'projects')
            with patch(MODULE + '.validation_owner', return_value=(account, root / 'projects/task', 'task-key')), \
                    patch(MODULE + '.require_fedops_session', return_value={}), \
                    patch(MODULE + '.fedops_request', return_value={'items': []}), \
                    patch(MODULE + '.upload_fedops_binary', return_value={'dataPath': 'validation-id'}) as upload:
                result = upload_validation_data('local:x', None, ValidationUploadRequest(consent=True))
            self.assertEqual(result['dataPath'], 'validation-id')
            self.assertEqual(upload.call_args.args[1], 'server-control/validation-data/task-key')
            self.assertEqual(upload.call_args.kwargs['headers']['X-FedOps-Server-Data-Consent'], 'true')
            self.assertFalse(upload.call_args.args[2].exists())
