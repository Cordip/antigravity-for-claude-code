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

## Next

3. **Live trial.** `claude --plugin-dir ~/projects/antigravity-for-claude-code`, then
   `/antigravity:delegate` on a real task in a real repo. Note any friction: hidden paths
   that agy actually needed, timeouts, and output size.
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
