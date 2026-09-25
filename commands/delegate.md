---
description: Delegate a well-scoped subtask to Antigravity (agy / Gemini 3.8 Flash) through the antigravity-delegate subagent, then verify.
argument-hint: "[--background|--wait] [--continue] [--readonly] <task>"
---

Delegate the following task to Antigravity through the `antigravity:antigravity-delegate`
subagent, following the `antigravity` skill's **Cost discipline** and **Verification gates**.

Raw arguments: $ARGUMENTS

How to run it (the Codex `/codex:rescue` pattern):

1. Invoke the subagent with the `Agent` tool (`subagent_type: "antigravity:antigravity-delegate"`).
   Its prompt is the task text, plus "read-only" if the task only reads (review, analysis,
   research) and "continue the previous agy run" for a follow-up.
   - `--background`, or no flag and the task is multi-step or long (implementing a spec,
     generating a test suite, a migration): run the subagent in the background, tell the
     user it started, and keep working. You are notified when it returns. Do not poll,
     sleep or run `agy-job status` loops.
   - `--wait`, or a small bounded task: run it in the foreground.
   - `--continue` / "keep going" / "fix what you left": say so in the subagent prompt; it
     passes `--continue`, which resumes the same agy conversation.
   - `--readonly`: say "read-only" in the prompt.
   Strip these flags from the task text. Do not add `--tier`, `--model` or `--yolo`: the
   model is locked to Gemini 3.8 Flash (High), and agy always runs in the bubblewrap jail
   (the repository is writable; the rest of the filesystem is not).
2. When the subagent returns, read its `EXIT <code>` line first:
   - `0`: verify (step 3).
   - `12` timeout: the reply may be empty, but files may already be changed. Check
     `git status`, then send a follow-up with `--continue` if the work is unfinished.
   - `16`: the jail is unavailable (no bwrap). Report it; never switch to
     `--isolation off` yourself.
   - other codes: report agy's `AGY_SIGNAL` line to the user.
3. **Verify** it yourself: `git status` / `git diff`, run the tests and the commands the
   task names, and compare agy's reported numbers against your own rerun. agy's report is a
   claim, not evidence. Watch for loosened thresholds, weakened or skipped tests, and stray
   files. Then either fix small issues yourself or send one `--continue` follow-up that lists
   the concrete defects.

Remember the break-even: delegate only if the offloaded volume clearly exceeds the spec,
round-trip and verification overhead. Tiny tasks are cheaper to do yourself.

If you are headless (`claude -p`), run the subagent in the foreground: there is no later
turn to collect a background result.
