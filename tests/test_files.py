# SPDX-License-Identifier: AGPL-3.0-only
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import open_platform
import server

class FilesTest(unittest.TestCase):
    def test_authorized_download_is_verified_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            dst = Path(temp) / "sample.txt"
            record = {"fs_id":1,"size":4,"dlink":"https://pan.baidu.com/sample"}
            opener = MagicMock()
            opener.open.return_value = io.BytesIO(b"xy")
            with patch.object(open_platform,"token_value",return_value="example"), \
                 patch.object(open_platform,"metadata",return_value=[record]), \
                 patch.object(open_platform,"build_opener",return_value=opener):
                with self.assertRaisesRegex(ValueError,"Incomplete"):
                    open_platform.download_file(1,dst,20)
                self.assertFalse(dst.exists())
                opener.open.return_value = io.BytesIO(b"data")
                result = open_platform.download_file(1,dst,20)
                self.assertEqual(result["bytes"],4)
                self.assertEqual(dst.read_bytes(),b"data")
                with self.assertRaisesRegex(ValueError,"exists"):
                    open_platform.download_file(1,dst,20)

    def test_paths_and_redirects(self):
        for path in ("../x","/x","a/../b"):
            with self.assertRaises(ValueError):
                server.remote(path)
        with self.assertRaises(ValueError):
            open_platform.trusted_download("https://example.org/file")
        with self.assertRaises(ValueError):
            open_platform.trusted_download("http://pan.baidu.com/sample")

    def test_application_root_uses_native_command(self):
        with patch.object(server,"cli",return_value={}) as run:
            server.baidu_list()
            self.assertEqual(run.call_args.args[0],["ls"])
