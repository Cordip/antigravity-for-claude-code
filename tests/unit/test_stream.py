from __future__ import annotations

import json

from agy_runner.stream import Progress, Result, parse_line
from conftest import FIXTURES


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


def replay(name: str) -> tuple[Progress, Result | None]:
    p, res = Progress(), None
    for line in (FIXTURES / name).read_text().splitlines():
        ev = parse_line(line)
        assert ev is not None, line
        p.update(*ev)
        if ev[0] == "result":
            res = Result.from_payload(ev[1])
    return p, res


def test_real_stream_tools_run() -> None:
    p, res = replay("agy-1.2.11-tools.ndjson")
    assert res is not None and res.status == "SUCCESS" and res.response == "DONE.\n"
    assert res.usage["total"] == 85102 and res.num_turns == 1
    # 12 distinct steps: user input, 6 agent responses, 5 tools (each tool arrives twice).
    assert p.steps == 12
    assert p.tools == {"run_command": 2, "view_file": 1, "write_to_file": 2}
    assert p.commands == ["ls src/field_length", "cat /nonexistent-file-for-test"]
    probe = "/home/user/projects/field-length/README-probe.md"
    assert p.files_written == ["/tmp/agy-sample.txt", probe]
    assert p.errors == 1
    assert "read-only file system" in p.last_error
    assert p.error_messages == [f"open {probe}: read-only file system"]
    assert p.text == "DONE.\n"
    # Per-step usage of the agent responses, which is what a cut-off run has to go on.
    assert p.tokens == 13355 + 13582 + 14199 + 14409 + 14661 + 14896


def test_real_stream_print_timeout_still_ends_with_a_result() -> None:
    p, res = replay("agy-1.2.11-print-timeout.ndjson")
    assert res is not None and res.response == ""
    assert res.usage["total"] == 13275  # the finished steps only
    assert p.current == "run_command: sleep 40 (active)"


def test_real_stream_resume_keeps_the_conversation_and_counts_cumulatively() -> None:
    _, r1 = replay("agy-1.2.11-tools.ndjson")
    p3, r3 = replay("agy-1.2.11-resume.ndjson")
    assert r1 is not None and r3 is not None
    assert r3.conversation_id == r1.conversation_id == p3.conversation_id
    # result.duration / num_turns / usage cover the whole conversation.
    assert r3.num_turns == 2 and r3.duration_seconds > r1.duration_seconds
    assert r3.usage["total"] > r1.usage["total"]
