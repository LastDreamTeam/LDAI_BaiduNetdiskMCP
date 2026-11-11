# SPDX-License-Identifier: AGPL-3.0-only
"""Run a fixed provider command, keeping provider configuration in its own profile."""
import json
import re
import subprocess
import tempfile

SENSITIVE = re.compile(r"password|secret|cookie|authorization|privatekey|apikey|token|dlink|sessionkey", re.I)

def public_result(value):
    if isinstance(value, dict):
        return {k: public_result(v) for k, v in value.items()
                if not SENSITIVE.search(k.replace("_", "").replace("-", ""))}
    if isinstance(value, list):
        return [public_result(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"(?i)([?&](?:access_token|refresh_token|token|sign)=)[^&#\s]+", r"\1[redacted]", value)
    return value

def run_json(argv, timeout=60):
    with tempfile.TemporaryFile() as output:
        try:
            proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=output,
                                  stderr=subprocess.DEVNULL, timeout=timeout, shell=False)
        except FileNotFoundError:
            raise ValueError("Provider CLI not installed. Follow the README setup steps.") from None
        except subprocess.TimeoutExpired:
            raise ValueError("Provider timed out. Check the result before repeating a write.") from None
        if output.tell() > 8 * 1024 * 1024:
            raise ValueError("Result exceeds 8 MiB. Request a smaller page.")
        output.seek(0)
        try:
            value = json.load(output)
        except (ValueError, UnicodeError):
            raise ValueError(f"Provider returned no JSON (exit {proc.returncode}). Verify CLI setup and login in a terminal.") from None
    if proc.returncode or isinstance(value, dict) and (
        value.get("success") is False or value.get("ok") is False or
        value.get("code") not in (None, 0, 200, "0", "200") or value.get("error")
    ):
        raise ValueError("Provider rejected the operation. Check login, resource access and CLI version.")
    return public_result(value)
