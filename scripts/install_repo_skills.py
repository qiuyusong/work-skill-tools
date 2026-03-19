#!/usr/bin/env python3
"""Install one or all skills from qiuyusong/work-skill-tools."""

from __future__ import annotations

import argparse
import json
import subprocess
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
    parser.add_argument(
        "--method",
        choices=["auto", "download", "git"],
        default="auto",
        help="Deprecated. Retained for compatibility and ignored in the npx-based flow.",
    )
    parser.add_argument(
        "--dest",
        help="Project directory used for project-level install. Omit it to install in the current directory.",
    )
    parser.add_argument("--list", action="store_true", help="List available skills and exit.")
    parser.add_argument("--global", dest="global_install", action="store_true", help="Install globally.")
    parser.add_argument("--agent", action="append", default=[], help="Agent to install to. Repeatable.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompts.")
    parser.add_argument("--copy", action="store_true", help="Copy files instead of symlinking.")
    parser.add_argument("--full-depth", action="store_true", help="Search all subdirectories for skills.")
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


def resolve_selected_names(manifest: dict[str, Any], skill_names: list[str], install_all: bool) -> list[str]:
    skills = manifest["skills"]
    by_name = {
        str(item.get("name")): str(item.get("path"))
        for item in skills
        if isinstance(item, dict) and item.get("name") and item.get("path")
    }
    if install_all:
        return list(by_name)
    missing = [name for name in skill_names if name not in by_name]
    if missing:
        raise SystemExit(f"Unknown skill(s): {', '.join(missing)}")
    return [name for name in skill_names if name in by_name]


def build_source(repo: str, ref: str) -> str:
    if ref == DEFAULT_REF:
        return repo
    return f"https://github.com/{repo}.git#{ref}"


def build_install_command(
    repo: str,
    ref: str,
    selected_names: list[str],
    install_all: bool,
    agents: list[str],
    global_install: bool,
    assume_yes: bool,
    copy_files: bool,
    full_depth: bool,
) -> list[str]:
    command = [
        "npx",
        "skills",
        "add",
        build_source(repo, ref),
    ]
    if install_all:
        command.extend(["--skill", "*"])
    else:
        command.extend(["--skill", *selected_names])
    if agents:
        command.extend(["--agent", *agents])
    if global_install:
        command.append("--global")
    if assume_yes:
        command.append("--yes")
    if copy_files:
        command.append("--copy")
    if full_depth:
        command.append("--full-depth")
    return command


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

    resolved_names = resolve_selected_names(manifest, selected_names, args.all)
    command = build_install_command(
        repo=args.repo,
        ref=args.ref,
        selected_names=resolved_names,
        install_all=args.all,
        agents=args.agent,
        global_install=args.global_install,
        assume_yes=args.yes,
        copy_files=args.copy,
        full_depth=args.full_depth,
    )
    workdir = Path(args.dest).expanduser().resolve() if args.dest else Path.cwd()
    workdir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, check=False, cwd=workdir)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
