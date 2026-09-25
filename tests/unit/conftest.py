from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

# A fake `agy` driven by FAKE_AGY_MODE. It speaks agy 1.2's stream-json and records its
# pid (and those of its children) so tests can check that a timeout or a cancel stopped
# the whole tree.
FAKE_AGY = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
if sys.argv[1:] == ["--version"]:
    print(os.environ.get("FAKE_AGY_VERSION", "1.2.11")); sys.exit(0)
mode = os.environ.get("FAKE_AGY_MODE", "ok")
pids = os.environ.get("FAKE_AGY_PIDS")
cid = "conv-123"
def ev(name, **payload):
    print(json.dumps({"event": name, name: payload}), flush=True)
def step(i, tool, state="DONE"):
    ev("step_update", conversation_id=cid, step_index=i, state=state, step_type="tool",
       tool_name=tool)
print(json.dumps({"event": "init", "conversation_id": cid, "init": {"cwd": os.getcwd()}}),
      flush=True)
if pids:
    with open(pids, "a") as fh:
        fh.write(f"{os.getpid()}\n")
if mode == "ok":
    step(0, "run_command"); step(1, "view_file"); step(2, "run_command")
    ev("result", conversation_id=cid, status="SUCCESS", response="DONE\n",
       duration_seconds=2.5, num_turns=1,
       usage={"input_tokens": 100, "output_tokens": 20, "thinking_tokens": 5,
              "cache_read_tokens": 50, "total_tokens": 125})
elif mode == "hang":
    # Starts a child that would outlive a careless kill, then goes silent.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    if pids:
        with open(pids, "a") as fh:
            fh.write(f"{child.pid}\n")
    step(0, "run_command", "WORKING")
    time.sleep(300)
elif mode == "slow":
    for i in range(int(os.environ.get("FAKE_AGY_STEPS", "6"))):
        step(i, "run_command", "WORKING"); time.sleep(0.5)
    ev("result", conversation_id=cid, status="SUCCESS", response="SLOW DONE\n",
       usage={"total_tokens": 1})
elif mode == "error":
    ev("result", conversation_id=cid, status="ERROR", response="",
       error="quota exceeded for this model", usage={})
    sys.exit(1)
'''


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    agy = bindir / "agy"
    agy.write_text(FAKE_AGY)
    agy.chmod(agy.stat().st_mode | stat.S_IEXEC)
    e = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_PLUGIN_OPTION_")}
    e.update({
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(SRC),
        "CLAUDE_PLUGIN_OPTION_ISOLATION": "off",
        "CLAUDE_PLUGIN_OPTION_WORK_RULES": "off",
        "ANTIGRAVITY_JOBS": str(tmp_path / "jobs"),
        "FAKE_AGY_PIDS": str(tmp_path / "pids"),
        "AGY_JOB_POLL": "0.2",
    })
    e.pop("AGY_USAGE_LOG", None)
    return e


def run(env: dict[str, str], *args: str, timeout: float = 60,
        cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "agy_runner", *args], env=env, cwd=cwd,
                          capture_output=True, text=True, timeout=timeout, check=False)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie is dead for our purposes
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(") ")[1].split()[0] != "Z"
    except OSError:
        return True
    return True
