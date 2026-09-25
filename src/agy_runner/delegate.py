"""agy-delegate: run one agy turn headless and print agy's reply on stdout.

Usage:
  agy-delegate [options] "the task prompt"
  echo "long prompt" | agy-delegate [options] -      # read the prompt from stdin

Options:
  -t, --tier <flash|flash-lo|pro>  Model tier (only with model_lock=off; default flash)
  -m, --model <exact name>         Exact agy model (only with model_lock=off)
  -d, --dir <path>                 Add a workspace dir, writable in the jail (repeatable)
      --timeout <dur>              agy's --print-timeout, e.g. 10m (default 5m)
      --isolation <auto|workspace|readonly|off>
                                   workspace (default): agy runs in a bubblewrap jail where
                                   only the repository, --dir paths, ~/.gemini and the jail
                                   caches are writable, /tmp is private and credential dirs
                                   are hidden, with --dangerously-skip-permissions inside.
                                   readonly: only --dir paths writable. auto: workspace when
                                   bwrap is available, else off. off: plain agy.
      --yolo                       Auto-approve all tools (--dangerously-skip-permissions);
                                   implied inside the jail
      --sandbox                    agy's terminal sandbox (ignored in the jail)
      --mode <accept-edits|plan>   agy execution mode
      --digest                     Ask for a digest-only reply
  -c, --continue                   Resume agy's most recent conversation
      --conversation <id>          Resume a specific agy conversation
      --print-command              Print the resolved command and exit (dry run)
  -h, --help                       Show this help

Exit codes: 0 ok | 1 usage | 2 agy failed | 3 empty reply | 10 quota | 11 auth
  | 12 timeout (agy's --print-timeout mid-turn, the wall-clock guard, or no events for
  |    idle_timeout) | 13 agy missing or older than 1.2 | 14 model unavailable
  | 15 permission denied (headless, isolation off) | 16 isolation unavailable

stderr carries one AGY_RUN line before the run, one AGY_USAGE line after it and, on a
classified failure, one AGY_SIGNAL line; AGY_USAGE_LOG (or the usage_log option) also
appends AGY_USAGE / AGY_SIGNAL to a file. AGY_EVENTS_FILE keeps agy's raw event stream,
AGY_PROGRESS_FILE a live progress summary (agy-job sets both).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import NoReturn, TextIO

from . import jail as jailmod
from .options import (
    LOCKED_MODEL_DEFAULT,
    TIER_DEFAULTS,
    duration_seconds,
    model_for_tier,
    on_wsl,
    option,
    option_on,
    outer_timeout_seconds,
    tee_usage,
)
from .stream import Progress, Result, parse_line

MIN_AGY = (1, 2)

WORK_RULES = """

WORK RULES (from the orchestrator, who will review your diff and rerun everything):
- Never weaken, skip or delete tests, and never loosen thresholds or tolerances to make a check pass. If something fails, leave it failing and say so.
- Report only results you actually observed in this run (exact commands and their real output). Say "not run" rather than estimating.
- If you did less than asked (fewer runs or trials, a subset of cases, a smaller setup), say so explicitly in the report, with the numbers asked for and the numbers done.
- Do not leave scratch or debug files in the repository.
- Run commands in the foreground. Never start one in the background and then wait or poll for it: your turn has a time limit, and waiting burns it.
- Do not change project config (pyproject.toml, package.json, .gitignore, ...) to work around the sandbox. If a path is read-only, say which one and continue without it.
- End with a short report: files changed, commands run with their results, and what is unfinished or failing."""

DIGEST_CONTRACT = """

OUTPUT CONTRACT (digest): reply with ONLY a compact digest — short bullets (findings / decisions / errors, with file:line references where useful). NO full file contents, NO raw logs, NO long code blocks. End with exactly one line: DIGEST: <one-sentence summary>."""

WRITE_WORDS = ("implement", "scaffold", "migrate", "refactor", "write the file",
               "create the file", "edit the file")


class Exit(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Args:
    tier: str = ""
    tier_explicit: bool = False
    model: str = ""
    dirs: list[str] = field(default_factory=list)
    timeout: str = ""
    yolo: bool = False
    sandbox: bool = False
    isolation: str = ""
    digest: bool = False
    mode: str = ""
    cont: bool = False
    conversation: str = ""
    print_command: bool = False
    prompt: str = ""


class Delegate:
    def __init__(self, stdout: TextIO, stderr: TextIO) -> None:
        self.out = stdout
        self.err = stderr
        self.model = ""
        self.tool_errors: list[str] = []

    # --- output helpers ---------------------------------------------------------------
    def say(self, msg: str) -> None:
        print(f"agy-delegate: {msg}", file=self.err)

    def die(self, msg: str) -> NoReturn:
        self.say(msg)
        raise Exit(1)

    def signal(self, status: str, reason: str) -> None:
        retry = "--continue" if status == "QUOTA_EXHAUSTED" else ""
        reason = " ".join(reason.split()).replace('"', "").replace("\\", "")[:200]
        line = "AGY_SIGNAL " + json.dumps(
            {"status": status, "reason": reason, "model": self.model, "retry": retry},
            separators=(",", ":"), ensure_ascii=False,
        )
        print(line, file=self.err)
        tee_usage(line)

    # --- arguments --------------------------------------------------------------------
    def parse(self, argv: list[str]) -> Args:
        a = Args(tier=option("DEFAULT_TIER", "flash"), timeout=option("TIMEOUT", "5m"),
                 isolation=option("ISOLATION", "workspace"))
        i = 0

        def value(flag: str) -> str:
            nonlocal i
            if i + 1 >= len(argv):
                self.die(f"option '{flag}' needs a value")
            i += 2
            return argv[i - 1]

        while i < len(argv):
            arg = argv[i]
            if arg in ("-t", "--tier"):
                a.tier, a.tier_explicit = value(arg), True
            elif arg in ("-d", "--dir"):
                a.dirs.append(value(arg))
            elif arg == "--timeout":
                a.timeout = value(arg)
            elif arg == "--yolo":
                a.yolo, i = True, i + 1
            elif arg == "--sandbox":
                a.sandbox, i = True, i + 1
            elif arg == "--isolation":
                a.isolation = value(arg)
            elif arg == "--digest":
                a.digest, i = True, i + 1
            elif arg == "--mode":
                a.mode = value(arg)
                if a.mode not in ("accept-edits", "plan"):
                    self.die(f"invalid --mode '{a.mode}' (use accept-edits | plan)")
            elif arg in ("-c", "--continue"):
                a.cont, i = True, i + 1
            elif arg == "--conversation":
                a.conversation = value(arg)
            elif arg in ("-m", "--model"):
                a.model = value(arg)
            elif arg == "--print-command":
                a.print_command, i = True, i + 1
            elif arg in ("-h", "--help"):
                print(__doc__.strip(), file=self.out)
                raise Exit(0)
            elif arg == "-":
                a.prompt, i = sys.stdin.read(), i + 1
            elif arg == "--":
                a.prompt = " ".join(argv[i + 1:])
                break
            elif arg.startswith("-") and len(arg) > 1:
                self.die(f"unknown option '{arg}'")
            else:
                a.prompt = " ".join(argv[i:])
                break
        if not a.prompt:
            self.die("no prompt given (pass a string, or '-' to read stdin)")
        return a

    def resolve_model(self, a: Args) -> str:
        """Model lock on (default): default_model > Gemini 3.8 Flash (High); --tier and
        --model are ignored with a note. Off: --model > --tier > default_model > tier."""
        if option_on("MODEL_LOCK"):
            locked = option("DEFAULT_MODEL") or LOCKED_MODEL_DEFAULT
            asked = []
            if a.tier_explicit:
                asked.append(f"--tier {a.tier}")
            if a.model and a.model != locked:
                asked.append(f"--model '{a.model}'")
            if asked:
                self.say(f"note: {', '.join(asked)} ignored — the model is locked to "
                         f"'{locked}' (plugin option model_lock; default_model changes "
                         "the locked model).")
            return locked
        if a.model:
            return a.model
        if a.tier_explicit:
            if a.tier not in TIER_DEFAULTS:
                self.die(f"unknown tier '{a.tier}' (use flash | flash-lo | pro)")
            return model_for_tier(a.tier)
        if option("DEFAULT_MODEL"):
            return option("DEFAULT_MODEL")
        tier = a.tier
        if tier not in TIER_DEFAULTS:
            self.say(f"invalid default tier '{tier}' (set CLAUDE_PLUGIN_OPTION_DEFAULT_TIER "
                     "to flash|flash-lo|pro); using flash")
            tier = "flash"
        return model_for_tier(tier)

    def usage_tier(self, a: Args) -> str:
        if option_on("MODEL_LOCK") or a.model:
            return ""
        if a.tier_explicit:
            return a.tier
        if option("DEFAULT_MODEL"):
            return ""
        return a.tier if a.tier in TIER_DEFAULTS else "flash"

    def check_agy(self) -> None:
        agy = shutil.which("agy")
        if agy is None:
            self.say("'agy' not found on PATH — install the Antigravity CLI first")
            self.signal("AGY_MISSING", "agy not on PATH")
            raise Exit(13)
        try:
            out = subprocess.run([agy, "--version"], capture_output=True, text=True,
                                 timeout=15, stdin=subprocess.DEVNULL, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
        if m and (int(m.group(1)), int(m.group(2))) < MIN_AGY:
            self.say(f"agy {m.group(0)} is too old: this plugin needs agy >= 1.2 "
                     "(stream-json output). Update the Antigravity CLI.")
            self.signal("AGY_MISSING", f"agy {m.group(0)} < 1.2")
            raise Exit(13)

    # --- main -------------------------------------------------------------------------
    def main(self, argv: list[str]) -> int:
        # SIGTERM / SIGHUP (agy-job cancel, a killed Bash call) stop agy's whole tree via
        # the KeyboardInterrupt path in run(), instead of leaving it running unattended.
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with contextlib.suppress(ValueError):  # not the main thread
                signal.signal(sig, _interrupt)
        try:
            return self._main(argv)
        except Exit as e:
            return e.code
        except KeyboardInterrupt:
            self.say("interrupted — agy was stopped")
            return 130

    def _main(self, argv: list[str]) -> int:
        a = self.parse(argv)
        if not a.print_command:
            self.check_agy()
        self.model = self.resolve_model(a)
        timeout = a.timeout

        if on_wsl():
            for d in a.dirs:
                if d.startswith("/mnt/"):
                    self.say(f"note: --add-dir '{d}' is on a Windows mount under WSL; agy "
                             "reads it over a slow 9p bridge (calls can take 20s+). Move the "
                             "repo into the Linux FS (~) for ~10x faster I/O.")
                    break

        isolation = a.isolation
        if isolation == "auto":
            isolation = "workspace" if jailmod.can_isolate() else "off"
        elif isolation in ("workspace", "readonly"):
            if not jailmod.can_isolate():
                self.isolation_fail(f"isolation '{isolation}' (the default is workspace) "
                                    "needs Linux with bwrap")
        elif isolation != "off":
            self.die(f"invalid --isolation '{isolation}' (use auto | workspace | readonly | off)")
        yolo, sandbox = a.yolo, a.sandbox
        if isolation != "off":
            yolo = True  # the jail is the boundary; headless prompts would only auto-deny
            if sandbox:
                self.say(f"note: --sandbox ignored under --isolation {isolation} (agy's "
                         "terminal sandbox cannot nest inside bwrap)")
                sandbox = False

        if not yolo and not a.print_command:
            low = a.prompt.lower()
            if any(w in low for w in WRITE_WORDS):
                self.say("note: this looks like a write task and --yolo is not set. Headless "
                         "agy will NOT touch your workspace without a write grant; the "
                         "workspace is untouched either way. Two grants work: a "
                         "permissions.allow rule matching the target — write_file(<dir>), a "
                         "recursive prefix, in ~/.gemini/antigravity-cli/settings.json — "
                         "or --yolo, which auto-approves ALL tools. Verify with git status.")

        prompt = a.prompt
        if a.digest:
            prompt += DIGEST_CONTRACT
        if option_on("WORK_RULES"):
            prompt += WORK_RULES

        # -p takes the prompt as its value, so it goes last.
        args = ["--model", self.model, "--print-timeout", timeout]
        for d in a.dirs:
            args += ["--add-dir", d]
        if yolo:
            args.append("--dangerously-skip-permissions")
        if a.mode:
            args += ["--mode", a.mode]
        if sandbox:
            args.append("--sandbox")
        if a.cont:
            args.append("--continue")
        if a.conversation:
            args += ["--conversation", a.conversation]
        args += ["--output-format", "stream-json"]

        settings_tmp = ""
        try:
            cmd = ["agy"]
            if isolation != "off":
                j = jailmod.Jail(isolation, a.dirs, dry_run=a.print_command)
                if a.print_command:
                    home_settings = os.path.join(os.path.expanduser("~"), jailmod.SETTINGS_REL)
                    settings = "<settings-copy>" if os.path.isfile(home_settings) else ""
                else:
                    fd, settings_tmp = tempfile.mkstemp(prefix="agy-settings.")
                    os.close(fd)
                    settings = settings_tmp if jailmod.settings_copy(settings_tmp) else ""
                try:
                    cmd = [*j.command(settings), "agy"]
                except jailmod.IsolationUnavailable as exc:
                    self.isolation_fail(str(exc))
                for n in j.notes:
                    self.say(f"note: {n}")
            full = [*cmd, *args, "-p", prompt]
            if a.print_command:
                print(shlex.join(full), file=self.out)
                return 0
            return self.run(full, a, timeout, isolation)
        except jailmod.IsolationUnavailable as exc:
            self.isolation_fail(str(exc))
        finally:
            if settings_tmp:
                with contextlib.suppress(OSError):
                    os.unlink(settings_tmp)

    def isolation_fail(self, why: str) -> NoReturn:
        self.say(f"isolation unavailable — {why}. Install bubblewrap (bwrap) on Linux, or "
                 "pass --isolation off (agy then needs permissions.allow rules or --yolo).")
        self.signal("ISOLATION_UNAVAILABLE", why)
        raise Exit(16)

    # --- the run ----------------------------------------------------------------------
    def run(self, cmd: list[str], a: Args, timeout: str, isolation: str) -> int:
        print("AGY_RUN " + json.dumps({"model": self.model, "isolation": isolation,
                                       "timeout": timeout}, separators=(",", ":"),
                                      ensure_ascii=False), file=self.err)
        # stdout goes to a FILE, never a pipe: agy's stdio MCP children inherit it and can
        # outlive agy, so a pipe reader would wait for EOF forever (issue #37). A reader
        # thread tails the file for progress instead.
        events_path = os.environ.get("AGY_EVENTS_FILE", "")
        own_events = not events_path
        if own_events:
            fd, events_path = tempfile.mkstemp(prefix="agy-events.")
            os.close(fd)
        fd, err_path = tempfile.mkstemp(prefix="agy-err.")
        os.close(fd)
        progress_path = os.environ.get("AGY_PROGRESS_FILE", "")
        wall = outer_timeout_seconds(timeout)
        idle = duration_seconds(option("IDLE_TIMEOUT", "15m"), 900)
        try:
            with open(events_path, "wb") as out_fh, open(err_path, "wb") as err_fh:
                # Own session: a timeout kills the whole tree (bwrap, agy, its tools).
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out_fh,
                                        stderr=err_fh, start_new_session=True)
            child_pid_file = os.environ.get("AGY_CHILD_PID_FILE", "")
            if child_pid_file:
                with contextlib.suppress(OSError), open(child_pid_file, "w") as fh:
                    fh.write(f"{proc.pid}\n")
            progress = Progress()
            tail = _Tail(events_path, progress, progress_path)
            tail.start()
            killed = ""
            try:
                while True:
                    try:
                        proc.wait(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    now = time.time()
                    if now - progress.started > wall:
                        killed = "wall"
                    elif idle and now - progress.last_event > idle:
                        killed = "idle"
                    if killed:
                        _kill_tree(proc)
                        break
            except KeyboardInterrupt:
                _kill_tree(proc)
                raise
            tail.stop()
            rc = proc.returncode if proc.returncode is not None else -1
            with open(err_path, encoding="utf-8", errors="replace") as fh:
                err_text = fh.read()
            return self.finish(rc, killed, wall, idle, tail, progress, err_text, a, timeout,
                               isolation)
        finally:
            for p in [err_path] + ([events_path] if own_events else []):
                with contextlib.suppress(OSError):
                    os.unlink(p)

    def finish(self, rc: int, killed: str, wall: int, idle: int, tail: _Tail,
               progress: Progress, err_text: str, a: Args, timeout: str,
               isolation: str) -> int:
        res = tail.result
        self.tool_errors = progress.error_messages
        # Trailing newlines dropped, as the bash wrapper's $(cat ...) did: one print adds one.
        out = (res.response if res else (progress.text or tail.fallback_text())).rstrip("\n")
        if res:
            meta = {
                "status": res.status, "error": res.error, "usage": res.usage,
                "conversation_id": res.conversation_id or progress.conversation_id,
                "model": self.model, "tier": self.usage_tier(a), "isolation": isolation,
                "duration_seconds": res.duration_seconds, "num_turns": res.num_turns,
            }
            line = "AGY_USAGE " + json.dumps(meta, ensure_ascii=False)
            print(line, file=self.err)
            tee_usage(line)
            if res.status == "ERROR" and rc == 0:
                rc = 1

        if killed:
            if killed == "wall":
                self.say(f"agy hit the wall-clock guard ({wall}s) and was terminated — "
                         "likely a hang.")
                self.signal("TIMEOUT", f"agy wall-clock guard fired after {wall}s")
            else:
                self.say(f"agy sent no events for {idle}s and was terminated (plugin option "
                         "idle_timeout). Files may already be changed: check git status; "
                         "--continue resumes the conversation.")
                self.signal("TIMEOUT", f"agy idle for {idle}s — files may be changed; "
                                       "--continue resumes")
            self.readonly_hint(out, err_text, isolation)
            raise Exit(12)

        denied = res.denied_actions if res else []
        if denied:
            if out.strip():
                print(out, file=self.out)
            self.permission_denied(err_text, denied)

        if rc == 0 and re.search(r"print timeout after .*returning partial output", err_text):
            if out.strip():
                print(out, file=self.out)
                note = "the reply above is PARTIAL"
            else:
                note = "agy returned NO reply text (the turn ended before its final message)"
            self.say(f"agy's --print-timeout ({timeout}) expired mid-turn — {note}. Files may "
                     "already be changed: check git status. --continue resumes the same "
                     "conversation (agy reports no usage for the cut-off turn"
                     + (", so the AGY_USAGE line above undercounts" if res else "")
                     + "). Raise --timeout or narrow the task.")
            self.readonly_hint(out, err_text, isolation)
            self.signal("TIMEOUT", f"agy print-timeout ({timeout}) expired mid-turn — reply "
                                   "may be empty, files may be changed; --continue resumes")
            raise Exit(12)

        if rc != 0:
            self.say(f"agy exited {rc}")
            if err_text:
                self.err.write(err_text if err_text.endswith("\n") else err_text + "\n")
            if res and res.error:
                print(res.error, file=self.err)
            self.readonly_hint(out, err_text, isolation)
            blob = ((res.error + "\n") if res else "") + err_text
            low = blob.lower()
            if any(s in low for s in ("user denied permission", "permission check failed",
                                      "auto-denied", "permission that headless",
                                      "dangerously-skip-permissions")):
                self.permission_denied("", [])
            if any(s in low for s in ("quota", "rate limit", "resource exhausted")):
                self.signal("QUOTA_EXHAUSTED", "agy quota / rate limit")
                raise Exit(10)
            if any(s in low for s in ("unauthenticated", "unauthorized", "sign in",
                                      "please authenticate", "reauth")):
                self.signal("AUTH_REQUIRED", "agy not authenticated — run `agy` once")
                raise Exit(11)
            if any(s in low for s in ("timed out", "deadline exceeded", "print-timeout")):
                self.signal("TIMEOUT", "agy print-timeout / deadline exceeded")
                raise Exit(12)
            if any(s in low for s in ("invalid --model", "is not recognized as a known model",
                                      "not a known model")):
                self.say(f"model '{self.model}' is not available on this plan — run "
                         "`agy models`, then fix --model / the tier_* / default_model option.")
                self.signal("MODEL_UNAVAILABLE",
                            "model not in `agy models` (check --model / tier remaps)")
                raise Exit(14)
            self.signal("AGY_FAILED", f"agy exited {rc}")
            raise Exit(2)

        if not out.strip():
            low = err_text.lower()
            if progress.denied or any(s in low for s in (
                    "auto-denied", "permissions.allow", "permission that headless",
                    "dangerously-skip-permissions")):
                self.permission_denied(err_text, progress.denied)
            self.say(f"agy returned empty output (model='{self.model}')")
            raise Exit(3)

        try:
            warn = int(option("DIGEST_WARN_CHARS", "8000") or "8000")
        except ValueError:
            warn = 8000
        if warn > 0 and len(out) > warn:
            self.say(f"note: output is {len(out)} chars (> {warn}) — that looks like a raw "
                     "dump, not a digest. Don't ingest this into the conductor's context: "
                     "re-run with --digest, or have agy summarize it first. (plugin option "
                     "digest_warn_chars tunes this; 0 disables.)")
        self.readonly_hint(out, err_text, isolation)
        print(out, file=self.out)
        return 0

    def readonly_hint(self, out: str, err_text: str, isolation: str) -> None:
        if isolation != "workspace":
            return
        paths = jailmod.readonly_paths([out, err_text, *self.tool_errors])
        if paths:
            self.say(f"note: agy hit read-only paths in the jail: {' '.join(paths)}. If a tool "
                     "needs one of them (a cache, never a credential), add it to the plugin "
                     "option isolation_writable and rerun with --continue.")

    def permission_denied(self, err_text: str, denied: list[str]) -> NoReturn:
        if err_text:
            self.err.write(err_text if err_text.endswith("\n") else err_text + "\n")
        names = " ".join(denied)
        suffix = f" — denied: {names}" if names else ""
        self.say("agy denied a tool that needs permission (headless can't prompt)" + suffix +
                 " — the denied action was not performed. For a FILE WRITE, the narrower fix "
                 "is a permissions.allow rule covering the target in "
                 "~/.gemini/antigravity-cli/settings.json — write_file(<dir>) matches "
                 "recursively beneath <dir>; --yolo also works but auto-approves ALL tools. "
                 "For a URL READ the rule is read_url(<target>). `--mode accept-edits` is "
                 "NOT a write grant: agy denies it exactly like a plain write. In the default "
                 "bubblewrap jail none of this is needed.")
        self.signal("PERMISSION_DENIED", "agy denied a permissioned tool in headless" +
                    (f" (denied: {names})" if names else "") +
                    " — add a permissions.allow rule or pass --yolo")
        raise Exit(15)


class _Tail(threading.Thread):
    """Follows the events file while agy runs: updates progress, keeps the result."""

    def __init__(self, path: str, progress: Progress, progress_path: str) -> None:
        super().__init__(daemon=True)
        self.path = path
        self.progress = progress
        self.progress_path = progress_path
        self.result: Result | None = None
        self.text: list[str] = []
        self._halt = threading.Event()
        self._pos = 0
        self._buf = b""

    def run(self) -> None:
        last_write = 0.0
        while not self._halt.is_set():
            changed = self._read()
            now = time.time()
            if self.progress_path and (changed or now - last_write > 5):
                self.progress.write(self.progress_path)
                last_write = now
            self._halt.wait(0.5)

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=5)
        self._read(final=True)
        if self.progress_path:
            self.progress.write(self.progress_path)

    def _read(self, final: bool = False) -> bool:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._pos)
                chunk = fh.read()
        except OSError:
            return False
        if not chunk and not (final and self._buf):
            return False
        self._pos += len(chunk)
        self._buf += chunk
        *lines, self._buf = self._buf.split(b"\n")
        if final and self._buf:
            lines.append(self._buf)
            self._buf = b""
        for raw in lines:
            line = raw.decode("utf-8", errors="replace")
            ev = parse_line(line)
            if ev is None:
                if line.strip():
                    self.text.append(line)
                continue
            name, payload = ev
            self.progress.update(name, payload)
            if name == "result":
                self.result = Result.from_payload(payload)
        return True

    def fallback_text(self) -> str:
        """Non-event stdout, for an agy that printed plain text instead of events."""
        return "\n".join(self.text)


def _interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, sig)
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def main(argv: list[str] | None = None) -> int:
    return Delegate(sys.stdout, sys.stderr).main(sys.argv[1:] if argv is None else argv)
