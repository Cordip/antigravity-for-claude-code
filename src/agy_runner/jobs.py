"""agy-job: background jobs over agy-delegate, like Codex's background tasks.

Usage:
  agy-job start  [agy-delegate options] "task"   # -> prints a JOB_ID, returns now
  agy-job start  --resume <id> [options] "task"   # follow-up in job <id>'s own agy
                                                  # conversation (--conversation), not
                                                  # agy's most recent one (--continue)
  agy-job list                                    # jobs started from this dir (ALL=1: all)
  agy-job status <id>                             # state, live progress, conversation
  agy-job result <id>                             # print the reply (+rc) when finished
  agy-job wait   <id> [--timeout <dur>]           # block until finished, then = result;
                                                  # --timeout (e.g. 9m) gives up with
                                                  # "still running", exit 2. Run it as a
                                                  # background Bash command and wait for
                                                  # its exit notification.
  agy-job cancel <id>                             # stop a running job (agy included)

Jobs live under ${ANTIGRAVITY_JOBS:-~/.antigravity-jobs}/<id>/: meta, pid, out, err, rc,
events.ndjson (agy's raw event stream) and progress.json. A job runs with --timeout 30m
unless the args name one (plugin option job_timeout, or env AGY_JOB_TIMEOUT).

Several jobs may run at once in one checkout, as with Codex's background tasks: nothing
here isolates them, so parallel WRITE jobs must work on separate files (the caller's job).
`start` says how many other jobs are running in the same directory.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import NoReturn

from .options import duration_seconds, option, valid_duration

RC_LABELS = {
    "0": "ok",
    "2": "agy failed",
    "3": "empty output",
    "10": "QUOTA — retry later with --continue",
    "11": "AUTH required — run `agy` once interactively",
    "12": "TIMEOUT — the reply may be empty and files may already be changed (check git "
          "status); agy-job start --resume <this id> continues the conversation, or raise "
          "--timeout",
    "13": "agy MISSING or older than 1.2 — install / update the Antigravity CLI",
    "14": "MODEL unavailable — check `agy models` / tier remap",
    "15": "PERMISSION denied (headless, isolation off) — add a permissions.allow rule, or "
          "--yolo",
    "16": "ISOLATION unavailable — install bubblewrap (Linux), run from the project dir, or "
          "pass --isolation off",
    "130": "CANCELLED",
}


def registry() -> str:
    return os.environ.get("ANTIGRAVITY_JOBS") or os.path.join(
        os.path.expanduser("~"), ".antigravity-jobs")


def die(msg: str) -> NoReturn:
    print(f"agy-job: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def meta(jd: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _read(os.path.join(jd, "meta")).splitlines():
        k, sep, v = line.partition("=")
        if sep:
            out[k] = v
    return out


def jobdir(ref: str) -> str:
    if not ref:
        die("need a job id")
    reg = registry()
    if os.path.isdir(os.path.join(reg, ref)):
        return os.path.join(reg, ref)
    try:
        hits = sorted(n for n in os.listdir(reg) if n.startswith(ref)
                      and os.path.isdir(os.path.join(reg, n)))
    except OSError:
        hits = []
    if not hits:
        die(f"no such job: {ref}")
    if len(hits) > 1:
        die(f"ambiguous id '{ref}'")
    return os.path.join(reg, hits[0])


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def state(jd: str) -> str:
    """running | done | failed (pid gone with no rc = crashed or killed)."""
    rc = _read(os.path.join(jd, "rc")).strip()
    if rc:
        return "done" if rc == "0" else "failed"
    pid = _read(os.path.join(jd, "pid")).strip()
    if pid.isdigit() and _alive(int(pid)):
        return "running"
    return "failed"


def conversation(jd: str) -> str:
    """The job's agy conversation id: from its AGY_USAGE line, else from the progress
    file (which has it even when the job was cut off before agy's result)."""
    for line in _read(os.path.join(jd, "err")).splitlines():
        if line.startswith("AGY_USAGE "):
            m = re.search(r'"conversation_id": *"([^"]*)"', line)
            if m and m.group(1):
                return m.group(1)
    try:
        prog = json.loads(_read(os.path.join(jd, "progress.json")) or "{}")
    except ValueError:
        prog = {}
    cid = prog.get("conversation_id") if isinstance(prog, dict) else ""
    return cid if isinstance(cid, str) else ""


def _fmt_secs(s: float) -> str:
    s = int(s)
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def progress_line(jd: str) -> str:
    try:
        p = json.loads(_read(os.path.join(jd, "progress.json")) or "null")
    except ValueError:
        p = None
    if not isinstance(p, dict):
        return ""
    tools = p.get("tools") or {}
    tool_s = " ".join(f"{k} x{v}" for k, v in list(tools.items())[:6]) if tools else "none"
    parts = [f"{_fmt_secs(p.get('elapsed_seconds', 0))}", f"{p.get('steps', 0)} steps",
             f"tools: {tool_s}"]
    if p.get("files_written_count"):
        parts.append(f"{p['files_written_count']} files written")
    if p.get("errors"):
        parts.append(f"{p['errors']} tool errors")
    if p.get("tokens"):
        parts.append(f"{p['tokens']} tokens")
    if p.get("current"):
        parts.append(f"now: {p['current']}")
    parts.append(f"last event {_fmt_secs(p.get('idle_seconds', 0))} ago")
    if p.get("denied"):
        parts.append(f"denied: {' '.join(p['denied'])}")
    return " · ".join(parts)


def progress_details(jd: str) -> list[str]:
    """Extra status lines: recent commands, files written, the last tool error."""
    try:
        p = json.loads(_read(os.path.join(jd, "progress.json")) or "null")
    except ValueError:
        return []
    if not isinstance(p, dict):
        return []
    lines = [f"  command={c}" for c in (p.get("commands") or [])[-3:]]
    files = p.get("files_written") or []
    if files:
        more = p.get("files_written_count", len(files)) - len(files[-5:])
        lines.append("  files_written=" + ", ".join(files[-5:])
                     + (f" (+{more} more)" if more > 0 else ""))
    if p.get("last_error"):
        lines.append(f"  last_error={p['last_error']}")
    return lines


def _others_running(cwd: str) -> int:
    reg = registry()
    n = 0
    with contextlib.suppress(OSError):
        for name in os.listdir(reg):
            jd = os.path.join(reg, name)
            if os.path.isdir(jd) and meta(jd).get("cwd") == cwd and state(jd) == "running":
                n += 1
    return n


def cmd_start(args: list[str]) -> int:
    if not args:
        die('start needs delegate args, e.g.  start "task"')
    resume = ""
    rest: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--resume":
            if i + 1 >= len(args):
                die("--resume needs a job id")
            resume = args[i + 1]
            i += 2
        else:
            rest.append(args[i])
            i += 1
    if not rest:
        die("start needs a task")
    resumed_from = ""
    if resume:
        rjd = jobdir(resume)
        if state(rjd) == "running":
            die(f"job {os.path.basename(rjd)} is still running; wait for it before resuming")
        if any(a in ("-c", "--continue", "--conversation") for a in rest):
            die("use --resume or --continue/--conversation, not both")
        conv = conversation(rjd)
        if not conv:
            die(f"job {os.path.basename(rjd)} recorded no agy conversation id (agy was "
                "killed before it started, or printed no events); --continue resumes agy's "
                "most recent conversation instead")
        rest = ["--conversation", conv, *rest]
        resumed_from = os.path.basename(rjd)
    if "--timeout" not in rest:
        rest = ["--timeout", os.environ.get("AGY_JOB_TIMEOUT") or option("JOB_TIMEOUT", "30m"),
                *rest]

    cwd = os.getcwd()
    others = _others_running(cwd)
    job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}-{random.randint(0, 32767)}"
    jd = os.path.join(registry(), job_id)
    os.makedirs(jd, exist_ok=True)
    task = " ".join(rest[-1].split())[:200]
    lines = [f"id={job_id}", f"cwd={cwd}",
             f"started={datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}", f"task={task}"]
    if resumed_from:
        lines.append(f"resumed_from={resumed_from}")
    with open(os.path.join(jd, "meta"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # Own session: the worker leads its process group, so cancel can stop the tree.
    proc = subprocess.Popen(
        [sys.executable, "-m", "agy_runner", "_job-run", jd, *rest],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env=env, cwd=cwd,
    )
    with open(os.path.join(jd, "pid"), "w", encoding="utf-8") as fh:
        fh.write(f"{proc.pid}\n")
    print(job_id)
    print(f"agy-job: started {job_id}. Collect it with: agy-job wait {job_id} (as a background "
          "Bash command; you are notified when it exits)", file=sys.stderr)
    if others:
        print(f"agy-job: note: {others} other job(s) still running in {cwd}. They share this "
              "checkout: parallel write jobs must touch separate files. agy-job list shows "
              "them.", file=sys.stderr)
    return 0


def job_run(jd: str, args: list[str]) -> int:
    """The detached worker: runs agy-delegate in-process with its output in the job dir."""
    from .delegate import Delegate

    os.environ["AGY_EVENTS_FILE"] = os.path.join(jd, "events.ndjson")
    os.environ["AGY_PROGRESS_FILE"] = os.path.join(jd, "progress.json")
    os.environ["AGY_CHILD_PID_FILE"] = os.path.join(jd, "agy.pid")
    with open(os.path.join(jd, "out"), "w", encoding="utf-8") as out, \
            open(os.path.join(jd, "err"), "w", encoding="utf-8") as err:
        rc = Delegate(out, err).main(args)
    tmp = os.path.join(jd, "rc.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(f"{rc}\n")
    os.replace(tmp, os.path.join(jd, "rc"))
    return rc


def cmd_list() -> int:
    reg = registry()
    if not os.path.isdir(reg):
        print("(no jobs)")
        return 0
    found = False
    for name in sorted(os.listdir(reg)):
        jd = os.path.join(reg, name)
        if not os.path.isdir(jd):
            continue
        m = meta(jd)
        if os.environ.get("ALL") != "1" and m.get("cwd") != os.getcwd():
            continue
        found = True
        print(f"{name:<32} {state(jd):<8} {m.get('task', '')}")
    if not found:
        print(f"(no jobs for {os.getcwd()} — set ALL=1 to see all)")
    return 0


def cmd_status(ref: str) -> int:
    jd = jobdir(ref)
    st = state(jd)
    rc = _read(os.path.join(jd, "rc")).strip()
    print(f"job:    {os.path.basename(jd)}")
    for line in _read(os.path.join(jd, "meta")).splitlines():
        print(f"  {line}")
    print(f"  state={st} (rc={rc}: {RC_LABELS.get(rc, 'error')})" if rc else f"  state={st}")
    prog = progress_line(jd)
    if prog:
        print(f"  progress={prog}")
        for line in progress_details(jd):
            print(line)
    for line in _read(os.path.join(jd, "err")).splitlines():
        if line.startswith("AGY_SIGNAL "):
            print(f"  signal={line[len('AGY_SIGNAL '):]}")
            break
    conv = conversation(jd)
    if conv:
        print(f'  conversation={conv} (follow up: agy-job start --resume '
              f'{os.path.basename(jd)} "<task>")')
    return 0


def cmd_result(ref: str, wait: bool, opts: list[str]) -> int:
    jd = jobdir(ref)
    st = state(jd)
    if wait:
        limit = 0
        if opts:
            if opts[0] != "--timeout":
                die(f"unknown wait option '{opts[0]}' (use --timeout <dur>)")
            if len(opts) < 2 or not opts[1]:
                die("wait --timeout needs a duration, e.g. 9m")
            if not valid_duration(opts[1]):
                die(f"bad --timeout '{opts[1]}' (use e.g. 540, 540s, 9m, 1h)")
            limit = duration_seconds(opts[1])
        poll = float(os.environ.get("AGY_JOB_POLL", "5"))
        start = time.monotonic()
        while st == "running":
            if limit and time.monotonic() - start >= limit:
                break
            time.sleep(poll)
            st = state(jd)
    if st == "running":
        print("still running — try again later")
        prog = progress_line(jd)
        if prog:
            print(f"progress: {prog}")
        return 2
    rc = _read(os.path.join(jd, "rc")).strip()
    err = _read(os.path.join(jd, "err"))
    if err:
        print("----- stderr -----", file=sys.stderr)
        sys.stderr.write(err if err.endswith("\n") else err + "\n")
    sys.stdout.write(_read(os.path.join(jd, "out")))
    sys.stdout.flush()
    label = f": {RC_LABELS.get(rc, 'error')}" if rc else ""
    print(f"[exit rc={rc or '?'}{label}]", file=sys.stderr)
    if conversation(jd):
        print(f'[follow up in this job\'s conversation: agy-job start --resume '
              f'{os.path.basename(jd)} "<task>"]', file=sys.stderr)
    return 0


def _kill_group(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def cmd_cancel(ref: str) -> int:
    jd = jobdir(ref)
    pid_s = _read(os.path.join(jd, "pid")).strip()
    if not (pid_s.isdigit() and _alive(int(pid_s))) or _read(os.path.join(jd, "rc")).strip():
        print("not running")
        return 0
    pid = int(pid_s)
    agy_s = _read(os.path.join(jd, "agy.pid")).strip()
    # SIGTERM the worker's group: the worker stops agy's own group and records rc 130.
    _kill_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.2)
    for p in ([int(agy_s)] if agy_s.isdigit() else []) + [pid]:
        _kill_group(p, signal.SIGKILL)
    if not _read(os.path.join(jd, "rc")).strip():
        with open(os.path.join(jd, "rc"), "w", encoding="utf-8") as fh:
            fh.write("130\n")
    print(f"cancelled {os.path.basename(jd)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if cmd == "start":
            return cmd_start(rest)
        if cmd == "list":
            return cmd_list()
        if cmd == "status":
            return cmd_status(rest[0] if rest else "")
        if cmd in ("result", "wait"):
            return cmd_result(rest[0] if rest else "", cmd == "wait", rest[1:])
        if cmd == "cancel":
            return cmd_cancel(rest[0] if rest else "")
        if cmd in ("", "-h", "--help", "help"):
            print((__doc__ or "").strip())
            return 0
        die(f"unknown subcommand '{cmd}' (start|list|status|result|wait|cancel)")
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1
