# SPDX-License-Identifier: AGPL-3.0-only
"""Select the plugin's local Python environment and start the stdio server."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
local_python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
python = os.getenv("LDAI_MCP_PYTHON") or (str(local_python) if local_python.is_file() else sys.executable)
os.execv(python, [python, str(root / "server.py")])
