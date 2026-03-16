#!/usr/bin/env python3
"""Install one or all skills from qiuyusong/work-skill-tools."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_REPO = "qiuyusong/work-skill-tools"
DEFAULT_REF = "main"
MANIFEST_PATH = "skills/manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install skills from work-skill-tools.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub owner/repo.")
    parser.add_argument("--ref", default=DEFAULT_REF, help="Git ref.")
    parser.add_argument("--all", action="store_true", help="Install all skills in manifest.")
    parser.add_argument("--skill", action="append", default=[], help="Skill name to install. Repeatable.")
    parser.add_argument("--method", choices=["auto", "download", "git"], default="auto")
    parser.add_argument("--dest", help="Destination skill directory.")
    parser.add_argument("--list", action="store_true", help="List available skills and exit.")
    return parser.parse_args()


def raw_manifest_url(repo: str, ref: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{ref}/{MANIFEST_PATH}"


def local_manifest_path() -> Path:
    return Path(__file__).resolve().parents[1] / MANIFEST_PATH


def load_manifest(repo: str, ref: str) -> dict[str, Any]:
    payload: str | None = None
    try:
        with urllib.request.urlopen(raw_manifest_url(repo, ref), timeout=30) as response:
            payload = response.read().decode("utf-8")
    except Exception:
        local_path = local_manifest_path()
        if local_path.exists():
            payload = local_path.read_text(encoding="utf-8")
        else:
            raise
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        raise SystemExit("Invalid manifest format.")
    return data


def find_installer_script() -> Path:
    candidates: list[Path] = []
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        candidates.append(Path(codex_home) / "skills" / ".system" / "skill-installer" / "scripts" / "install-skill-from-github.py")
    candidates.append(Path.home() / ".codex" / "skills" / ".system" / "skill-installer" / "scripts" / "install-skill-from-github.py")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit("Cannot find install-skill-from-github.py. Make sure Codex is installed on this machine.")


def resolve_selected_paths(manifest: dict[str, Any], skill_names: list[str], install_all: bool) -> list[str]:
    skills = manifest["skills"]
    by_name = {
        str(item.get("name")): str(item.get("path"))
        for item in skills
        if isinstance(item, dict) and item.get("name") and item.get("path")
    }
    if install_all:
        return list(by_name.values())
    missing = [name for name in skill_names if name not in by_name]
    if missing:
        raise SystemExit(f"Unknown skill(s): {', '.join(missing)}")
    return [by_name[name] for name in skill_names]


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.repo, args.ref)
    if args.list:
        for item in manifest["skills"]:
            print(f"{item['name']}: {item['path']}")
        return 0

    selected_names = [name.strip() for name in args.skill if name.strip()]
    if not args.all and not selected_names:
        raise SystemExit("Use --all or --skill <name>.")

    selected_paths = resolve_selected_paths(manifest, selected_names, args.all)
    installer = find_installer_script()
    command = [
        sys.executable,
        str(installer),
        "--repo",
        args.repo,
        "--ref",
        args.ref,
        "--method",
        args.method,
        "--path",
        *selected_paths,
    ]
    if args.dest:
        command.extend(["--dest", args.dest])
    result = subprocess.run(command, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
