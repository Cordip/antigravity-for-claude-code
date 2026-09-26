"""agy-delegate and agy-job against a fake agy speaking stream-json."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from conftest import alive, run


def pids(env: dict[str, str]) -> list[int]:
    p = Path(env["FAKE_AGY_PIDS"])
    return [int(x) for x in p.read_text().split()] if p.exists() else []


def test_reply_on_stdout_usage_on_stderr(env: dict[str, str], tmp_path: Path) -> None:
    events = tmp_path / "events.ndjson"
    progress = tmp_path / "progress.json"
    r = run({**env, "AGY_EVENTS_FILE": str(events), "AGY_PROGRESS_FILE": str(progress)},
            "delegate", "do it")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "DONE\n"
    usage = next(line for line in r.stderr.splitlines() if line.startswith("AGY_USAGE "))
    meta = json.loads(usage[len("AGY_USAGE "):])
    assert meta["conversation_id"] == "conv-123"
    assert meta["usage"]["total"] == 125
    assert meta["isolation"] == "off"
    # The raw stream is kept for the job, and progress counted every step once.
    assert events.read_text().count('"event"') == 5
    prog = json.loads(progress.read_text())
    assert prog["steps"] == 3
    assert prog["tools"] == {"run_command": 2, "view_file": 1}


def test_structured_error_classifies(env: dict[str, str]) -> None:
    r = run({**env, "FAKE_AGY_MODE": "error"}, "delegate", "x")
    assert r.returncode == 10
    assert "QUOTA_EXHAUSTED" in r.stderr


def test_too_old_agy_is_refused(env: dict[str, str]) -> None:
    r = run({**env, "FAKE_AGY_VERSION": "1.1.28"}, "delegate", "x")
    assert r.returncode == 13
    assert "needs agy >= 1.2" in r.stderr


def test_idle_timeout_kills_the_whole_tree(env: dict[str, str]) -> None:
    r = run({**env, "FAKE_AGY_MODE": "hang", "CLAUDE_PLUGIN_OPTION_IDLE_TIMEOUT": "3s"},
            "delegate", "--timeout", "5m", "x", timeout=60)
    assert r.returncode == 12
    assert "sent no events for 3s" in r.stderr
    assert "TIMEOUT" in r.stderr
    time.sleep(0.5)
    assert pids(env) and not any(alive(p) for p in pids(env))


def test_wall_clock_guard_fires_on_a_hang(env: dict[str, str]) -> None:
    # --timeout 1s: agy's own limit is 1 s, the guard adds its 10 s minimum.
    start = time.monotonic()
    r = run({**env, "FAKE_AGY_MODE": "hang", "CLAUDE_PLUGIN_OPTION_IDLE_TIMEOUT": "0"},
            "delegate", "--timeout", "1s", "x", timeout=60)
    assert r.returncode == 12
    assert "wall-clock guard (11s)" in r.stderr
    assert time.monotonic() - start < 40
    time.sleep(0.5)
    assert not any(alive(p) for p in pids(env))


def test_sigterm_stops_agy(env: dict[str, str]) -> None:
    proc = subprocess.Popen([sys.executable, "-m", "agy_runner", "delegate", "x"],
                            env={**env, "FAKE_AGY_MODE": "hang"}, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 20
    while len(pids(env)) < 2 and time.monotonic() < deadline:
        time.sleep(0.2)
    proc.send_signal(signal.SIGTERM)
    _, err = proc.communicate(timeout=30)
    assert proc.returncode == 130
    assert "interrupted" in err
    time.sleep(0.5)
    assert not any(alive(p) for p in pids(env))


def test_job_status_shows_live_progress(env: dict[str, str], tmp_path: Path) -> None:
    e = {**env, "FAKE_AGY_MODE": "slow", "FAKE_AGY_STEPS": "12"}
    r = run(e, "job", "start", "slow task", cwd=str(tmp_path))
    job = r.stdout.strip()
    deadline = time.monotonic() + 20
    status = ""
    while time.monotonic() < deadline:
        status = run(e, "job", "status", job).stdout
        if "run_command x" in status:
            break
        time.sleep(0.3)
    assert "state=running" in status
    assert "progress=" in status and "run_command x" in status
    w = run(e, "job", "wait", job, timeout=60)
    assert "SLOW DONE" in w.stdout
    assert "[exit rc=0: ok]" in w.stderr
    assert "conversation=conv-123" in run(e, "job", "status", job).stdout


def test_second_wait_on_a_job_refuses(env: dict[str, str], tmp_path: Path) -> None:
    e = {**env, "FAKE_AGY_MODE": "slow", "FAKE_AGY_STEPS": "8"}
    job = run(e, "job", "start", "slow task", cwd=str(tmp_path)).stdout.strip()
    first = subprocess.Popen([sys.executable, "-m", "agy_runner", "job", "wait", job], env=e,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    waiter = Path(e["ANTIGRAVITY_JOBS"]) / job / "waiter"
    deadline = time.monotonic() + 10
    while not waiter.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    second = run(e, "job", "wait", job)
    assert second.returncode == 3
    assert "already running" in second.stdout
    out, err = first.communicate(timeout=60)
    assert first.returncode == 0 and "SLOW DONE" in out and "[exit rc=0: ok]" in err
    assert not waiter.exists()
    # Once the first wait is gone, waiting (or fetching the result) works again.
    assert "SLOW DONE" in run(e, "job", "wait", job).stdout


def test_job_cancel_stops_agy_and_its_children(env: dict[str, str], tmp_path: Path) -> None:
    e = {**env, "FAKE_AGY_MODE": "hang"}
    job = run(e, "job", "start", "hang", cwd=str(tmp_path)).stdout.strip()
    deadline = time.monotonic() + 20
    while len(pids(env)) < 2 and time.monotonic() < deadline:
        time.sleep(0.2)
    assert len(pids(env)) == 2
    c = run(e, "job", "cancel", job)
    assert c.stdout.startswith("cancelled")
    time.sleep(0.5)
    assert not any(alive(p) for p in pids(env))
    s = run(e, "job", "status", job).stdout
    assert "state=failed" in s and "CANCELLED" in s


def test_resume_uses_the_conversation_from_progress(env: dict[str, str],
                                                    tmp_path: Path) -> None:
    # A job cut off before agy's result still knows its conversation (from progress.json).
    e = {**env, "FAKE_AGY_MODE": "hang"}
    job = run(e, "job", "start", "hang", cwd=str(tmp_path)).stdout.strip()
    deadline = time.monotonic() + 20
    jd = Path(env["ANTIGRAVITY_JOBS"]) / job
    while time.monotonic() < deadline:
        if (jd / "progress.json").exists() and "conv-123" in (jd / "progress.json").read_text():
            break
        time.sleep(0.2)
    run(e, "job", "cancel", job)
    r = run({**env, "FAKE_AGY_MODE": "ok"}, "job", "start", "--resume", job,
            "--print-command", "follow up", cwd=str(tmp_path))
    fid = r.stdout.strip()
    out = run(env, "job", "wait", fid).stdout
    assert "--conversation conv-123" in out
    assert os.path.exists(Path(env["ANTIGRAVITY_JOBS"]) / fid / "meta")


def test_replayed_print_timeout_is_exit_12_with_git_status_hint(env: dict[str, str]) -> None:
    from conftest import FIXTURES

    r = run({**env, "FAKE_AGY_MODE": "replay",
             "FAKE_AGY_REPLAY": str(FIXTURES / "agy-1.2.11-print-timeout.ndjson"),
             "FAKE_AGY_STDERR": "[agy] print timeout after 15s with turn in progress; "
                                "returning partial output"},
            "delegate", "--timeout", "15s", "x")
    assert r.returncode == 12
    assert "NO reply text" in r.stderr and "check git status" in r.stderr
    assert '"total": 13275' in r.stderr  # AGY_USAGE still printed


def test_replayed_tool_error_feeds_the_readonly_hint(env: dict[str, str], tmp_path: Path) -> None:
    """A write refused by the jail shows up as a tool error; the wrapper names the path."""
    from conftest import FIXTURES

    home = tmp_path / "home"
    (home / "projects").mkdir(parents=True)
    fixture = (FIXTURES / "agy-1.2.11-tools.ndjson").read_text().replace("/home/user", str(home))
    rec = tmp_path / "rec.ndjson"
    rec.write_text(fixture)
    # The hint is for the workspace jail; run the jail's logic with a stub bwrap.
    stub = tmp_path / "bin" / "bwrap"
    stub.write_text('#!/bin/sh\nwhile [ "$1" != "--die-with-parent" ]; do shift; done\n'
                    'shift; exec "$@"\n')
    stub.chmod(0o755)
    e = {**env, "HOME": str(home), "FAKE_AGY_MODE": "replay", "FAKE_AGY_REPLAY": str(rec),
         "CLAUDE_PLUGIN_OPTION_ISOLATION": "workspace"}
    r = run(e, "delegate", "x", cwd=str(home / "projects"))
    assert r.returncode == 0, r.stderr
    assert "isolation_writable" in r.stderr
    assert f"{home}/projects/field-length/README-probe.md" in r.stderr
