# SPDX-License-Identifier: AGPL-3.0-only
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from account_manager import load,save,replace_entry
import router

class Management(unittest.TestCase):
    def test_concurrent_edit_disconnect_and_isolation(self):
        with tempfile.TemporaryDirectory() as t,patch.dict(os.environ,{"LDAI_ACCOUNTS_FILE":str(Path(t)/"accounts.json")}):
            old={"connector":router.NAME,"accounts":{"default":{"command":"operator-helper","args":["existing"]}}}
            Path(os.environ["LDAI_ACCOUNTS_FILE"]).write_text(json.dumps(old))
            new=replace_entry(old,"default","work",{**old["accounts"]["default"],"label":"Work"})
            save(old,new)
            with self.assertRaises(ValueError):save(old,old)
            self.assertEqual(load()["accounts"]["work"]["args"],["existing"])
            empty={**new,"accounts":{}};save(new,empty)
            self.assertEqual(router.profiles(),{})
