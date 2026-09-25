#!/usr/bin/env bash
#
# agy-job.sh — background-job layer over agy-delegate.sh, à la `codex --background`.
# For INTERACTIVE Claude Code sessions: fire a long delegation, keep working, then
# poll :status / fetch :result. (Headless `claude -p` is one-shot — use the wrapper
# synchronously there instead; there is no later turn to collect the result.)
#
# Usage:
#   agy-job.sh start  [agy-delegate options] "task"   # -> prints a JOB_ID, returns now
#   agy-job.sh list                                    # jobs started from this dir
#   agy-job.sh status <id>                             # running | done(rc) | failed
#   agy-job.sh result <id>                             # print stdout (+rc) when finished
#   agy-job.sh wait   <id>                             # block until finished, then = result
#                                                      # (run it as a background Bash command
#                                                      #  and wait for its exit notification)
#   agy-job.sh cancel <id>                             # terminate a running job
#
# Jobs live under ${ANTIGRAVITY_JOBS:-~/.antigravity-jobs}/<id>/ (out, err, rc, meta).
# A job runs with --timeout 30m unless the args name one (plugin option job_timeout, or
# env AGY_JOB_TIMEOUT). The 5m wrapper default killed the first real job mid-turn.
#
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DELEGATE="${AGY_DELEGATE:-$HERE/agy-delegate.sh}"
REG="${ANTIGRAVITY_JOBS:-$HOME/.antigravity-jobs}"

die() { echo "agy-job: $*" >&2; exit 1; }

# resolve a (possibly abbreviated) id to a job dir
jobdir() {
  [ -n "${1:-}" ] || die "need a job id"
  if [ -d "$REG/$1" ]; then echo "$REG/$1"; return; fi
  local hits; hits=$(ls -d "$REG/$1"* 2>/dev/null)
  [ -n "$hits" ] || die "no such job: $1"
  [ "$(printf '%s\n' "$hits" | grep -c .)" -eq 1 ] || die "ambiguous id '$1'"
  echo "$hits"
}

# echoes running | done | failed. (rc is read directly from the file by callers —
# a global set here would NOT survive the `$(job_state ...)` command-substitution subshell.)
job_state() {
  local jd="$1" rc
  if [ -f "$jd/rc" ]; then
    rc="$(cat "$jd/rc")"
    if [ "$rc" = "0" ]; then echo "done"; else echo "failed"; fi
  elif [ -f "$jd/pid" ] && kill -0 "$(cat "$jd/pid")" 2>/dev/null; then
    echo running
  else
    echo failed   # pid gone, no rc recorded = crashed/killed
  fi
}

# Human label for a delegate exit code (mirrors agy-delegate.sh structured codes).
rc_label() {
  case "$1" in
    0)  echo 'ok' ;;
    2)  echo 'agy failed' ;;
    3)  echo 'empty output' ;;
    10) echo 'QUOTA — retry later with --continue' ;;
    11) echo 'AUTH required — run `agy` once interactively' ;;
    12) echo 'TIMEOUT — the reply may be empty and files may already be changed (check git status); --continue resumes the conversation, or raise --timeout' ;;
    13) echo 'agy MISSING — install the Antigravity CLI' ;;
    14) echo 'MODEL unavailable — check `agy models` / tier remap' ;;
    # Both denial shapes: the soft deny (agy 1.1.3+, and again from 1.1.20) and 1.1.13's hard error.
    15) echo 'PERMISSION denied (soft on 1.1.3+, a hard error by 1.1.13, soft again from 1.1.20; named in denied_actions since 1.1.27) — add a permissions.allow rule, or --yolo' ;;
    16) echo 'ISOLATION unavailable — install bubblewrap (Linux), run from the project dir, or pass --isolation off' ;;
    *)  echo 'error' ;;
  esac
}

cmd="${1:-}"; shift || true
case "$cmd" in
  start)
    [ $# -ge 1 ] || die "start needs delegate args, e.g.  start --tier pro \"task\""
    [ -x "$DELEGATE" ] || die "delegate not executable: $DELEGATE"
    has_timeout=0
    for a in "$@"; do [ "$a" = "--timeout" ] && has_timeout=1; done
    if [ "$has_timeout" -eq 0 ]; then
      set -- --timeout "${AGY_JOB_TIMEOUT:-${CLAUDE_PLUGIN_OPTION_JOB_TIMEOUT:-30m}}" "$@"
    fi
    id="$(date +%Y%m%d-%H%M%S)-$$-${RANDOM}"
    jd="$REG/$id"; mkdir -p "$jd"
    { echo "id=$id"; echo "cwd=$PWD"; echo "started=$(date -u +%FT%TZ 2>/dev/null || date)";
      echo "task=$(printf '%s' "${!#}" | tr '\n' ' ' | cut -c1-200)"; } > "$jd/meta"
    ( nohup "$DELEGATE" "$@" >"$jd/out" 2>"$jd/err"; echo $? >"$jd/rc" ) >/dev/null 2>&1 &
    echo $! > "$jd/pid"
    disown 2>/dev/null || true
    echo "$id"
    ;;
  list)
    [ -d "$REG" ] || { echo "(no jobs)"; exit 0; }
    found=0
    for jd in "$REG"/*/; do
      [ -d "$jd" ] || continue
      cwd="$(sed -n 's/^cwd=//p' "$jd/meta" 2>/dev/null)"
      [ "${ALL:-0}" = "1" ] || [ "$cwd" = "$PWD" ] || continue
      found=1
      st="$(job_state "$jd")"
      printf '%-32s %-8s %s\n' "$(basename "$jd")" "$st" \
        "$(sed -n 's/^task=//p' "$jd/meta" 2>/dev/null)"
    done
    [ "$found" = "1" ] || echo "(no jobs for $PWD — set ALL=1 to see all)"
    ;;
  status)
    jd="$(jobdir "${1:-}")"; st="$(job_state "$jd")"
    rc="$(cat "$jd/rc" 2>/dev/null || true)"
    echo "job:    $(basename "$jd")"
    sed 's/^/  /' "$jd/meta" 2>/dev/null
    if [ -n "$rc" ]; then echo "  state=$st (rc=$rc: $(rc_label "$rc"))"; else echo "  state=$st"; fi
    sig="$(grep -m1 '^AGY_SIGNAL ' "$jd/err" 2>/dev/null || true)"
    if [ -n "$sig" ]; then echo "  signal=${sig#AGY_SIGNAL }"; fi
    ;;
  result|wait)
    jd="$(jobdir "${1:-}")"; st="$(job_state "$jd")"
    if [ "$cmd" = wait ]; then
      while [ "$st" = "running" ]; do sleep "${AGY_JOB_POLL:-5}"; st="$(job_state "$jd")"; done
    fi
    if [ "$st" = "running" ]; then echo "still running — try again later"; exit 2; fi
    rc="$(cat "$jd/rc" 2>/dev/null || true)"
    [ -s "$jd/err" ] && { echo "----- stderr -----" >&2; cat "$jd/err" >&2; }
    cat "$jd/out" 2>/dev/null
    echo "[exit rc=${rc:-?}${rc:+: $(rc_label "$rc")}]" >&2
    ;;
  cancel)
    jd="$(jobdir "${1:-}")"
    pid="$(cat "$jd/pid" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      pkill -P "$pid" 2>/dev/null || true   # children (agy) first
      kill "$pid" 2>/dev/null || true
      echo "cancelled $(basename "$jd")"
    else
      echo "not running"
    fi
    ;;
  ""|-h|--help|help)
    sed -n '/^# Usage:/,/^# Jobs live/p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown subcommand '$cmd' (start|list|status|result|wait|cancel)" ;;
esac
