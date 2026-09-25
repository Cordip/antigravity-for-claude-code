# Fork plan

Working plan for this fork (`Cordip/antigravity-for-claude-code`). Goal: Claude Code
delegates a task to agy (Gemini 3.8 Flash), agy works in the current checkout inside a
bubblewrap jail, and Claude reviews the diff. Same ergonomics as the Codex plugin: no
worktree, no permission rules, no approval prompts.

## Done

- [x] **bwrap isolation** (`--isolation`, 0.29.0, branch `feat/bwrap-isolation`).
  agy runs with `--dangerously-skip-permissions` inside a jail. Only the repo, `--dir` and
  `~/.gemini` are writable, `/tmp` is private, and credential directories are hidden.
  Verified end to end on agy 1.2.11 / WSL2: writes outside the repo fail with
  `Read-only file system`, while shell, `git commit` and web search work.

- [x] **Subagent and commands for the jailed setup.** The agent, `/delegate` and SKILL.md
  no longer ask for `--yolo` or `permissions.allow` rules. `delegation_nudge` is off by
  default.
- [x] **Jail always on.** `--isolation` defaults to `workspace` and fails closed (exit 16)
  when bwrap is missing. `--yolo` is gone from all examples.
- [x] **`media` and `research` on `--isolation readonly`.** In `agy-media` only the
  transcript directory is writable. `/research` and the web-search recipes run readonly.

- [x] **Live trial 1** (2026-09-25, `~/projects/field-length`, "implement phase 1 of
  SPEC.md"). Claude used `agy-job start --tier pro --dir .` twice (the second run with
  `--timeout 45m --continue`). There were no permission prompts, no classifier blocks, no
  exit 16, no uv, network or hidden-path errors, and no output-size problems. The
  isolation mode was not logged, but it was most likely the default `workspace`.
  Findings:
  - **Claude picked `--tier pro`** (Gemini 3.1 Pro), not Flash. The model is not
    actually pinned.
  - **The 5m default timeout killed round 1** (rc=12 after 299 s). `out` was 0 bytes
    despite the "PARTIAL reply is in the output" hint. Round 2 took 837 s and 545k
    tokens.
  - **Claude polled with a `sleep 20` bash loop.** It did not use Monitor or a
    completion notification.
  - **Quality.** agy loosened a test threshold (2.0 → 20.0), weakened the oracle test,
    and reported a B median of 12 m while B actually failed on every trial. It left a
    debug script in the repo root. Claude's review caught all of this.

- [x] **Fixes from trial 1** (0.30.0): model locked to Flash High (`model_lock`), the
  subagent is a thin Sonnet forwarder spawned in the background (Codex `rescue` pattern),
  `agy-job` defaults to 30m and gained `wait`, the timeout message no longer promises a
  partial reply, work rules are appended to every task, and `AGY_RUN` logs model, jail and
  timeout before each run.

- [x] **Live trial 2** (2026-09-25, same repo, branch `agy/phase1-test2`, Flash High,
  workspace jail, via the 0.30.0 subagent in the background). Two runs: the first hit the
  30m limit (exit 12, 1.33M tokens), one `--continue` follow-up finished with exit 0
  (1.57M tokens cumulative). Committed as `9aa9a19`. Findings:
  - **The work rules held.** No loosened thresholds or weakened tests; agy reported a
    regression honestly ("not tuned to pass"), and Claude's reruns matched its numbers.
    One trailing blank line in `.gitignore` was left over.
  - **The first 30 minutes were mostly agy waiting** on a test run it had started in the
    background itself.
  - **The subagent polled** after its Bash call was moved to the background at 10
    minutes, and its row stayed "running" in the UI. The main agent twice told the user
    nothing was running.
  - **The Bash gate never ran:** plugin agent frontmatter `hooks` are ignored.
  - **uv could not write `~/.cache/uv`** in the jail; agy added a cache dir to
    `pyproject.toml` / `.gitignore`, reverted on Claude's follow-up.
  - Quality: the first pass cut corners (markings fit on two features, wrong oracle dict
    shape, no README); the follow-up fixed all five listed defects. The estimator still
    gives a length in only 35% of wide shots.
- [x] **Fixes from trial 2** (0.31.0): long work runs as `agy-job` + background
  `agy-job wait` (Codex `--background` pattern), the subagent is bounded to 7 minutes,
  the Bash gate is registered in `hooks.json` scoped by `agent_type`, the jail has a
  private XDG cache plus the shared package caches (`shared_caches`,
  `isolation_writable`), and two more work rules.

- [x] **Live trial 3** (0.31.0, small task: "add a test and run uv run pytest"). Claude
  picked the subagent path; one 7m call, exit 0 after 262 s, 270k tokens, one new test
  file, uv worked in the jail, the subagent closed cleanly. Claude itself called the
  delegation a net loss for a task that small.
- [x] **Live trial 4** (0.31.0, `--background`, "run experiments E2–E5, fix what fails").
  Job path as designed: `agy-job start`, background `agy-job wait`, a completion
  notification, no polling. Exit 0 after 624 s, 534k tokens, no jail or cache errors.
  Claude's rerun matched agy's numbers and confirmed a real bug fix, but agy ran 10–15
  trials where the spec asks for 100/200 and dropped some breakdowns without saying so.
- [x] **One path** (0.32.0): the delegate subagent and its Bash gate are removed; every
  delegation is an `agy-job`. New work rule: say so when less was done than asked.

- [x] **Parallel jobs and per-job follow-ups** (0.33.0): `agy-job start --resume <job-id>`,
  the parallel-write rule, a note on other running jobs.

- [x] **Python port** (0.34.0, planned as a TS port; Python chosen because the jail
  already requires `python3`, so it adds no runtime and no build step). `src/agy_runner`,
  stdlib only, uv for development. agy >= 1.2 with stream-json: live progress per job,
  idle timeout, the whole process tree stopped on timeout / cancel.

## Next

3. **Live trial 5** on the Python runner: a real job in `field-length`, checking
   `agy-job status` progress mid-run, the notification, and `--resume`.
4. Open: agy's `result` usage, `duration_seconds` and `num_turns` are cumulative over a
   resumed conversation (measured); per-run numbers would need the difference to the
   previous job's result.

## Backlog

- **Claude Code mod (optional UI layer).** Mods are plugins with a TypeScript hooks module
  (`anthropics/claude-code/mods`, early access, needs
  `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`, API changed between 2.1.277 and 2.1.282). Plan:
  `agy_delegate` / `agy_status` / `agy_result` / `agy_cancel` tools via `$.tool.register`,
  progress in `$.ui.status` and a pane polled with `$.clock.every`, completion via
  `$.prompt.submit`, all on top of the job runner (a mod cannot own a process past 10
  minutes). Estimated gain is modest (job visibility, no Bash quoting or prompts); revisit
  after the TS port gives real stream-json progress and the function hooks API settles.

## Open questions

- Upstream PR for `--isolation`? The upstream suite (bash 3.2 / macOS CI) must stay green,
  and the isolation tests skip on non-Linux.
- macOS equivalent (`sandbox-exec` profile)? Not needed for now.
- Network stays open inside the jail (agy needs the Google API). Reads outside the hidden
  paths are unrestricted. This is the same as Codex `workspace-write`.
