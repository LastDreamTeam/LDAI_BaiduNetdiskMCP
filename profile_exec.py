# SPDX-License-Identifier: AGPL-3.0-only
"""Run an operator-selected login or setup command in a named profile's environment."""
import os
import subprocess
import sys
from router import PRODUCT, profiles

if __name__ == "__main__":
    entries=profiles()
    if len(sys.argv)<4 or sys.argv[1] not in entries or sys.argv[2]!="--":
        raise SystemExit("Usage: python profile_exec.py ACCOUNT -- PROGRAM [ARGS ...]")
    env=dict(os.environ)
    if len(entries)>1:
        for k in PRODUCT["providerEnvironment"]:
            env.pop(k,None)
    env.update(entries[sys.argv[1]].get("env",{}))
    raise SystemExit(subprocess.call(sys.argv[3:],env=env))
