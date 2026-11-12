"""Dispatch an explicitly selected local connector tool from a skill package."""
import argparse
import asyncio
import json
import sys

import router


async def run(tool):
    try:
        catalog = await router.handle({"method": "tools/list", "params": {}})
        if tool is None:
            return {"tools": catalog["tools"]}
        if tool not in {entry["name"] for entry in catalog["tools"]}:
            raise ValueError("Tool is unavailable in this local profile.")
        args = json.load(sys.stdin)
        if not isinstance(args, dict):
            raise ValueError("Supply a JSON object on standard input.")
        return await router.handle({"method": "tools/call", "params": {"name": tool, "arguments": args}})
    finally:
        await asyncio.gather(*(worker.close() for worker in router.WORKERS.values()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", nargs="?", help="Omit to list tools with their schemas.")
    result = asyncio.run(run(parser.parse_args().tool))
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(int(result.get("isError", False)))
