---
description: Delegate a well-scoped subtask to Antigravity (agy / Gemini 3.8 Flash) as a background job, then verify.
argument-hint: "[--wait] [--continue] [--readonly] <task>"
---

Delegate the following task to Antigravity, following the `antigravity` skill's
**Cost discipline** and **Verification gates**.

Raw arguments: $ARGUMENTS

Flags (strip them from the task text):

- `--continue` (or "keep going", "fix what you left"): resume the same agy conversation.
- `--readonly`: the task only reads (review, analysis, research); agy gets
  `--isolation readonly`.
- `--wait`: block on the result in the foreground (the headless loop at the end) instead
  of waiting in the background. `--background` is the default and accepted as a no-op.

Never add `--tier`, `--model` or `--yolo`: the model is locked to Gemini 3.8 Flash (High),
and agy always runs in the bubblewrap jail (the repository is writable, the rest of the
filesystem is not).

Every delegation runs as a job, the Codex `--background` pattern: nothing sits waiting on agy.

1. From the repository root, start it with one Bash call:
   `agy-job start [--isolation readonly] [--continue] "<task>"`. It prints the job id and
   returns at once. The job runs with a 30-minute agy limit (plugin option `job_timeout`).
2. Run `agy-job wait <id>` with the Bash tool's `run_in_background: true`. Tell the user
   the job started, then carry on with other work or end your turn. Claude Code notifies you
   when the wait exits. Do not poll, sleep, or loop on `agy-job status`.
3. On the notification, read that background command's output: agy's reply, its stderr,
   and a final `[exit rc=<code>: ...]` line. (`agy-job result <id>` prints the same again.)

Then read the exit code first:

- `0`: verify (below).
- `12` timeout: the reply may be empty, but files may already be changed. Check
  `git status`, then resume with `--continue` (as a job) if the work is unfinished.
- `16`: the jail is unavailable (no bwrap). Report it; never switch to `--isolation off`
  yourself.
- other codes: report agy's `AGY_SIGNAL` line to the user.
- If agy says a path was read-only (the wrapper also prints an `isolation_writable` note),
  tell the user which path; do not work around the jail in the repository.

**Verify** it yourself: `git status` / `git diff`, run the tests and the commands the task
names, and compare agy's reported numbers against your own rerun. agy's report is a claim,
not evidence. Watch for loosened thresholds, weakened or skipped tests, config edits that
work around the jail, stray files, and work done smaller than asked (fewer runs or trials,
missing cases) that the report does not mention. Then either fix small issues yourself or send one
`--continue` follow-up that lists the concrete defects.

Remember the break-even: delegate only if the offloaded volume clearly exceeds the spec,
round-trip and verification overhead. Tiny tasks are cheaper to do yourself.

Headless (`claude -p`) or `--wait`: there is no later turn for a notification. Start the job, then run
`agy-job wait <id> --timeout 9m` in the foreground, repeating while it prints
"still running" (exit 2).
