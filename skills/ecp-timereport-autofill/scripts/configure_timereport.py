#!/usr/bin/env python3
"""Configure local settings for ecp-timereport-autofill skill."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from getpass import getpass
from pathlib import Path
from typing import Any

from device_binding import with_device_binding

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = SKILL_ROOT / "config" / "timereport-config.json"
DEFAULT_CONFIG: dict[str, Any] = {
    "projects": [],
    "ecp": {
        "base_url": "https://econtact.ai3.cloud/ecp",
        "username": "",
        "password": "",
    },
    "timereport": {
        "total_hours": 8.0,
        "git_author": "",
        "output_dir": "timereport-reports",
        "nearby_days": 7,
        "fuzzy_descriptions": [
            "优化相关代码",
            "调整了相关业务逻辑",
            "配合前端调整相关接口",
        ],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure ECP timereport skill variables.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Config path (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument("--show", action="store_true", help="Show config summary.")
    parser.add_argument("--interactive", action="store_true", help="Interactive update mode.")
    parser.add_argument("--repo", action="append", default=[], help="Repo path, repeatable.")
    parser.add_argument("--repos", help="Repo paths separated by ';'.")
    parser.add_argument("--project", action="append", default=[], help="Project mapping 'name=path', repeatable.")
    parser.add_argument("--projects", help="Project mappings separated by ';' in 'name=path' format.")
    parser.add_argument("--reset-repos", action="store_true", help="Clear configured repositories.")
    parser.add_argument("--ecp-url", help="Set ECP base URL.")
    parser.add_argument("--username", help="Set ECP username.")
    parser.add_argument("--password", help="Set ECP password.")
    parser.add_argument("--hours", type=float, help="Set total daily hours.")
    parser.add_argument("--nearby-days", type=int, help="Set nearby search days.")
    parser.add_argument(
        "--fuzzy-descriptions",
        help="Set month-end fuzzy descriptions separated by ';'.",
    )
    parser.add_argument("--git-author", help="Set git author filter.")
    parser.add_argument("--output-dir", help="Set report output directory.")
    return parser.parse_args()


def split_repos(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def split_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def parse_projects_input(values: list[str]) -> list[dict[str, str]]:
    projects: list[dict[str, str]] = []
    for value in values:
        item = value.strip()
        if not item:
            continue
        if "=" in item:
            name, path = item.split("=", 1)
            project_name = name.strip()
            project_path = path.strip()
        else:
            project_path = item
            project_name = Path(project_path).name
        if not project_path:
            continue
        if not project_name:
            project_name = Path(project_path).name
        projects.append({"name": project_name, "path": project_path})
    return projects


def projects_to_prompt(projects: list[dict[str, str]]) -> str:
    pairs: list[str] = []
    for project in projects:
        name = str(project.get("name", "")).strip()
        path = str(project.get("path", "")).strip()
        if not path:
            continue
        pairs.append(f"{name}={path}" if name else path)
    return ";".join(pairs)


def ensure_structure(raw: Any) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return config
    raw_projects = raw.get("projects")
    if isinstance(raw_projects, list):
        parsed: list[dict[str, str]] = []
        for item in raw_projects:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            path = str(item.get("path", "")).strip()
            if not path:
                continue
            parsed.append({"name": name or Path(path).name, "path": path})
        if parsed:
            config["projects"] = parsed
    elif isinstance(raw_projects, str):
        parsed = parse_projects_input(split_repos(raw_projects))
        if parsed:
            config["projects"] = parsed

    if isinstance(raw.get("repos"), list):
        parsed = parse_projects_input([str(item).strip() for item in raw["repos"] if str(item).strip()])
        if parsed and not raw_projects:
            config["projects"] = parsed
    elif isinstance(raw.get("repos"), str):
        parsed = parse_projects_input(split_repos(raw["repos"]))
        if parsed and not raw_projects:
            config["projects"] = parsed
    raw_ecp = raw.get("ecp") if isinstance(raw.get("ecp"), dict) else {}
    raw_timereport = raw.get("timereport") if isinstance(raw.get("timereport"), dict) else {}
    config["ecp"].update({k: raw_ecp.get(k, config["ecp"][k]) for k in config["ecp"]})
    config["timereport"].update(
        {k: raw_timereport.get(k, config["timereport"][k]) for k in config["timereport"]}
    )
    if isinstance(raw.get("device_binding"), dict):
        config["device_binding"] = dict(raw["device_binding"])
    if "repos" in config:
        config.pop("repos", None)
    return config


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return deepcopy(DEFAULT_CONFIG)
    data = json.loads(path.read_text(encoding="utf-8"))
    return ensure_structure(data)


def prompt_text(label: str, current: str, hide_current: bool = False) -> str:
    hint = "******" if hide_current and current else current
    value = input(f"{label} [{hint}]: ").strip()
    if value:
        return value
    return current


def interactive_update(config: dict[str, Any]) -> dict[str, Any]:
    print(f"Config file: {DEFAULT_CONFIG_PATH}")
    projects_current = projects_to_prompt(config["projects"])
    projects_input = input(
        f"Projects (name=path; separated) [{projects_current}]: "
    ).strip()
    if projects_input:
        config["projects"] = parse_projects_input(split_repos(projects_input))

    config["ecp"]["base_url"] = prompt_text("ECP URL", str(config["ecp"]["base_url"]))
    config["ecp"]["username"] = prompt_text("ECP username", str(config["ecp"]["username"]))
    password_input = getpass("ECP password [******]: ").strip()
    if password_input:
        config["ecp"]["password"] = password_input
    config["timereport"]["git_author"] = prompt_text(
        "Git author filter",
        str(config["timereport"]["git_author"]),
    )
    config["timereport"]["output_dir"] = prompt_text(
        "Timereport output dir",
        str(config["timereport"]["output_dir"]),
    )
    nearby_days_text = prompt_text("Nearby search days", str(config["timereport"]["nearby_days"]))
    try:
        config["timereport"]["nearby_days"] = int(nearby_days_text)
    except ValueError as exc:
        raise SystemExit(f"Invalid nearby days: {nearby_days_text}") from exc
    fuzzy_current = ";".join(config["timereport"]["fuzzy_descriptions"])
    fuzzy_text = prompt_text("Month-end fuzzy descriptions (; separated)", fuzzy_current)
    config["timereport"]["fuzzy_descriptions"] = split_items(fuzzy_text)
    hours_text = prompt_text("Total hours", str(config["timereport"]["total_hours"]))
    try:
        config["timereport"]["total_hours"] = float(hours_text)
    except ValueError as exc:
        raise SystemExit(f"Invalid hours: {hours_text}") from exc
    return config


def apply_cli_updates(config: dict[str, Any], args: argparse.Namespace) -> bool:
    changed = False
    if args.reset_repos:
        config["projects"] = []
        changed = True

    project_values: list[str] = []
    if args.projects:
        project_values.extend(split_repos(args.projects))
    if args.project:
        project_values.extend(args.project)
    if args.repos:
        project_values.extend(split_repos(args.repos))
    if args.repo:
        project_values.extend(args.repo)
    if project_values:
        config["projects"] = parse_projects_input(project_values)
        changed = True

    ecp_map = {
        "ecp_url": "base_url",
        "username": "username",
        "password": "password",
    }
    for arg_key, cfg_key in ecp_map.items():
        value = getattr(args, arg_key, None)
        if value is not None:
            config["ecp"][cfg_key] = value
            changed = True

    timereport_map = {
        "git_author": "git_author",
        "output_dir": "output_dir",
    }
    for arg_key, cfg_key in timereport_map.items():
        value = getattr(args, arg_key, None)
        if value is not None:
            config["timereport"][cfg_key] = value
            changed = True
    if args.hours is not None:
        config["timereport"]["total_hours"] = float(args.hours)
        changed = True
    if args.nearby_days is not None:
        config["timereport"]["nearby_days"] = int(args.nearby_days)
        changed = True
    if args.fuzzy_descriptions is not None:
        config["timereport"]["fuzzy_descriptions"] = split_items(args.fuzzy_descriptions)
        changed = True

    return changed


def compact_config(config: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "device_binding": dict(config.get("device_binding") or {}),
    }
    projects = config.get("projects") or []
    if projects:
        result["projects"] = projects

    ecp_result: dict[str, Any] = {}
    for key in ("username", "password"):
        value = str(config["ecp"].get(key, "")).strip()
        if value:
            ecp_result[key] = value
    base_url = str(config["ecp"].get("base_url", "")).strip()
    if base_url and base_url != str(DEFAULT_CONFIG["ecp"]["base_url"]):
        ecp_result["base_url"] = base_url
    if ecp_result:
        result["ecp"] = ecp_result

    timereport_result: dict[str, Any] = {}
    for key, default_value in DEFAULT_CONFIG["timereport"].items():
        value = config["timereport"].get(key)
        if value in (None, "", []):
            continue
        if value != default_value:
            timereport_result[key] = value
    if timereport_result:
        result["timereport"] = timereport_result
    return result


def write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = with_device_binding(config)
    config["device_binding"] = dict(stamped["device_binding"])
    compacted = compact_config(stamped)
    path.write_text(json.dumps(compacted, ensure_ascii=False, indent=2), encoding="utf-8")


def masked(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 3:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 3)}{value[-1]}"


def print_summary(path: Path, config: dict[str, Any]) -> None:
    summary = {
        "config_path": str(path),
        "projects": config["projects"],
        "ecp": {
            "base_url": config["ecp"]["base_url"],
            "username": config["ecp"]["username"],
            "password": masked(config["ecp"]["password"]),
        },
        "device_binding": dict(config.get("device_binding") or {}),
        "timereport": config["timereport"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)

    changed = apply_cli_updates(config, args)
    if args.interactive or (not changed and not args.show):
        config = interactive_update(config)
        changed = True

    if changed:
        write_config(config_path, config)
        print(f"Configuration saved: {config_path}")

    if args.show or changed:
        print_summary(config_path, config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
