# SPDX-License-Identifier: AGPL-3.0-only
import os,hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import server,baidu_transfer as tx
class Transfers(unittest.TestCase):
    def test_limits_and_block_upload(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{"BAIDU_OAUTH_ROOT":"/team"}):
            p=Path(d)/"a";p.write_bytes(b"test")
            with self.assertRaises(ValueError):tx.scoped("/team-extra/a")
            with patch.object(tx,"token_value",return_value="fixture"),patch.object(tx,"post",side_effect=[{"uploadid":"fixture","block_list":[0]},{"md5":hashlib.md5(b"test").hexdigest()},{"fs_id":5,"size":4,"path":"/team/a"}]) as api:
                self.assertTrue(tx.upload(p,"/team/a",7)["provider_accepted"])
                self.assertEqual(api.call_args_list[1].args[-1].rate,7*1024)
            with patch.object(tx.time,"monotonic",return_value=100),patch.object(tx.time,"sleep") as wait:
                tx.Pacer(1).wait(1024);wait.assert_called_once_with(1.0)
    def test_partial_directory_size(self):
        with patch.object(server,"query",return_value=[{"isdir":1,"path":"/nested"},{"isdir":0,"path":"/a","size":8}]):
            result=server.baidu_directory_size("/",1)
            self.assertEqual(result["bytes"],8);self.assertFalse(result["complete"])
