from __future__ import annotations

import json

from agy_runner.stream import Progress, Result, parse_line


def test_parse_line_envelope_and_top_level_conversation() -> None:
    ev = parse_line('{"event":"init","conversation_id":"c1","init":{"cwd":"/r"}}')
    assert ev == ("init", {"cwd": "/r", "conversation_id": "c1"})


def test_parse_line_rejects_non_events() -> None:
    assert parse_line("plain text") is None
    assert parse_line('{"no_event": 1}') is None
    assert parse_line("[1, 2]") is None
    assert parse_line('{"event": "x"') is None


def test_parse_line_tolerates_raw_newlines_in_strings() -> None:
    raw = '{"event":"result","result":{"response":"a\nb","status":"SUCCESS"}}'
    ev = parse_line(raw)
    assert ev is not None and ev[1]["response"] == "a\nb"


def test_result_from_payload_normalises_usage_and_denials() -> None:
    r = Result.from_payload({
        "status": "SUCCESS", "response": "hi", "error": "a\n  b", "conversation_id": "c",
        "duration_seconds": 3.5, "num_turns": 2,
        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "bogus": True},
        "denied_actions": [{"action": "write_file"}, {"display_name": "Read Url"}, "cmd"],
    })
    assert r.error == "a b"
    assert r.usage == {"input": 1, "output": 2, "thinking": 0, "cache_read": 0, "total": 3}
    assert r.denied_actions == ["write_file", "Read Url", "cmd"]
    assert (r.duration_seconds, r.num_turns) == (3.5, 2)


def test_progress_counts_each_step_once_and_tracks_the_current_tool() -> None:
    p = Progress()
    for idx, tool, state in [(0, "run_command", "WORKING"), (0, "run_command", "DONE"),
                             (1, "view_file", "DONE"), (2, "run_command", "WORKING")]:
        p.update("step_update", {"step_index": idx, "tool_name": tool, "state": state,
                                 "conversation_id": "c9"})
    assert p.steps == 3
    assert p.tools == {"run_command": 2, "view_file": 1}
    assert p.current == "run_command (working)"
    assert p.conversation_id == "c9"


def test_progress_records_denials_and_writes_atomically(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = Progress()
    p.update("step_update", {"step_index": 0, "tool_name": "write_file", "state": "DENIED"})
    path = tmp_path / "progress.json"
    p.write(str(path))
    data = json.loads(path.read_text())
    assert data["denied"] == ["write_file"]
    assert data["steps"] == 1
    assert not list(tmp_path.glob("*.tmp.*"))
