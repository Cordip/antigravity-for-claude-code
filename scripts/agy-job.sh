#!/usr/bin/env bash
#
# agy-job.sh — background-job layer over agy-delegate.sh, à la `codex --background`.
# For INTERACTIVE Claude Code sessions: fire a long delegation, keep working, then
# poll :status / fetch :result. (Headless `claude -p` is one-shot — use the wrapper
# synchronously there instead; there is no later turn to collect the result.)
#
# Usage:
#   agy-job.sh start  [agy-delegate options] "task"   # -> prints a JOB_ID, returns now
#   agy-job.sh start  --resume <id> [options] "task"   # follow-up in job <id>'s own agy
#                                                      # conversation (--conversation), not
#                                                      # agy's most recent one (--continue)
#   agy-job.sh list                                    # jobs started from this dir
#   agy-job.sh status <id>                             # running | done(rc) | failed
#   agy-job.sh result <id>                             # print stdout (+rc) when finished
#   agy-job.sh wait   <id> [--timeout <dur>]           # block until finished, then = result;
#                                                      # --timeout (e.g. 9m) gives up with
#                                                      # "still running", exit 2. Run it as a
#                                                      # background Bash command and wait for
#                                                      # its exit notification.
#   agy-job.sh cancel <id>                             # terminate a running job (agy included)
#
# Jobs live under ${ANTIGRAVITY_JOBS:-~/.antigravity-jobs}/<id>/ (out, err, rc, meta).
# A job runs with --timeout 30m unless the args name one (plugin option job_timeout, or
# env AGY_JOB_TIMEOUT). The 5m wrapper default killed the first real job mid-turn.
#
# Several jobs may run at once in one checkout, as with Codex's background tasks: nothing
# here isolates them, so parallel WRITE jobs must work on separate files (the caller's
# job). `start` says how many other jobs are running in the same directory. Each job's
# agy conversation id is read from its AGY_USAGE line, so --resume <id> continues the
# right conversation even when several jobs have run since.
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

# agy conversation id of a job, from its AGY_USAGE line (JSON mode). Empty when agy
# printed none: plain-text mode, or the wall-clock guard killed agy before its envelope.
job_conv() {
  grep -m1 '^AGY_USAGE ' "$1/err" 2>/dev/null \
    | sed -n 's/.*"conversation_id": *"\([^"]*\)".*/\1/p'
}

# Human label for a delegate exit code (mirrors agy-delegate.sh structured codes).
rc_label() {
  case "$1" in
    0)  echo 'ok' ;;
    2)  echo 'agy failed' ;;
    3)  echo 'empty output' ;;
    10) echo 'QUOTA — retry later with --continue' ;;
    11) echo 'AUTH required — run `agy` once interactively' ;;
    12) echo 'TIMEOUT — the reply may be empty and files may already be changed (check git status); agy-job start --resume <this id> continues the conversation, or raise --timeout' ;;
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
    resume=""; args=()
    while [ $# -gt 0 ]; do
      case "$1" in
        --resume) [ $# -ge 2 ] || die "--resume needs a job id"; resume="$2"; shift 2 ;;
        *) args+=("$1"); shift ;;
      esac
    done
    [ "${#args[@]}" -ge 1 ] || die "start needs a task"
    resumed_from=""
    if [ -n "$resume" ]; then
      rjd="$(jobdir "$resume")"
      [ "$(job_state "$rjd")" != running ] || die "job $(basename "$rjd") is still running; wait for it before resuming"
      for a in "${args[@]}"; do
        case "$a" in -c|--continue|--conversation) die "use --resume or --continue/--conversation, not both" ;; esac
      done
      conv="$(job_conv "$rjd")"
      [ -n "$conv" ] || die "job $(basename "$rjd") recorded no agy conversation id (no AGY_USAGE line: plain-text mode, or agy was killed before replying); --continue resumes agy's most recent conversation instead"
      args=(--conversation "$conv" "${args[@]}")
      resumed_from="$(basename "$rjd")"
    fi
    set -- "${args[@]}"
    has_timeout=0
    for a in "$@"; do [ "$a" = "--timeout" ] && has_timeout=1; done
    if [ "$has_timeout" -eq 0 ]; then
      set -- --timeout "${AGY_JOB_TIMEOUT:-${CLAUDE_PLUGIN_OPTION_JOB_TIMEOUT:-30m}}" "$@"
    fi
    # Other jobs still running in this directory: parallel writers share the checkout.
    others=0
    for ojd in "$REG"/*/; do
      [ -d "$ojd" ] || continue
      [ "$(sed -n 's/^cwd=//p' "$ojd/meta" 2>/dev/null)" = "$PWD" ] || continue
      [ "$(job_state "${ojd%/}")" = running ] && others=$((others+1))
    done
    id="$(date +%Y%m%d-%H%M%S)-$$-${RANDOM}"
    jd="$REG/$id"; mkdir -p "$jd"
    { echo "id=$id"; echo "cwd=$PWD"; echo "started=$(date -u +%FT%TZ 2>/dev/null || date)";
      echo "task=$(printf '%s' "${!#}" | tr '\n' ' ' | cut -c1-200)";
      [ -z "$resumed_from" ] || echo "resumed_from=$resumed_from"; } > "$jd/meta"
    # Job control on: the job gets its own process group (pgid = its pid), so cancel can
    # stop the whole tree. Killing only the subshell and its children used to leave
    # timeout/bwrap/agy running.
    set -m
    ( nohup "$DELEGATE" "$@" >"$jd/out" 2>"$jd/err"; echo $? >"$jd/rc" ) >/dev/null 2>&1 &
    echo $! > "$jd/pid"
    disown 2>/dev/null || true
    set +m
    echo "$id"
    echo "agy-job: started $id. Collect it with: agy-job wait $id (as a background Bash command; you are notified when it exits)" >&2
    if [ "$others" -gt 0 ]; then
      echo "agy-job: note: $others other job(s) still running in $PWD. They share this checkout: parallel write jobs must touch separate files. agy-job list shows them." >&2
    fi
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
    conv="$(job_conv "$jd")"
    if [ -n "$conv" ]; then echo "  conversation=$conv (follow up: agy-job start --resume $(basename "$jd") \"<task>\")"; fi
    ;;
  result|wait)
    jd="$(jobdir "${1:-}")"; st="$(job_state "$jd")"
    if [ "$cmd" = wait ]; then
      limit=0
      if [ "${2:-}" = "--timeout" ]; then
        [ -n "${3:-}" ] || die "wait --timeout needs a duration, e.g. 9m"
        n="${3%[smh]}"; unit="${3#"$n"}"
        case "$n" in (*[!0-9]*|'') die "bad --timeout '$3' (use e.g. 540, 540s, 9m, 1h)" ;; esac
        case "$unit" in h) limit=$(( n * 3600 )) ;; m) limit=$(( n * 60 )) ;; *) limit=$n ;; esac
      elif [ -n "${2:-}" ]; then
        die "unknown wait option '$2' (use --timeout <dur>)"
      fi
      start_s=$SECONDS
      while [ "$st" = "running" ]; do
        if [ "$limit" -gt 0 ] && [ $(( SECONDS - start_s )) -ge "$limit" ]; then break; fi
        sleep "${AGY_JOB_POLL:-5}"; st="$(job_state "$jd")"
      done
    fi
    if [ "$st" = "running" ]; then echo "still running — try again later"; exit 2; fi
    rc="$(cat "$jd/rc" 2>/dev/null || true)"
    [ -s "$jd/err" ] && { echo "----- stderr -----" >&2; cat "$jd/err" >&2; }
    cat "$jd/out" 2>/dev/null
    echo "[exit rc=${rc:-?}${rc:+: $(rc_label "$rc")}]" >&2
    if [ -n "$(job_conv "$jd")" ]; then
      echo "[follow up in this job's conversation: agy-job start --resume $(basename "$jd") \"<task>\"]" >&2
    fi
    ;;
  cancel)
    jd="$(jobdir "${1:-}")"
    pid="$(cat "$jd/pid" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      # The whole process group (jobs started since 0.31.0 lead their own); the old
      # children-first kill stays as the fallback for jobs started before that.
      kill -- "-$pid" 2>/dev/null || true
      pkill -P "$pid" 2>/dev/null || true
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
