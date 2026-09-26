---
description: Delegate a well-scoped subtask to Antigravity (agy / Gemini 3.8 Flash) as a background job, then verify.
argument-hint: "[--wait] [--continue] [--readonly] <task>"
---

Delegate the following task to Antigravity, following the `antigravity` skill's
**Cost discipline** and **Verification gates**.

Raw arguments: $ARGUMENTS

Flags (strip them from the task text):

- `--continue` (or "keep going", "fix what you left"): resume that job's agy conversation
  with `agy-job start --resume <job-id> "<follow-up>"`. Plain `--continue` resumes agy's most
  recent conversation, which is another job's once several have run.
- `--readonly`: the task only reads (review, analysis, research); agy gets
  `--isolation readonly`.
- `--wait`: block on the result in the foreground (the headless loop at the end) instead
  of waiting in the background. `--background` is the default and accepted as a no-op.

Never add `--tier`, `--model` or `--yolo`: the model is locked to Gemini 3.8 Flash (High),
and agy always runs in the bubblewrap jail (the repository is writable, the rest of the
filesystem is not).

Every delegation runs as a job, the Codex `--background` pattern: nothing sits waiting on agy.

1. From the repository root, start it with one Bash call:
   `agy-job start [--isolation readonly] [--resume <job-id>] "<task>"`. It prints the job id and
   returns at once. The job runs with a 30-minute agy limit (plugin option `job_timeout`).
2. Launch the wait as a Claude Code background task, exactly like this:
   ```typescript
   Bash({
     command: "agy-job wait <id>",
     description: "Wait for agy job <id>",
     run_in_background: true
   })
   ```
   Never run `agy-job wait <id>` without `run_in_background: true` (unless headless or
   `--wait`, below): a foreground wait blocks the session for up to 30 minutes. Do not wait
   for completion in this turn: tell the user the job started, then carry on with other
   work or end your turn. Claude Code notifies you when the wait exits. Do not poll, sleep,
   or loop on `agy-job status`.
   One wait per job. If a wait for that job is already running (you started it, or the
   harness moved a foreground one to the background), do not start another: its exit
   brings the notification. A second wait refuses with exit 3.
3. On the notification, read that background command's output: agy's reply, its stderr,
   and a final `[exit rc=<code>: ...]` line. (`agy-job result <id>` prints the same again.)

**Several jobs at once** are allowed, as with Codex's background tasks. They share this
checkout (no worktrees), so:

- read-only jobs (`--readonly`) can always run in parallel;
- parallel write jobs must touch separate files or directories. Write that split into each
  task ("only edit src/estimate/, do not touch anything else"). Anything that touches shared
  files, config or dependencies (`pyproject.toml`, lockfiles, `uv sync`, `.gitignore`) runs
  alone;
- `agy-job start` prints a note when other jobs are still running in the same directory;
- verify once all of them have finished: one `git diff`, one test run.

Then read the exit code first:

- `0`: verify (below).
- `12` timeout: the reply may be empty, but files may already be changed. Check
  `git status`, then resume with `agy-job start --resume <job-id>` if the work is unfinished.
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
`--resume <job-id>` follow-up that lists the concrete defects.

Remember the break-even: delegate only if the offloaded volume clearly exceeds the spec,
round-trip and verification overhead. Tiny tasks are cheaper to do yourself.

Headless (`claude -p`) or `--wait`: there is no later turn for a notification. Start the job, then run
`agy-job wait <id> --timeout 9m` in the foreground, repeating while it prints
"still running" (exit 2).
