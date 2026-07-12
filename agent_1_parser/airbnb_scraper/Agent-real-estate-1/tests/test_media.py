import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from media import cleanup_paths, download_images


class MediaDownloadTest(unittest.TestCase):
    @patch('media.requests.get')
    def test_download_images_uses_stream_true(self, mock_get):
        response = MagicMock()
        response.iter_content.return_value = [b'chunk1', b'chunk2']
        mock_get.return_value = response

        with tempfile.TemporaryDirectory() as folder:
            paths = download_images(['http://example.com/a.jpg'], folder=folder)
            mock_get.assert_called_once_with('http://example.com/a.jpg', stream=True, timeout=45)
            self.assertEqual(len(paths), 1)
            self.assertTrue(os.path.exists(paths[0]))
            with open(paths[0], 'rb') as file:
                self.assertEqual(file.read(), b'chunk1chunk2')

    def test_cleanup_paths_removes_files(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        self.assertTrue(os.path.exists(path))
        cleanup_paths([path])
        self.assertFalse(os.path.exists(path))


if __name__ == '__main__':
    unittest.main()
