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

## Next

3. **Live trial 3.** A short run of the new job path: `/antigravity:delegate` in
   `field-length` on a bounded task that runs `uv run pytest`. Check that the result
   arrives as a notification, uv uses the shared cache, and no subagent row is left.
4. **Port the core to TypeScript.** Replace `agy-delegate.sh` with a TS runner. Reuse
   `driver.ts` / `streaming.ts` from `codex-antigravity-subagent` for stream-json
   progress and persistent sessions. Keep the commands and the subagent. Later, possibly
   a Claude Code mod (`tool.register` + a progress pane) once function hooks leave
   early access.

## Open questions

- Upstream PR for `--isolation`? The upstream suite (bash 3.2 / macOS CI) must stay green,
  and the isolation tests skip on non-Linux.
- macOS equivalent (`sandbox-exec` profile)? Not needed for now.
- Network stays open inside the jail (agy needs the Google API). Reads outside the hidden
  paths are unrestricted. This is the same as Codex `workspace-write`.
