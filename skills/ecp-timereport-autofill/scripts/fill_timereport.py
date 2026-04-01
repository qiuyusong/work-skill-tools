#!/usr/bin/env python3
"""Auto-fill ECP timereport from release branch commits with CN workday rules."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from device_binding import build_cleared_config, detect_binding_issue, missing_required_values

DEFAULT_BRANCH = "release"
DEFAULT_HOURS = 8.0
DEFAULT_ACTIVITY_TYPE = "產品研發"
DEFAULT_START_HOUR = 9
DEFAULT_ECP_BASE_URL = "https://econtact.gemfor.com.tw/ecp"
DEFAULT_REPOS: list[Path] = []
DEFAULT_NEARBY_DAYS = 7
DEFAULT_FUZZY_DESCRIPTIONS = [
    "优化相关代码",
    "调整了相关业务逻辑",
    "配合前端调整相关接口",
]

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = SKILL_ROOT / "config" / "timereport-config.json"
CONFIGURE_SCRIPT_PATH = SKILL_ROOT / "scripts" / "configure_timereport.py"


@dataclass
class CommitItem:
    repo_name: str
    summary: str
    short_hash: str
    committed_at: int


@dataclass
class RepoTarget:
    path: Path
    display_name: str


@dataclass
class TimeEntry:
    repo_name: str
    hours: float
    description: str
    work_description: str
    start_at: str
    end_at: str
    commits: list[CommitItem]
    activity_type: str
    task_id: str | None = None


@dataclass
class DailyPlan:
    date_value: dt.date
    source: str
    entries: list[TimeEntry]


@dataclass
class SkillConfig:
    repos: list[RepoTarget]
    ecp_base_url: str | None
    ecp_username: str | None
    ecp_password: str | None
    ecp_project_name: str | None
    total_hours: float | None
    git_author: str | None
    activity_type: str | None
    output_dir: str | None
    ecp_language: str | None
    nearby_days: int | None
    fuzzy_descriptions: list[str]


@dataclass
class SubmissionHistory:
    used_hashes: set[str]
    used_descriptions: set[str]


ACTIVITY_TYPE_ALIASES = {
    "產品研發": "產品研發",
    "产品研发": "產品研發",
    "產品研发": "產品研發",
    "会議": "會議",
    "會議": "會議",
    "会议": "會議",
    "休假": "休假",
    "其他": "其他",
}

MANUAL_DESCRIPTION_PREFIX = {
    "休假": "休假",
    "會議": "会议",
}


def clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def first_non_empty(*values: Any) -> str | None:
    for value in values:
        text = clean_string(value)
        if text:
            return text
    return None


def normalize_activity_type(value: Any) -> str:
    text = clean_string(value) or DEFAULT_ACTIVITY_TYPE
    return ACTIVITY_TYPE_ALIASES.get(text, text)


def requires_manual_activity_detail(activity_type: str) -> bool:
    return activity_type in {"休假", "會議"}


def activity_uses_task(activity_type: str) -> bool:
    return activity_type != "休假"


def format_manual_work_description(activity_type: str, detail: str) -> str:
    prefix = MANUAL_DESCRIPTION_PREFIX.get(activity_type)
    clean_detail = clean_string(detail)
    if not clean_detail:
        raise SystemExit(f"--activity-detail is required for activity type '{activity_type}'.")
    if not prefix:
        return clean_detail
    return f"{prefix}-{clean_detail}"


def sort_commits(commits: list[CommitItem]) -> list[CommitItem]:
    return sorted(commits, key=lambda item: (item.committed_at, item.repo_name, item.short_hash))


def build_repo_target(path_value: str, display_name: str | None = None) -> RepoTarget:
    path = Path(path_value).expanduser().resolve()
    name = clean_string(display_name) or path.name
    return RepoTarget(path=path, display_name=name)


def parse_repo_values(raw: Any) -> list[RepoTarget]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(";") if p.strip()]
        return [build_repo_target(p) for p in parts]
    if not isinstance(raw, list):
        raise SystemExit("Invalid config field 'repos'.")

    results: list[RepoTarget] = []
    for item in raw:
        if isinstance(item, dict):
            path_value = clean_string(item.get("path"))
            if not path_value:
                continue
            results.append(build_repo_target(path_value, clean_string(item.get("name"))))
            continue
        path_value = clean_string(item)
        if path_value:
            results.append(build_repo_target(path_value))
    return results


def parse_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(";") if p.strip()]
    if isinstance(raw, list):
        return [str(p).strip() for p in raw if str(p).strip()]
    return []


def parse_opt_float(raw: Any) -> float | None:
    value = clean_string(raw)
    if value is None:
        return None
    return float(value)


def parse_opt_int(raw: Any) -> int | None:
    value = clean_string(raw)
    if value is None:
        return None
    number = int(value)
    if number <= 0:
        raise SystemExit("nearby_days must be positive.")
    return number


def load_skill_config(path: Path) -> SkillConfig:
    if not path.exists():
        return SkillConfig([], None, None, None, None, None, None, None, None, None, None, [])
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid config structure: {path}")
    ecp = data.get("ecp") if isinstance(data.get("ecp"), dict) else {}
    timereport = data.get("timereport") if isinstance(data.get("timereport"), dict) else {}
    projects = parse_repo_values(data.get("projects"))
    repos = parse_repo_values(data.get("repos"))
    return SkillConfig(
        repos=projects or repos,
        ecp_base_url=clean_string(ecp.get("base_url")),
        ecp_username=clean_string(ecp.get("username")),
        ecp_password=clean_string(ecp.get("password")),
        ecp_project_name=clean_string(ecp.get("project_name")),
        total_hours=parse_opt_float(timereport.get("total_hours")),
        git_author=clean_string(timereport.get("git_author")),
        activity_type=clean_string(ecp.get("activity_type")),
        output_dir=clean_string(timereport.get("output_dir")),
        ecp_language=clean_string(ecp.get("language")),
        nearby_days=parse_opt_int(timereport.get("nearby_days")),
        fuzzy_descriptions=parse_string_list(timereport.get("fuzzy_descriptions")),
    )


def load_raw_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid config structure: {path}")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_bound_config(config_path: Path) -> None:
    raw_config = load_raw_config(config_path)
    issue = detect_binding_issue(raw_config)
    if issue:
        reason_map = {
            "missing_fingerprint": "device fingerprint missing",
            "fingerprint_mismatch": "device fingerprint mismatch",
        }
        write_json(config_path, build_cleared_config(reason=reason_map[issue]))
        raise SystemExit(
            f"Timereport config was cleared because {reason_map[issue]}. "
            f"Please ask the user to provide values for projects, ecp.username, and ecp.password, "
            f"then run python {CONFIGURE_SCRIPT_PATH} --interactive."
        )

    missing_values = missing_required_values(raw_config)
    if missing_values:
        raise SystemExit(
            f"Timereport config is incomplete. Missing: {', '.join(missing_values)}. "
            f"Please ask the user to provide those values, then run python {CONFIGURE_SCRIPT_PATH} --interactive."
        )


def resolve_config_path(argv: list[str] | None = None) -> tuple[Path, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    known, remaining = parser.parse_known_args(argv)
    return Path(known.config).expanduser().resolve(), remaining


def parse_args(config: SkillConfig, config_path: Path, argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and optionally submit ECP timereport entries.")
    parser.add_argument("--config", default=str(config_path))
    parser.add_argument("--date", help="Target date in YYYY-MM-DD format, default today.")
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--author", default=first_non_empty(config.git_author, os.environ.get("TIMEREPORT_GIT_AUTHOR"), config.ecp_username, os.environ.get("ECP_USERNAME")))
    parser.add_argument("--hours", type=float, default=config.total_hours if config.total_hours is not None else float(first_non_empty(os.environ.get("TIMEREPORT_TOTAL_HOURS"), DEFAULT_HOURS)))
    parser.add_argument("--activity-type", default=first_non_empty(config.activity_type, os.environ.get("ECP_ACTIVITY_TYPE"), DEFAULT_ACTIVITY_TYPE))
    parser.add_argument("--activity-detail", help="Manual activity detail, used for leave/meeting descriptions.")
    parser.add_argument("--project-name", default=config.ecp_project_name)
    parser.add_argument("--ecp-url", default=first_non_empty(config.ecp_base_url, os.environ.get("ECP_BASE_URL"), DEFAULT_ECP_BASE_URL))
    parser.add_argument("--ecp-username", default=first_non_empty(config.ecp_username, os.environ.get("ECP_USERNAME")))
    parser.add_argument("--ecp-password", default=first_non_empty(config.ecp_password, os.environ.get("ECP_PASSWORD")))
    parser.add_argument("--ecp-language", default=first_non_empty(config.ecp_language, os.environ.get("ECP_LANGUAGE"), "zh-cn"))
    parser.add_argument("--nearby-days", type=int, default=config.nearby_days if config.nearby_days is not None else DEFAULT_NEARBY_DAYS)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--output-dir", default=first_non_empty(config.output_dir, os.environ.get("TIMEREPORT_OUTPUT_DIR"), "timereport-reports"))
    args = parser.parse_args(argv)
    args.activity_type = normalize_activity_type(args.activity_type)
    args.activity_detail = clean_string(args.activity_detail)
    if args.hours <= 0:
        raise SystemExit("--hours must be positive.")
    if args.nearby_days <= 0:
        raise SystemExit("--nearby-days must be positive.")
    if requires_manual_activity_detail(args.activity_type) and not args.activity_detail:
        raise SystemExit(f"--activity-detail is required for activity type '{args.activity_type}'.")
    return args

def parse_date(value: str | None) -> dt.date:
    if not value:
        return dt.date.today()
    return dt.date.fromisoformat(value)


def ensure_current_month(target_date: dt.date, today: dt.date) -> None:
    if (target_date.year, target_date.month) != (today.year, today.month):
        raise SystemExit(
            f"Target date {target_date.isoformat()} is not in current month {today.strftime('%Y-%m')}. "
            "Please switch to current-month task and retry."
        )


def is_cn_workday(target_date: dt.date) -> bool:
    try:
        import chinese_calendar as cc  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            f"Missing dependency chinese_calendar. Run `{sys.executable} -m pip install chinesecalendar`."
        ) from exc
    return bool(cc.is_workday(target_date))


def is_last_day_of_month(target_date: dt.date) -> bool:
    return (target_date + dt.timedelta(days=1)).month != target_date.month


def default_repos(config: SkillConfig) -> list[RepoTarget]:
    if config.repos:
        return config.repos
    from_env = first_non_empty(os.environ.get("ECP_TIMEREPORT_REPOS", ""))
    if from_env:
        return [build_repo_target(p.strip()) for p in from_env.split(";") if p.strip()]
    return [build_repo_target(str(repo.resolve())) for repo in DEFAULT_REPOS]


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8")


def resolve_branch(repo: Path, branch: str) -> str:
    for candidate in (branch, f"origin/{branch}"):
        result = run_command(["git", "-C", str(repo), "rev-parse", "--verify", candidate])
        if result.returncode == 0:
            return candidate
    raise SystemExit(f"Branch '{branch}' not found in {repo}")


def collect_commits(repo: RepoTarget, branch: str, target_date: dt.date, author: str | None) -> list[CommitItem]:
    if not repo.path.exists():
        raise SystemExit(f"Repo not found: {repo.path}")
    branch_ref = resolve_branch(repo.path, branch)
    command = [
        "git", "-C", str(repo.path), "log", branch_ref,
        "--since", f"{target_date.isoformat()} 00:00:00",
        "--until", f"{target_date.isoformat()} 23:59:59",
        "--reverse",
        "--pretty=format:%ct%x09%h%x09%s",
    ]
    if author:
        command.extend(["--author", author])
    result = run_command(command)
    if result.returncode != 0:
        raise SystemExit(f"git log failed in {repo.path}: {result.stderr.strip()}")
    commits: list[CommitItem] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        committed_at, short_hash, summary = parts
        commits.append(CommitItem(
            repo_name=repo.display_name,
            summary=summary.strip(),
            short_hash=short_hash.strip(),
            committed_at=int(committed_at.strip()),
        ))
    return sort_commits(commits)


def truncate_text(text: str, max_len: int = 200) -> str:
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def split_hours(total_hours: float, count: int) -> list[float]:
    units = int(round(total_hours * 10))
    base, remainder = divmod(units, count)
    values = [base / 10.0 for _ in range(count)]
    for idx in range(remainder):
        values[idx] += 0.1
    return values


def build_time_range(target_date: dt.date, hours: float) -> tuple[str, str]:
    start_time = dt.datetime(target_date.year, target_date.month, target_date.day, DEFAULT_START_HOUR, 0, 0)
    end_time = start_time + dt.timedelta(minutes=int(round(hours * 60)))
    return start_time.strftime("%Y-%m-%d %H:%M:%S"), end_time.strftime("%Y-%m-%d %H:%M:%S")


def build_single_entry(
    target_date: dt.date,
    hours: float,
    repo_name: str,
    work_desc: str,
    commits: list[CommitItem],
    activity_type: str,
    task_id: str | None = None,
) -> TimeEntry:
    start_at, end_at = build_time_range(target_date, hours)
    work_desc = clean_string(work_desc) or "优化相关代码"
    return TimeEntry(
        repo_name=repo_name,
        hours=hours,
        description=truncate_text(work_desc),
        work_description=work_desc,
        start_at=start_at,
        end_at=end_at,
        commits=commits,
        activity_type=activity_type,
        task_id=task_id,
    )


def build_manual_entry(target_date: dt.date, hours: float, activity_type: str, activity_detail: str) -> TimeEntry:
    work_desc = format_manual_work_description(activity_type, activity_detail)
    manual_task_id = None if activity_uses_task(activity_type) else ""
    return build_single_entry(
        target_date=target_date,
        hours=hours,
        repo_name="manual",
        work_desc=work_desc,
        commits=[],
        activity_type=activity_type,
        task_id=manual_task_id,
    )


def build_direct_entries(
    target_date: dt.date,
    commits_by_repo: dict[str, list[CommitItem]],
    total_hours: float,
    activity_type: str,
) -> list[TimeEntry]:
    all_commits = sort_commits([commit for commits in commits_by_repo.values() for commit in commits])
    if not all_commits:
        return []
    earliest_commit = all_commits[0]
    return [build_single_entry(
        target_date=target_date,
        hours=total_hours,
        repo_name=earliest_commit.repo_name,
        work_desc=earliest_commit.summary,
        commits=[earliest_commit],
        activity_type=activity_type,
    )]


def iterate_dates(start_date: dt.date, end_date: dt.date) -> list[dt.date]:
    dates: list[dt.date] = []
    cursor = start_date
    while cursor <= end_date:
        dates.append(cursor)
        cursor += dt.timedelta(days=1)
    return dates


def extract_existing_descriptions(details: list[dict[str, Any]]) -> set[str]:
    descriptions: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"WorkDescription", "workDescription", "description"}:
                    text = clean_string(value)
                    if text:
                        descriptions.add(text)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(details)
    return descriptions


def parse_hours_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def extract_detail_hours(detail: Any) -> float | None:
    if isinstance(detail, dict):
        for key in ("workHours", "workTime", "actualWorktime"):
            hours = parse_hours_value(detail.get(key))
            if hours is not None:
                return hours
        nested_total = 0.0
        found_nested = False
        for value in detail.values():
            hours = extract_detail_hours(value)
            if hours is None:
                continue
            nested_total += hours
            found_nested = True
        if found_nested:
            return nested_total
        return None
    if isinstance(detail, list):
        nested_total = 0.0
        found_nested = False
        for item in detail:
            hours = extract_detail_hours(item)
            if hours is None:
                continue
            nested_total += hours
            found_nested = True
        if found_nested:
            return nested_total
    return None


def extract_existing_total_hours(details: list[dict[str, Any]]) -> float:
    total = 0.0
    for detail in details:
        hours = extract_detail_hours(detail)
        if hours is None:
            continue
        total += hours
    return round(total, 1)


def calculate_main_entity_hours(
    entries: list[TimeEntry],
    existing_details: list[dict[str, Any]],
    append_mode: bool,
    keep_existing_total: bool = False,
) -> float:
    if keep_existing_total and existing_details:
        return extract_existing_total_hours(existing_details)
    total = round(sum(entry.hours for entry in entries), 1)
    if append_mode and existing_details:
        total += extract_existing_total_hours(existing_details)
    return round(total, 1)


def build_existing_detail_update(
    detail: dict[str, Any],
    new_hours: float,
    employee_id: str,
    date_value: dt.date,
) -> dict[str, Any]:
    detail_id = require_value(clean_string(detail.get("FId")), "ECP detail id", DEFAULT_CONFIG_PATH)
    task_id = clean_string(detail.get("FTaskId")) or ""
    activity_type = normalize_activity_type(detail.get("FType"))
    work_desc = require_value(clean_string(detail.get("FWorkDescription")), "ECP detail description", DEFAULT_CONFIG_PATH)
    progress = parse_hours_value(detail.get("FProgress"))
    output_value = parse_hours_value(detail.get("FOutPutValue"))
    fname = clean_string(detail.get("FName")) or build_fname(clean_string(detail.get("FTaskName")) if task_id else None, activity_type, work_desc)
    return {
        "trpDetail": detail_id,
        "taskId": task_id,
        "type": activity_type,
        "workHours": f"{new_hours:.1f}",
        "progress": f"{int(progress)}" if progress is not None else "100",
        "outputValue": f"{(output_value or 0.0):.2f}",
        "description": work_desc,
        "fname": fname,
        "userId": employee_id,
        "date": date_to_iso_z(date_value),
    }


def plan_leave_deduction_mutations(
    existing_details: list[dict[str, Any]],
    leave_hours: float,
    employee_id: str,
    date_value: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    remaining = round(leave_hours, 1)
    updates: list[dict[str, Any]] = []
    deletions: list[dict[str, Any]] = []
    for detail in existing_details:
        task_id = clean_string(detail.get("FTaskId"))
        if not task_id:
            continue
        detail_hours = parse_hours_value(detail.get("FWorkTime"))
        if detail_hours is None or detail_hours <= 0:
            continue
        deduction = min(detail_hours, remaining)
        if deduction <= 0:
            continue
        new_hours = round(detail_hours - deduction, 1)
        if new_hours > 0:
            updates.append(build_existing_detail_update(detail, new_hours, employee_id, date_value))
        else:
            deletions.append(detail)
        remaining = round(remaining - deduction, 1)
        if remaining <= 0:
            break
    if remaining > 0:
        raise SystemExit(
            f"Leave hours exceed existing task-backed hours on {date_value.isoformat()}. "
            "Please adjust the day manually."
        )
    return updates, deletions


def load_submission_history(output_dir: Path, target_date: dt.date) -> SubmissionHistory:
    history = SubmissionHistory(used_hashes=set(), used_descriptions=set())
    if not output_dir.exists():
        return history

    for report_path in sorted(output_dir.glob("*-ecp-timereport.json")):
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        daily_reports = payload.get("daily_reports")
        if not isinstance(daily_reports, list):
            continue
        for daily_report in daily_reports:
            if not isinstance(daily_report, dict):
                continue
            date_text = clean_string(daily_report.get("date"))
            if not date_text:
                continue
            try:
                report_date = dt.date.fromisoformat(date_text)
            except ValueError:
                continue
            if (report_date.year, report_date.month) != (target_date.year, target_date.month):
                continue

            submit_result = daily_report.get("submit_result")
            if not isinstance(submit_result, dict) or submit_result.get("state") != "ok":
                continue

            entries = daily_report.get("entries")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                description = clean_string(entry.get("work_description")) or clean_string(entry.get("description"))
                if description:
                    history.used_descriptions.add(description)
                commits = entry.get("commits")
                if not isinstance(commits, list):
                    continue
                for commit in commits:
                    if not isinstance(commit, dict):
                        continue
                    short_hash = clean_string(commit.get("hash"))
                    if short_hash:
                        history.used_hashes.add(short_hash)
                    summary = clean_string(commit.get("summary"))
                    if summary:
                        history.used_descriptions.add(summary)

    return history


def remember_entry_descriptions(existing_descriptions: set[str], entries: list[TimeEntry]) -> None:
    for entry in entries:
        description = clean_string(entry.work_description) or clean_string(entry.description)
        if description:
            existing_descriptions.add(description)


def pick_nearby_commit(
    target_date: dt.date,
    search_end_date: dt.date,
    repos: list[RepoTarget],
    nearby_days: int,
    collect_day_commits: Any,
    existing_descriptions: set[str],
    used_hashes: set[str],
) -> tuple[CommitItem | None, dt.date | None]:
    candidates: list[tuple[int, dt.date, list[CommitItem]]] = []
    for offset in range(1, nearby_days + 1):
        for candidate in (target_date - dt.timedelta(days=offset), target_date + dt.timedelta(days=offset)):
            if candidate > search_end_date:
                continue
            if (candidate.year, candidate.month) != (target_date.year, target_date.month):
                continue
            day_commits: list[CommitItem] = []
            for repo in repos:
                day_commits.extend(collect_day_commits(repo, candidate))
            if len(day_commits) < 2:
                continue
            candidates.append((offset, candidate, sort_commits(day_commits)))
    candidates.sort(key=lambda x: (x[0], x[1]))
    for _, source_date, commits in candidates:
        for commit in commits:
            if commit.short_hash in used_hashes or commit.summary in existing_descriptions:
                continue
            return commit, source_date
    return None, None


def choose_fuzzy_description(target_date: dt.date, phrases: list[str], existing_descriptions: set[str]) -> str | None:
    if not phrases:
        return None
    start = (target_date.day - 1) % len(phrases)
    for offset in range(len(phrases)):
        phrase = phrases[(start + offset) % len(phrases)]
        if phrase not in existing_descriptions:
            return phrase
    return phrases[start]


def plan_entries_for_day(
    target_date: dt.date,
    repos: list[RepoTarget],
    args: argparse.Namespace,
    collect_day_commits: Any,
    existing_descriptions: set[str],
    used_hashes: set[str],
    fuzzy_descriptions: list[str],
    month_end_run: bool,
    search_end_date: dt.date,
) -> tuple[DailyPlan | None, str | None]:
    if args.activity_detail:
        entry = build_manual_entry(
            target_date=target_date,
            hours=args.hours,
            activity_type=args.activity_type,
            activity_detail=args.activity_detail,
        )
        return DailyPlan(target_date, f"manual:{args.activity_type}", [entry]), None

    commits_by_repo = {repo.display_name: collect_day_commits(repo, target_date) for repo in repos}
    direct_entries = build_direct_entries(target_date, commits_by_repo, args.hours, args.activity_type)
    if direct_entries:
        for entry in direct_entries:
            for commit in entry.commits:
                used_hashes.add(commit.short_hash)
        return DailyPlan(target_date, "direct", direct_entries), None

    nearby_commit, source_date = pick_nearby_commit(
        target_date=target_date,
        search_end_date=search_end_date,
        repos=repos,
        nearby_days=args.nearby_days,
        collect_day_commits=collect_day_commits,
        existing_descriptions=existing_descriptions,
        used_hashes=used_hashes,
    )
    if nearby_commit and source_date:
        used_hashes.add(nearby_commit.short_hash)
        entry = build_single_entry(
            target_date,
            args.hours,
            nearby_commit.repo_name,
            nearby_commit.summary,
            [nearby_commit],
            args.activity_type,
        )
        return DailyPlan(target_date, f"nearby:{source_date.isoformat()}", [entry]), None

    if month_end_run:
        phrase = choose_fuzzy_description(target_date, fuzzy_descriptions, existing_descriptions)
        if phrase:
            entry = build_single_entry(
                target_date,
                args.hours,
                "auto-generated",
                phrase,
                [],
                args.activity_type,
            )
            return DailyPlan(target_date, "month-end-fuzzy", [entry]), None

    return None, "No commits and no available fallback for this workday."

def require_value(value: str | None, label: str, config_path: Path) -> str:
    text = clean_string(value)
    if text:
        return text
    raise SystemExit(
        f"Missing {label}. Configure it in {config_path} or run: python {CONFIGURE_SCRIPT_PATH}"
    )


def select_month_task(
    task_items: list[dict[str, Any]],
    target_date: dt.date,
) -> tuple[str, str]:
    month_marker = f"{target_date.month}月"
    year_marker = f"{target_date.year}年"
    valid_items = [item for item in task_items if isinstance(item, dict)]
    if not valid_items:
        raise SystemExit("No ECP tasks found for current user. Please create current-month task first.")

    strict_matches: list[dict[str, Any]] = []
    loose_matches: list[dict[str, Any]] = []
    for item in valid_items:
        text = clean_string(item.get("text")) or ""
        value = clean_string(item.get("value"))
        if not value:
            continue
        if month_marker in text and year_marker in text:
            strict_matches.append(item)
        elif month_marker in text:
            loose_matches.append(item)

    candidates = strict_matches or loose_matches
    if not candidates:
        sample = [clean_string(item.get("text")) or "" for item in valid_items[:5]]
        raise SystemExit(
            f"No task matches current month ({target_date.year}-{target_date.month:02d}). "
            f"Available task examples: {sample}"
        )

    selected = candidates[0]
    task_id = clean_string(selected.get("value"))
    task_text = clean_string(selected.get("text")) or ""
    if not task_id:
        raise SystemExit("Matched ECP task missing value field.")
    return task_id, task_text


def date_to_iso_z(date_value: dt.date) -> str:
    return dt.datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        0,
        0,
        0,
        tzinfo=dt.timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_fname(project_name: str | None, activity_type: str, work_description: str) -> str:
    if project_name:
        return f"{project_name}:{activity_type}:{work_description}"
    return f"{activity_type}:{work_description}"


def plans_require_task(plans: list[DailyPlan]) -> bool:
    return any(
        activity_uses_task(entry.activity_type) and clean_string(entry.task_id) is None
        for plan in plans
        for entry in plan.entries
    )


class EcpClient:
    def __init__(self, base_url: str, username: str, password: str, language: str = "zh-cn") -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.language = language
        self.session = requests.Session()
        self.timeout = 30

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = urljoin(self.base_url, f"{endpoint}.data")
        response = self.session.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        if not response.text.strip():
            return {}
        data = response.json()
        if isinstance(data, dict) and data.get("errorMessage"):
            raise SystemExit(f"ECP API error ({endpoint}): {data['errorMessage']}")
        return data

    def login(self) -> None:
        self.session.get(
            urljoin(self.base_url, f"Qs.OnlineUser.Login.page?language={self.language}"),
            timeout=self.timeout,
        )
        key_data = self._post("Qs.Misc.getLoginPublicKey", {})
        public_key = key_data.get("publicKey", "")
        if not public_key:
            raise SystemExit("ECP login failed: no public key.")
        pem = public_key if "BEGIN PUBLIC KEY" in public_key else f"-----BEGIN PUBLIC KEY-----\n{public_key}\n-----END PUBLIC KEY-----"
        rsa_key = serialization.load_pem_public_key(pem.encode("utf-8"))
        encrypted_password = base64.b64encode(
            rsa_key.encrypt(self.password.encode("utf-8"), padding.PKCS1v15())
        ).decode("ascii")
        self._post("Qs.OnlineUser.login", {
            "loginName": self.username,
            "password": encrypted_password,
            "language": self.language,
            "checkRelogin": True,
            "extraArgs": None,
        })

    def get_current_employee(self) -> dict[str, Any]:
        result = self._post("Qs.OnlineUser.getCurrentUserInformation", {"type": "employee"})
        if not result.get("userId"):
            raise SystemExit("Cannot get current employee info.")
        return result

    def get_online_user(self) -> dict[str, Any]:
        result = self._post("Ecp.Aile.getOnlineUser", {})
        if not clean_string(result.get("userId")):
            raise SystemExit("ECP online user query failed: missing userId.")
        return result

    def get_all_relevant_tasks(self, user_id: str) -> list[dict[str, Any]]:
        result = self._post("Ecp.TimeReport.getAllRelevantObjs", {"userId": user_id})
        task_items = result.get("taskItems")
        if isinstance(task_items, list):
            return [item for item in task_items if isinstance(item, dict)]
        return []

    def get_activity_types(self) -> list[str]:
        result = self._post("Ecp.TimeReport.getActivityTypeItem", {})
        items = result.get("typeItems") or []
        return [item["text"] for item in items if isinstance(item, dict) and item.get("text")]

    def get_daily_details(self, date_value: dt.date) -> list[dict[str, Any]]:
        result = self._post("Ecp.TimeReport.getAllDetailDatas", {"dateTime": date_value.isoformat()})
        return result.get("datas") or []

    def update_existing_details(self, entity_id: str, detail_updates: list[dict[str, Any]]) -> dict[str, Any]:
        if not detail_updates:
            return {"state": "ok"}
        result = self._post("Ecp.TimeReport.addDetails", {"entityId": entity_id, "jsonData": [], "allDetails": detail_updates})
        if result.get("state") != "ok":
            raise SystemExit(f"ECP addDetails update failed: {result}")
        return result

    def delete_detail_rows(self, employee_id: str, date_value: dt.date, details: list[dict[str, Any]]) -> None:
        if not details:
            return
        task_ids = sorted({task_id for task_id in (clean_string(detail.get("FTaskId")) for detail in details) if task_id})
        if task_ids:
            self._post(
                "Ecp.TimeReport.doBackProgressWhereDeleteDetail",
                {"userId": employee_id, "date": date_value.isoformat(), "taskIds": task_ids, "needBack": 1},
            )
        for detail in details:
            detail_id = require_value(clean_string(detail.get("FId")), "ECP detail id", DEFAULT_CONFIG_PATH)
            result = self._post("Ecp.TimeReport.doDeleteForMecp", {"trpDetailId": detail_id})
            if not result.get("isSuccess"):
                raise SystemExit(f"ECP delete detail failed: {result}")

    def upsert_main_entity(self, employee_id: str, total_hours: float, total_value: float, date_value: dt.date) -> str:
        date_iso = date_to_iso_z(date_value)
        check_payload = {"userId": employee_id, "date": date_iso, "getLastRecordByuser": 1, "couldSave": 0}
        check_result = self._post("Ecp.TimeReport.addMainUnitEntity", check_payload)
        if check_result.get("whatTime") == 1:
            raise SystemExit("ECP blocks future date timereport.")

        save_payload = {
            "userId": employee_id,
            "actualWorktime": f"{total_hours:.1f}",
            "actualWorkvalue": f"{total_value:.2f}",
            "date": date_iso,
            "couldSave": 0,
        }
        save_result = self._post("Ecp.TimeReport.addMainUnitEntity", save_payload)
        if save_result.get("mainEntity") == 1:
            entity_ids = save_result.get("entityIds") or []
            if not entity_ids:
                raise SystemExit("ECP returned existing main entity without id.")
            save_payload["couldSave"] = 1
            self._post("Ecp.TimeReport.addMainUnitEntity", save_payload)
            return entity_ids[0]

        save_payload["couldSave"] = 1
        final_result = self._post("Ecp.TimeReport.addMainUnitEntity", save_payload)
        if final_result.get("state") != "ok":
            raise SystemExit(f"ECP addMainUnitEntity failed: {final_result}")
        entity_ids = final_result.get("entityIds") or []
        if not entity_ids:
            raise SystemExit("ECP created main entity but did not return entity id.")
        return entity_ids[0]

    def add_details(
        self,
        entity_id: str,
        employee_id: str,
        department_id: str,
        date_value: dt.date,
        default_activity_type: str,
        default_task_id: str | None,
        entries: list[TimeEntry],
        project_name: str | None = None,
    ) -> dict[str, Any]:
        date_iso = date_to_iso_z(date_value)
        json_data = []
        all_details = []
        for entry in entries:
            work_desc = clean_string(entry.work_description) or entry.description
            activity_type = normalize_activity_type(first_non_empty(entry.activity_type, default_activity_type))
            task_id = clean_string(entry.task_id)
            if not activity_uses_task(activity_type):
                task_id = ""
            elif task_id is None:
                task_id = clean_string(default_task_id) or ""
            fname = build_fname(project_name if task_id else None, activity_type, work_desc)
            json_data.append({
                "taskId": task_id,
                "type": activity_type,
                "workTime": f"{entry.hours:.1f}",
                "progress": "100",
                "outputValue": "0.00",
                "WorkDescription": work_desc,
                "fname": fname,
                "fdatetime": entry.start_at,
                "fenddatetime": entry.end_at,
                "userId": employee_id,
                "departmentId": department_id,
            })
            all_details.append({
                "trpDetail": "",
                "taskId": task_id,
                "type": activity_type,
                "workHours": f"{entry.hours:.1f}",
                "progress": "100",
                "outputValue": "0.00",
                "description": work_desc,
                "fname": fname,
                "userId": employee_id,
                "date": date_iso,
            })
        result = self._post("Ecp.TimeReport.addDetails", {"entityId": entity_id, "jsonData": json_data, "allDetails": all_details})
        if result.get("state") != "ok":
            raise SystemExit(f"ECP addDetails failed: {result}")
        return result


def serialize_entry(entry: TimeEntry) -> dict[str, Any]:
    return {
        "repo_name": entry.repo_name,
        "hours": entry.hours,
        "activity_type": entry.activity_type,
        "task_id": entry.task_id,
        "description": entry.description,
        "work_description": entry.work_description,
        "start_at": entry.start_at,
        "end_at": entry.end_at,
        "commits": [{"hash": c.short_hash, "summary": c.summary} for c in entry.commits],
    }


def write_report(
    output_dir: Path,
    run_date: dt.date,
    branch: str,
    repos: list[RepoTarget],
    plans: list[DailyPlan],
    skipped_dates: list[dict[str, str]],
    submit_results: dict[str, dict[str, Any]],
    project_name: str | None,
    task_id: str | None,
    config_path: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{run_date.isoformat()}-ecp-timereport.json"
    daily_reports = []
    for plan in plans:
        date_key = plan.date_value.isoformat()
        daily_reports.append({
            "date": date_key,
            "source": plan.source,
            "entries": [serialize_entry(e) for e in plan.entries],
            "submit_result": submit_results.get(date_key),
        })
    payload = {
        "run_date": run_date.isoformat(),
        "branch": branch,
        "project_name": project_name,
        "task_id": task_id,
        "config_path": str(config_path),
        "repositories": [{"name": repo.display_name, "path": str(repo.path)} for repo in repos],
        "daily_reports": daily_reports,
        "skipped_dates": skipped_dates,
        "summary": {
            "planned_days": len(plans),
            "submitted_days": len(submit_results),
            "skipped_days": len(skipped_dates),
        },
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path

def main(argv: list[str] | None = None) -> int:
    config_path, remaining_argv = resolve_config_path(argv)
    ensure_bound_config(config_path)
    config = load_skill_config(config_path)
    args = parse_args(config=config, config_path=config_path, argv=remaining_argv)

    run_date = parse_date(args.date)
    today = dt.date.today()
    ensure_current_month(run_date, today)

    repos = [build_repo_target(value) for value in args.repo] if args.repo else default_repos(config)
    if not repos:
        raise SystemExit("No repositories configured. Use --repo or update skill config.")

    fuzzy_descriptions = config.fuzzy_descriptions or DEFAULT_FUZZY_DESCRIPTIONS
    month_end_run = is_last_day_of_month(today)

    commit_cache: dict[tuple[str, dt.date], list[CommitItem]] = {}

    def collect_day_commits(repo: RepoTarget, date_value: dt.date) -> list[CommitItem]:
        key = (str(repo.path), date_value)
        if key not in commit_cache:
            commit_cache[key] = collect_commits(repo=repo, branch=args.branch, target_date=date_value, author=args.author)
        return commit_cache[key]

    plans: list[DailyPlan] = []
    skipped_dates: list[dict[str, str]] = []
    submit_results: dict[str, dict[str, Any]] = {}
    history = load_submission_history(Path(args.output_dir), run_date)
    used_hashes: set[str] = set(history.used_hashes)
    submitted_descriptions: set[str] = set(history.used_descriptions)

    if args.submit:
        ecp_username = require_value(args.ecp_username, "ECP username", config_path)
        ecp_password = require_value(args.ecp_password, "ECP password", config_path)
        activity_type = normalize_activity_type(require_value(args.activity_type, "ECP activity type", config_path))

        client = EcpClient(
            base_url=args.ecp_url,
            username=ecp_username,
            password=ecp_password,
            language=args.ecp_language,
        )
        client.login()
        employee = client.get_current_employee()
        activity_types = client.get_activity_types()
        if activity_type not in activity_types:
            raise SystemExit(f"Activity type '{activity_type}' not found. Valid values: {activity_types}")

        month_start = today.replace(day=1)
        daily_details_cache: dict[dt.date, list[dict[str, Any]]] = {}

        def get_daily_details(date_value: dt.date) -> list[dict[str, Any]]:
            if date_value not in daily_details_cache:
                daily_details_cache[date_value] = client.get_daily_details(date_value)
            return daily_details_cache[date_value]

        for date_value in iterate_dates(month_start, today):
            if not is_cn_workday(date_value):
                continue
            submitted_descriptions.update(extract_existing_descriptions(get_daily_details(date_value)))

        manual_activity_mode = bool(args.activity_detail)

        if run_date == today and not manual_activity_mode:
            for date_value in iterate_dates(month_start, today):
                if not is_cn_workday(date_value):
                    continue
                details = get_daily_details(date_value)
                if details and not args.allow_overwrite:
                    continue
                plan, reason = plan_entries_for_day(
                    target_date=date_value,
                    repos=repos,
                    args=args,
                    collect_day_commits=collect_day_commits,
                    existing_descriptions=submitted_descriptions,
                    used_hashes=used_hashes,
                    fuzzy_descriptions=fuzzy_descriptions,
                    month_end_run=month_end_run,
                    search_end_date=today,
                )
                if plan:
                    plans.append(plan)
                    remember_entry_descriptions(submitted_descriptions, plan.entries)
                else:
                    skipped_dates.append({"date": date_value.isoformat(), "reason": reason or "Skipped"})
        else:
            if not is_cn_workday(run_date):
                raise SystemExit(f"Date {run_date.isoformat()} is not a China workday.")
            details = get_daily_details(run_date)
            if details and not args.allow_overwrite:
                raise SystemExit(
                    f"Date {run_date.isoformat()} already has {len(details)} detail row(s). Use --allow-overwrite to continue."
                )
            plan, reason = plan_entries_for_day(
                target_date=run_date,
                repos=repos,
                args=args,
                collect_day_commits=collect_day_commits,
                existing_descriptions=submitted_descriptions,
                used_hashes=used_hashes,
                fuzzy_descriptions=fuzzy_descriptions,
                month_end_run=month_end_run,
                search_end_date=today,
            )
            if not plan:
                raise SystemExit(reason or "No fillable content found for target date.")
            plans.append(plan)
            remember_entry_descriptions(submitted_descriptions, plan.entries)

        task_id: str | None = None
        task_text: str | None = None
        if plans_require_task(plans):
            online_user = client.get_online_user()
            online_user_id = require_value(clean_string(online_user.get("userId")), "ECP online userId", config_path)
            task_items = client.get_all_relevant_tasks(online_user_id)
            task_id, task_text = select_month_task(task_items, run_date)

        for plan in plans:
            plan_details = get_daily_details(plan.date_value)
            leave_only_plan = all(normalize_activity_type(entry.activity_type) == "休假" for entry in plan.entries)
            leave_updates: list[dict[str, Any]] = []
            leave_deletions: list[dict[str, Any]] = []
            if manual_activity_mode and leave_only_plan:
                leave_hours = round(sum(entry.hours for entry in plan.entries), 1)
                leave_updates, leave_deletions = plan_leave_deduction_mutations(
                    existing_details=plan_details,
                    leave_hours=leave_hours,
                    employee_id=employee["userId"],
                    date_value=plan.date_value,
                )
            entity_id = client.upsert_main_entity(
                employee_id=employee["userId"],
                total_hours=calculate_main_entity_hours(
                    entries=plan.entries,
                    existing_details=plan_details,
                    append_mode=manual_activity_mode and not leave_only_plan,
                    keep_existing_total=manual_activity_mode and leave_only_plan,
                ),
                total_value=0.0,
                date_value=plan.date_value,
            )
            submit_results[plan.date_value.isoformat()] = client.add_details(
                entity_id=entity_id,
                employee_id=employee["userId"],
                department_id=employee["departmentId"],
                date_value=plan.date_value,
                default_activity_type=activity_type,
                default_task_id=task_id,
                entries=plan.entries,
                project_name=clean_string(args.project_name),
            )
            if leave_deletions:
                client.delete_detail_rows(
                    employee_id=employee["userId"],
                    date_value=plan.date_value,
                    details=leave_deletions,
                )
            if leave_updates:
                client.update_existing_details(entity_id=entity_id, detail_updates=leave_updates)

        report_path = write_report(
            output_dir=Path(args.output_dir),
            run_date=run_date,
            branch=args.branch,
            repos=repos,
            plans=plans,
            skipped_dates=skipped_dates,
            submit_results=submit_results,
            project_name=clean_string(args.project_name),
            task_id=f"{task_id} ({task_text})" if task_id and task_text else task_id,
            config_path=config_path,
        )
        print(f"Timereport report: {report_path}")
        if submit_results:
            print(f"ECP submit succeeded for {len(submit_results)} workday(s).")
        else:
            print("No workdays needed submission.")
        return 0

    if not is_cn_workday(run_date):
        raise SystemExit(f"Date {run_date.isoformat()} is not a China workday. Dry-run stopped.")

    plan, reason = plan_entries_for_day(
        target_date=run_date,
        repos=repos,
        args=args,
        collect_day_commits=collect_day_commits,
        existing_descriptions=set(),
        used_hashes=used_hashes,
        fuzzy_descriptions=fuzzy_descriptions,
        month_end_run=month_end_run,
        search_end_date=today,
    )
    if not plan:
        raise SystemExit(reason or "No fillable content found for target date.")

    report_path = write_report(
        output_dir=Path(args.output_dir),
        run_date=run_date,
        branch=args.branch,
        repos=repos,
        plans=[plan],
        skipped_dates=[],
        submit_results={},
        project_name=clean_string(args.project_name),
        task_id=None,
        config_path=config_path,
    )
    print(f"Timereport report: {report_path}")
    print("Dry-run only. Add --submit to write into ECP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
