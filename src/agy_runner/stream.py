"""agy `--output-format stream-json`: one JSON event per line.

Shapes, measured on agy 1.2.11 (tests/unit/fixtures; unknown events are ignored):
  {"event":"init","conversation_id":"c","init":{"model":..,"cwd":..,"tools":[..]}}
  {"event":"step_update","step_update":{"conversation_id":..,"step_index":4,
     "state":"ACTIVE|DONE|ERROR","step_type":"user_input|agent_response|tool",
     "tool_name":"run_command","duration_seconds":..,"text_delta":..,"usage":{..},
     "tool_info":{"name":..,"parameters":{"CommandLine"|"AbsolutePath"|"TargetFile":..},
                  "output":..,"error":{"type":"TOOL_ERROR","message":..}}}}
  {"event":"result","result":{"conversation_id":..,"status":"SUCCESS|ERROR","response":..,
     "error":..,"duration_seconds":..,"num_turns":..,"usage":{"input_tokens":..,..}}}
A tool step arrives twice (ACTIVE, then DONE or ERROR) with the same step_index. usage on
agent_response steps is per step; on result, and duration / num_turns there, it covers the
whole conversation, earlier --continue / --conversation turns included. A --print-timeout
cut still ends with a result (empty response, usage of the finished steps).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


def parse_line(line: str) -> tuple[str, dict[str, Any]] | None:
    """(event name, payload) for a stream-json line, or None for anything else. agy 1.1.8
    left raw newlines inside strings, so the parse is non-strict."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line, strict=False)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("event")
    if not isinstance(name, str):
        return None
    payload = obj.get(name)
    if not isinstance(payload, dict):
        payload = {}
    if "conversation_id" not in payload and isinstance(obj.get("conversation_id"), str):
        payload = {**payload, "conversation_id": obj["conversation_id"]}
    return name, payload


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _num(v: Any) -> float:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


@dataclass
class Result:
    status: str = ""
    response: str = ""
    error: str = ""
    conversation_id: str = ""
    duration_seconds: float = 0
    num_turns: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    denied_actions: list[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, p: dict[str, Any]) -> Result:
        u = p.get("usage") if isinstance(p.get("usage"), dict) else {}
        assert isinstance(u, dict)
        denied: list[str] = []
        raw = p.get("denied_actions")
        for a in raw if isinstance(raw, list) else []:
            n = (a.get("action") or a.get("display_name")) if isinstance(a, dict) else a
            if n:
                denied.append(" ".join(str(n).split()))
        return cls(
            status=str(p.get("status") or ""),
            response=str(p.get("response") or ""),
            error=" ".join(str(p.get("error") or "").split()),
            conversation_id=str(p.get("conversation_id") or ""),
            duration_seconds=_num(p.get("duration_seconds")),
            num_turns=_int(p.get("num_turns")),
            usage=usage_of(u),
            denied_actions=denied,
        )


def usage_of(u: dict[str, Any]) -> dict[str, int]:
    """agy's usage object in the AGY_USAGE key names."""
    return {
        "input": _int(u.get("input_tokens")),
        "output": _int(u.get("output_tokens")),
        "thinking": _int(u.get("thinking_tokens")),
        "cache_read": _int(u.get("cache_read_tokens")),
        "total": _int(u.get("total_tokens")),
    }


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# The parameter that says what a tool acted on, by tool (agy 1.2.11 names).
_TARGET_KEYS = ("CommandLine", "TargetFile", "AbsolutePath", "SearchPath", "Query", "Url")


@dataclass
class Progress:
    """What a running agy turn has done so far, for `agy-job status`: tool counts, the
    commands it ran and the files it wrote (paths only, never contents or outputs),
    tool errors, and the tokens of the steps so far."""

    started: float = field(default_factory=time.time)
    last_event: float = field(default_factory=time.time)
    conversation_id: str = ""
    steps: int = 0
    tools: dict[str, int] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    errors: int = 0
    last_error: str = ""
    error_messages: list[str] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)
    current: str = ""
    tokens: int = 0
    # Summed usage of this run's finished steps. agy's `result` usage is cumulative over a
    # resumed conversation; these sums are what this run alone spent (measured on 1.2.11:
    # previous result + this run's step sums == this result, per key).
    usage: dict[str, int] = field(default_factory=dict)
    text: str = ""
    _seen_steps: set[int] = field(default_factory=set)

    def update(self, name: str, payload: dict[str, Any]) -> None:
        self.last_event = time.time()
        cid = payload.get("conversation_id")
        if isinstance(cid, str) and cid:
            self.conversation_id = cid
        if name != "step_update":
            return
        idx = payload.get("step_index")
        info = payload.get("tool_info") if isinstance(payload.get("tool_info"), dict) else {}
        assert isinstance(info, dict)
        tool = str(payload.get("tool_name") or info.get("name") or "")
        state = str(payload.get("state") or "")
        params = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
        assert isinstance(params, dict)
        target = next((str(params[k]) for k in _TARGET_KEYS if params.get(k)), "")
        new = isinstance(idx, int) and idx not in self._seen_steps
        if new:
            assert isinstance(idx, int)
            self._seen_steps.add(idx)
            self.steps += 1
            if tool:
                self.tools[tool] = self.tools.get(tool, 0) + 1
                if params.get("CommandLine"):
                    self.commands = [*self.commands, _short(str(params["CommandLine"]))][-5:]
                if params.get("TargetFile") and str(params["TargetFile"]) not in self.files_written:
                    self.files_written.append(str(params["TargetFile"]))
        if tool:
            label = f"{tool}: {_short(target, 80)}" if target else tool
            self.current = f"{label} ({state.lower()})" if state else label
            if state == "DENIED" and tool not in self.denied:
                self.denied.append(tool)
            err = info.get("error")
            if state == "ERROR" or err:
                msg = err.get("message") if isinstance(err, dict) else err
                self.errors += 1
                self.last_error = _short(f"{tool}: {msg or state}", 200)
                if msg:
                    self.error_messages = [*self.error_messages, str(msg)][-20:]
        usage = payload.get("usage")
        if isinstance(usage, dict) and state == "DONE":
            self.tokens += _int(usage.get("total_tokens"))
            for k, v in usage_of(usage).items():
                self.usage[k] = self.usage.get(k, 0) + v
        delta = payload.get("text_delta")
        if isinstance(delta, str):
            self.text += delta

    def as_dict(self) -> dict[str, Any]:
        now = time.time()
        return {
            "elapsed_seconds": round(now - self.started, 1),
            "idle_seconds": round(now - self.last_event, 1),
            "conversation_id": self.conversation_id,
            "steps": self.steps,
            "tools": dict(sorted(self.tools.items(), key=lambda kv: -kv[1])),
            "current": self.current,
            "commands": self.commands,
            "files_written": self.files_written[-20:],
            "files_written_count": len(self.files_written),
            "errors": self.errors,
            "last_error": self.last_error,
            "denied": self.denied,
            "tokens": self.tokens,
        }

    def write(self, path: str) -> None:
        tmp = f"{path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.as_dict(), fh)
            os.replace(tmp, path)
        except OSError:
            pass
