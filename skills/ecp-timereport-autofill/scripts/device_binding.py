#!/usr/bin/env python3
"""Device binding helpers for the timereport skill."""

from __future__ import annotations

import getpass
import hashlib
import platform
import socket
import uuid
from typing import Any

FINGERPRINT_VERSION = 1
REQUIRED_CONFIG_KEYS = [
    "projects",
    "ecp.username",
    "ecp.password",
]


def clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def current_device_fingerprint() -> str:
    raw_parts = [
        str(FINGERPRINT_VERSION),
        platform.system(),
        platform.release(),
        platform.machine(),
        socket.gethostname(),
        str(uuid.getnode()),
        getpass.getuser(),
    ]
    payload = "|".join(raw_parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_device_binding(reason: str | None = None) -> dict[str, Any]:
    binding = {
        "fingerprint_version": FINGERPRINT_VERSION,
        "fingerprint": current_device_fingerprint(),
        "hostname": socket.gethostname(),
    }
    reason_text = clean_string(reason)
    if reason_text:
        binding["reset_reason"] = reason_text
    return binding


def with_device_binding(config: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
    result = dict(config)
    result["device_binding"] = build_device_binding(reason=reason)
    return result


def build_cleared_config(reason: str | None = None) -> dict[str, Any]:
    return {
        "device_binding": build_device_binding(reason=reason),
        "projects": [],
        "ecp": {
            "username": "",
            "password": "",
        },
    }


def detect_binding_issue(raw_config: dict[str, Any]) -> str | None:
    binding = raw_config.get("device_binding") if isinstance(raw_config.get("device_binding"), dict) else {}
    stored_fingerprint = clean_string(binding.get("fingerprint"))
    if not stored_fingerprint:
        return "missing_fingerprint"
    if stored_fingerprint != current_device_fingerprint():
        return "fingerprint_mismatch"
    return None


def missing_required_values(raw_config: dict[str, Any]) -> list[str]:
    missing: list[str] = []

    projects = raw_config.get("projects")
    if not isinstance(projects, list) or not projects:
        missing.append("projects")

    ecp = raw_config.get("ecp") if isinstance(raw_config.get("ecp"), dict) else {}
    if not clean_string(ecp.get("username")):
        missing.append("ecp.username")
    if not clean_string(ecp.get("password")):
        missing.append("ecp.password")
    return missing
