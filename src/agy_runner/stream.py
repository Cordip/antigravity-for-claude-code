"""agy `--output-format stream-json`: one JSON event per line.

Shapes (agy 1.2.x; unknown events are ignored):
  {"event":"init","conversation_id":"c","init":{"cwd":..,"model":..,"tools":[..]}}
  {"event":"step_update","step_update":{"conversation_id":..,"step_index":4,
     "state":"WORKING|DONE|DENIED","step_type":"tool|agent_response","tool_name":..,
     "text_delta":..,"tool_info":{"name":..,"output":..},"usage":{..}}}
  {"event":"result","result":{"conversation_id":..,"status":"SUCCESS|ERROR","response":..,
     "error":..,"duration_seconds":..,"num_turns":..,"usage":{"input_tokens":..,..}}}
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
            usage={
                "input": _int(u.get("input_tokens")),
                "output": _int(u.get("output_tokens")),
                "thinking": _int(u.get("thinking_tokens")),
                "cache_read": _int(u.get("cache_read_tokens")),
                "total": _int(u.get("total_tokens")),
            },
            denied_actions=denied,
        )


@dataclass
class Progress:
    """What a running agy turn has done so far, for `agy-job status`. Tool names and
    counts only: arguments can hold file contents and secrets."""

    started: float = field(default_factory=time.time)
    last_event: float = field(default_factory=time.time)
    conversation_id: str = ""
    steps: int = 0
    tools: dict[str, int] = field(default_factory=dict)
    denied: list[str] = field(default_factory=list)
    current: str = ""
    text_chars: int = 0
    _seen_steps: set[int] = field(default_factory=set)

    def update(self, name: str, payload: dict[str, Any]) -> None:
        self.last_event = time.time()
        cid = payload.get("conversation_id")
        if isinstance(cid, str) and cid:
            self.conversation_id = cid
        if name != "step_update":
            return
        idx = payload.get("step_index")
        tool = payload.get("tool_name") or (payload.get("tool_info") or {}).get("name")
        state = str(payload.get("state") or "")
        if isinstance(idx, int) and idx not in self._seen_steps:
            self._seen_steps.add(idx)
            self.steps += 1
            if tool:
                self.tools[str(tool)] = self.tools.get(str(tool), 0) + 1
        if tool:
            self.current = f"{tool} ({state.lower()})" if state else str(tool)
            if state == "DENIED" and str(tool) not in self.denied:
                self.denied.append(str(tool))
        delta = payload.get("text_delta")
        if isinstance(delta, str):
            self.text_chars += len(delta)

    def as_dict(self) -> dict[str, Any]:
        now = time.time()
        return {
            "elapsed_seconds": round(now - self.started, 1),
            "idle_seconds": round(now - self.last_event, 1),
            "conversation_id": self.conversation_id,
            "steps": self.steps,
            "tools": dict(sorted(self.tools.items(), key=lambda kv: -kv[1])),
            "current": self.current,
            "denied": self.denied,
            "text_chars": self.text_chars,
        }

    def write(self, path: str) -> None:
        tmp = f"{path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.as_dict(), fh)
            os.replace(tmp, path)
        except OSError:
            pass
