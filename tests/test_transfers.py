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

    def test_oauth_reconnect(self):
        import json
        import baidu_oauth as oauth
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            secret=folder/"application"
            secret.write_text("fixture",encoding="utf8")
            grant=folder/"grant"
            grant.write_text("old grant",encoding="utf8")
            session=dict(verification_url="https://openapi.baidu.com/device",interval=1,expires_in=120,device_code="device",user_code="code")
            with patch.dict(os.environ,dict(BAIDU_CLIENT_ID="example",BAIDU_CLIENT_SECRET_FILE=str(secret),BAIDU_ACCESS_TOKEN_FILE=str(grant))),patch.object(oauth.time,"sleep"):
                with patch.object(oauth,"request",side_effect=[session,{"error":"access_denied"}]):
                    with self.assertRaises(ValueError):oauth.authorize(lambda url,code: None)
                self.assertEqual(grant.read_text(),"old grant")
                with patch.object(oauth,"request",side_effect=[session,{"error":"authorization_pending"},{"access_token":"new grant","scope":"netdisk"}]):
                    oauth.authorize(lambda url,code: None)
                self.assertEqual(json.loads(grant.read_text())["access_token"],"new grant")
