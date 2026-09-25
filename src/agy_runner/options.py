"""Plugin options (CLAUDE_PLUGIN_OPTION_*), durations and small shared helpers."""

from __future__ import annotations

import os
import re

OFF_WORDS = frozenset({"off", "false", "0", "no", "disabled"})

LOCKED_MODEL_DEFAULT = "Gemini 3.8 Flash (High)"
TIER_DEFAULTS = {
    "flash": ("TIER_FLASH", "Gemini 3.8 Flash (High)"),
    "flash-lo": ("TIER_FLASH_LO", "Gemini 3.8 Flash (Low)"),
    "pro": ("TIER_PRO", "Gemini 3.1 Pro (High)"),
}


def option(name: str, default: str = "") -> str:
    """A plugin userConfig value; Claude Code exports it as CLAUDE_PLUGIN_OPTION_<NAME>."""
    return os.environ.get("CLAUDE_PLUGIN_OPTION_" + name, default)


def option_on(name: str, default: str = "on") -> bool:
    """A boolean-ish option: anything but off/false/0/no/disabled counts as on."""
    return option(name, default).strip().lower() not in OFF_WORDS


_DURATION = re.compile(r"^(\d+)([smh]?)$")


def duration_seconds(text: str, default: int = 300) -> int:
    """agy-style duration (300, 300s, 5m, 1h) to seconds; a bad value falls back to default."""
    m = _DURATION.match(text.strip())
    if not m:
        return default
    n, unit = int(m.group(1)), m.group(2)
    return n * {"h": 3600, "m": 60}.get(unit, 1)


def valid_duration(text: str) -> bool:
    return bool(_DURATION.match(text.strip()))


def outer_timeout_seconds(timeout: str) -> int:
    """The wall-clock guard: agy's own --print-timeout plus head-room, so the guard fires
    only when agy hangs past its own limit. +25% of the budget, at least 10 s, at most 120 s."""
    secs = duration_seconds(timeout)
    pad = min(max(secs // 4, 10), 120)
    return secs + pad


def model_for_tier(tier: str) -> str:
    key, default = TIER_DEFAULTS[tier]
    return option(key, default)


def on_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def usage_log_path() -> str:
    return os.environ.get("AGY_USAGE_LOG") or option("USAGE_LOG")


def tee_usage(line: str) -> None:
    """Append a machine-readable line to the usage log, if one is set. Never fatal:
    measurement must not break work. (A named file survives `2>&1 | tail -N`, stderr
    does not; a benchmark once lost most of its cost data that way.)"""
    path = usage_log_path()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
