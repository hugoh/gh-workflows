"""Emit a JSON map of which manifest [tools] entries changed since a git ref."""

import json
import subprocess
import sys

import tomllib


def load_tools(ref: str, manifest: str) -> dict:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{manifest}"], capture_output=True, check=False
    )
    if proc.returncode != 0:
        return {}
    return tomllib.loads(proc.stdout.decode()).get("tools", {})


def main() -> None:
    if not 2 <= len(sys.argv) <= 3:
        sys.exit("usage: tool_bumps.py <base-ref> [manifest]")
    base_ref = sys.argv[1]
    manifest = sys.argv[2] if len(sys.argv) == 3 else "mise.toml"
    base_tools = load_tools(base_ref, manifest)
    head_tools = load_tools("HEAD", manifest)
    changed = {
        key: base_tools.get(key) != head_tools.get(key)
        for key in set(base_tools) | set(head_tools)
    }
    print(json.dumps(changed))


if __name__ == "__main__":
    main()
