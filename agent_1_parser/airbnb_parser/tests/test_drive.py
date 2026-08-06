import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from drive import GoogleDriveUploader


class DriveUploaderTest(unittest.TestCase):
    def test_is_configured_requires_root_folder(self):
        uploader = GoogleDriveUploader()
        with patch.object(uploader, '_has_oauth_token', return_value=True):
            with patch('google_drive.config.ENABLE_DRIVE_UPLOAD', True):
                with patch('google_drive.config.DRIVE_ROOT_FOLDER_ID', ''):
                    self.assertFalse(uploader.is_configured())

    def test_upload_photos_without_oauth_returns_empty(self):
        uploader = GoogleDriveUploader()
        with patch.object(uploader, '_has_oauth_token', return_value=False):
            folder_url, count = uploader.upload_photos('PHK-0001', ['a.jpg'])
        self.assertEqual(folder_url, '')
        self.assertEqual(count, 0)

    @patch('google_drive.build')
    def test_upload_photos_creates_files(self, mock_build):
        service = MagicMock()
        mock_build.return_value = service
        service.files.return_value.list.return_value.execute.return_value = {'files': []}
        service.files.return_value.create.return_value.execute.side_effect = [
            {'id': 'folder123', 'webViewLink': 'https://drive/folder123'},
            {'id': 'file1'},
            {'id': 'file2'},
        ]

        uploader = GoogleDriveUploader()
        with patch.object(uploader, '_has_oauth_token', return_value=True):
            with patch.object(uploader, '_get_service', return_value=service):
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                    tmp.write(b'fake-image')
                    tmp_path = tmp.name
                try:
                    folder_url, count = uploader.upload_photos('PHK-0001', [tmp_path])
                finally:
                    os.remove(tmp_path)

        self.assertEqual(count, 1)
        self.assertIn('folder123', folder_url)


if __name__ == '__main__':
    unittest.main()
