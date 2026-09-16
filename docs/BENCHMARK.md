# Benchmark: Claude Code alone vs Claude Code + this plugin on real pull requests

_Status: complete (full run 2026-09-15/16; 75 runs). Every table on this page is regenerated from
`bench/results/<run-id>/aggregate.json` by `tests/check-bench-claims.py`; a number that
is not inside a `bench:table` block is not a measurement._

## The question

Does delegating implementation work to agy (Gemini Flash) through this plugin cost less
per **successful** task than Claude Code alone, without lowering quality — and where is
the break-even by task size? The repo's own playbook predicts parity on repo-editing
work; this study is designed to find the size at which that stops being true, if it does,
and to publish the answer either way.

## How it is measured (summary; protocol in `bench/PROTOCOL.md`)

- **Tasks**: merged pull requests from public Go repositories (caddyserver/caddy, cli/cli,
  grafana/k6, sourcegraph/zoekt), merged after 2026-07-01 — after the models' training cutoffs. A task is the
  repository at the PR's parent commit plus the PR's test files; the requirement is a
  20–30 line prompt written from the PR description without naming files or identifiers
  the author introduced (linted). Every task is verified: the hidden tests fail at the
  base commit and pass three times in a row at the merge commit.
- **Arms**: `solo-opus` (reference), `solo-sonnet`, `hybrid-inst` (plugin loaded, prompt
  asks for delegation, Claude keeps its file tools), `hybrid-forced` (same, but Claude has
  no Edit/Write; agy is the only way to change a file). One verification-only Bash
  policy in every arm, identical caps per size class, identical prompts, cold starts.
- **Outcome**: the PR's test files are restored over the agent's tree (tamper check first),
  then build, vet, hidden tests, full suite, gofmt. `pass` needs all of them.
- **Cost**: Claude Code's list-price `modelUsage.costUSD` plus the Gemini side priced from
  the wrapper's `AGY_USAGE_LOG` (`input×in + output×out + cache_read×cached_in`, a lower
  bound). Metric: cost-of-pass = total spend ÷ passes. Both sides are pay-as-you-go on
  one GCP project here and are reconciled against its billing export.
- **Attribution**: a hook fingerprints the working tree around every tool call, so each
  change is attributed to a Claude file tool, a Claude shell command, or agy.
- **Quality review**: blinded single-candidate scoring on six axes by Claude Fable 5.1
  and Gemini 3.1 Pro, with the author's patch and an empty patch mixed in unlabelled.

## Tasks

| id | repo | PRs | class | author code lines / files | hidden test files |
|---|---|---|---|---|---|
| caddy-7877 | caddyserver/caddy | [#7877](https://github.com/caddyserver/caddy/pull/7877) | small | 52 / 1 | 1 |
| caddy-7995 | caddyserver/caddy | [#7995](https://github.com/caddyserver/caddy/pull/7995) | small | 57 / 4 | 1 |
| caddy-7888 | caddyserver/caddy | [#7888](https://github.com/caddyserver/caddy/pull/7888) | medium | 176 / 1 | 1 |
| caddy-7913 | caddyserver/caddy | [#7913](https://github.com/caddyserver/caddy/pull/7913) | medium | 570 / 10 | 2 |
| cli-14136 | cli/cli | [#14136](https://github.com/cli/cli/pull/14136) | medium | 199 / 3 | 5 |
| k6-6169 | grafana/k6 | [#6169](https://github.com/grafana/k6/pull/6169) | large | 1,364 / 16 | 13 |
| cli-attach | cli/cli | [#14177–#14184](https://github.com/cli/cli/pull/14186) | large | 2,242 / 22 | 22 |
| zoekt-1105 | sourcegraph/zoekt | [#1105](https://github.com/sourcegraph/zoekt/pull/1105) | large | 749 / 4 | 4 |

Curation notes are in each `bench/tasks/<repo>/<id>/task.json` (`verify`): k6-6169 skips
three HTTP/2 tests that fail 3/3 with the author's own patch on this machine and drops one
hidden test that flaked 1/6. Rejected: caddy #7858 (Windows-only tests pass at base),
cli #14179 alone (not isolable from its stack).

## Pilot (2026-09-14/15; n = 1 per cell — calibration, not a claim)

**Smoke, small task caddy-7877, all four arms:**

<!-- bench:table run=smoke kind=arms -->
| arm | runs | pass | cost-of-pass $ (deck) | cost-of-pass $ (billed rates) | median $ among passes (min–max) | Claude $ | Gemini $ deck / billed | wall med s | turns med | delegations med (0-runs) | denials med | warm starts | caps hit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hybrid-forced | 1 | 1/1 | 1.34 | 1.62 | 1.34 (1.34–1.34) | 1.06 | 0.28 / 0.56 | 478.80 | 19 | 2 (0) | 2 | 0 | 0 |
| hybrid-inst | 1 | 1/1 | 1.41 | 1.77 | 1.41 (1.41–1.41) | 1.05 | 0.36 / 0.72 | 594.70 | 23 | 1 (0) | 2 | 0 | 0 |
| solo-opus | 1 | 1/1 | 0.93 | 0.93 | 0.93 (0.93–0.93) | 0.93 | 0.00 / 0.00 | 241.90 | 15 | 0 (1) | 0 | 0 | 0 |
| solo-sonnet | 1 | 1/1 | 0.72 | 0.72 | 0.72 (0.72–0.72) | 0.72 | 0.00 / 0.00 | 339.60 | 14 | 0 (1) | 1 | 0 | 0 |
<!-- /bench:table -->

**Pilot, medium caddy-7913 and large k6-6169, `solo-opus` vs `hybrid-forced`:**

<!-- bench:table run=pilot kind=arms -->
| arm | runs | pass | cost-of-pass $ (deck) | cost-of-pass $ (billed rates) | median $ among passes (min–max) | Claude $ | Gemini $ deck / billed | wall med s | turns med | delegations med (0-runs) | denials med | warm starts | caps hit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hybrid-forced | 1 | 0/1 | — | — | — (—–—) | 5.72 | 3.24 / 6.47 | 3600.20 | 40 | 8 (0) | 0 | 0 | 1 |
| solo-opus | 2 | 2/2 | 8.72 | 8.72 | 8.72 (4.76–12.68) | 17.45 | 0.00 / 0.00 | 1302.85 | 85.50 | 0.00 (2) | 4.50 | 0 | 0 |
<!-- /bench:table -->

<!-- bench:table run=pilot kind=size -->
| arm | size | runs | pass | cost-of-pass $ (deck) | cost-of-pass $ (billed rates) | median $ among passes | wall med s |
|---|---|---|---|---|---|---|---|
| hybrid-forced | medium | 1 | 0/1 | — | — | — | 3600.20 |
| hybrid-forced | large | 0 | 0/0 | — | — | — | — |
| solo-opus | medium | 1 | 1/1 | 4.76 | 4.76 | 4.76 | 797.70 |
| solo-opus | large | 1 | 1/1 | 12.68 | 12.68 | 12.68 | 1808.00 |
<!-- /bench:table -->

What the pilot established, and what it changed:

- Both `hybrid-forced` runs were **killed by the pilot's wall caps** (60 min medium,
  100 min large) with trees that already passed the hidden tests and the full suite. Under
  the protocol a capped run is a failure, so their cost-of-pass is undefined here; their
  spend is nevertheless known exactly from the transcripts: **$8.96** (Claude $5.72 +
  Gemini $3.24) on the medium task versus **$4.76** for `solo-opus`, and **$20.60**
  (Claude $16.05 + Gemini $4.55) versus **$12.68** on the large task. Wall-clock ran
  4.5× and 3.3× longer. The delegations themselves took 37 and 62 minutes of agy time
  (8 and 14 calls; one failed, three hit agy's 10-minute print timeout). The full-run caps
  were set from these numbers so that none binds.
- On these two tasks the hybrid's **Claude side alone cost more than the solo run**:
  orchestration (reading to write specifications, verifying) churns the prompt cache —
  1.6 M cache-write tokens versus 0.44 M for solo on the large task.
- `go vet` failed for both arms on k6 in files neither touched; the base commit has the
  same two findings under Go 1.27. The vet gate is now relative to the base commit and
  both records were re-accounted (`solo-opus` passes; the hybrid stays a capped failure).
- The large hybrid run carries `claude_side_write_in_forced_arm`: a tree change during a
  `go doc` call 74 s in, before any delegation. The pilot ran Go with `-mod=mod`, which lets
  `go` commands rewrite `go.mod`/`go.sum`; the trace then recorded only a digest, so the
  files cannot be named after the fact. The full run uses `-mod=readonly` and the hook
  now logs the changed file list, which makes that classification possible.
- Every accounting cross-check held: Claude Code's `total_cost_usd` versus the frozen
  price deck within 0.03 percent, transcript usage versus the result object within
  tolerance, every `AGY_USAGE` line joined by its own `model`/`tier` fields, no warm starts.

## Full run — results (2026-09-15 02:43Z to 2026-09-16 18:21Z)

**Headline.** On eight merged pull requests from four public Go repositories, Claude
Code with this plugin delegating the implementation to agy cost **more per successful
task than Claude Code alone, at equal test outcomes, and took 3.8–4.6× the wall-clock**.
Paired on the same tasks, cost-of-pass was **1.33× `solo-opus` for `hybrid-forced`**
(95% CI 1.03–1.78) and **1.68× for `hybrid-inst`** (1.34–2.12) at the pre-registered
price deck; at the unit prices the project was actually billed (Gemini 3.8 Flash at twice
the deck's promotional rate, see below) 1.75× (1.39–2.28) and 2.08× (1.67–2.66). Every
hybrid run passed its hidden tests and the repository's full suite (16/16 and 16/16), as
did every `solo-opus` run (21/21). `solo-sonnet` cost 0.58× `solo-opus` (0.42–0.83) and
passed 17/20 — two of its failures were the turn cap with a passing tree. No size class
showed a saving. The closest to parity was the largest task, cli-attach (2,242 lines),
where `hybrid-forced` cost 0.87× `solo-opus` on n = 2 per arm.

- **H1 (cost)** — not supported: no class has a ratio below 1; the overall CIs exclude 1
  at both price bases.
- **H2 (quality)** — pass rates non-inferior (−0 pp); judge means non-inferior for
  `hybrid-inst` under both judges and for `hybrid-forced` under the Gemini judge; under
  the Claude judge `hybrid-forced` sits 0.28 below `solo-opus` (3.74 vs 4.02, margin 0.3).
- **H3 (mechanism)** — satisfied: every hybrid run delegated (median 4–5 calls) and agy
  wrote files in every one, so the hybrid numbers measure delegation, not a baseline.

Design as run: 75 counted runs over 32 task × arm cells (11 cells at n = 3, 21 at n = 2;
the operator stopped the run at n ≥ 2 per cell, see the deviations log). Two `hybrid-forced`
runs are excluded by the pre-registered rule: the executor used `search_web` to look for
the upstream file or pull request (caddy-7888 r1, zoekt-1105 r1; both had passed).

<!-- bench:table run=full kind=arms -->
| arm | runs | pass | cost-of-pass $ (deck) | cost-of-pass $ (billed rates) | median $ among passes (min–max) | Claude $ | Gemini $ deck / billed | wall med s | turns med | delegations med (0-runs) | denials med | warm starts | caps hit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hybrid-forced | 16 | 16/16 | 10.89 | 14.34 | 6.74 (0.74–38.03) | 118.98 | 55.24 / 110.49 | 2451.60 | 36.00 | 5.00 (0) | 3.00 | 0 | 0 |
| hybrid-inst | 16 | 16/16 | 13.77 | 17.06 | 6.14 (1.06–48.17) | 153.33 | 67.05 / 119.66 | 2974.25 | 59.50 | 4.00 (0) | 4.50 | 3 | 0 |
| solo-opus | 21 | 21/21 | 8.22 | 8.22 | 4.32 (0.65–40.92) | 172.55 | 0.00 / 0.00 | 653.10 | 63 | 0 (21) | 2 | 0 | 0 |
| solo-sonnet | 20 | 17/20 | 4.74 | 4.74 | 2.28 (0.46–8.83) | 80.50 | 0.00 / 0.00 | 676.40 | 69.00 | 0.00 (20) | 3.00 | 0 | 2 |
<!-- /bench:table -->
<!-- bench:table run=full kind=size -->
| arm | size | runs | pass | cost-of-pass $ (deck) | cost-of-pass $ (billed rates) | median $ among passes | wall med s |
|---|---|---|---|---|---|---|---|
| hybrid-forced | small | 5 | 5/5 | 2.40 | 3.26 | 2.93 | 924.40 |
| hybrid-forced | medium | 6 | 6/6 | 6.62 | 8.75 | 6.74 | 2451.60 |
| hybrid-forced | large | 5 | 5/5 | 24.50 | 32.14 | 24.87 | 6976.50 |
| hybrid-inst | small | 4 | 4/4 | 3.29 | 4.42 | 3.18 | 969.15 |
| hybrid-inst | medium | 6 | 6/6 | 5.84 | 7.41 | 5.90 | 2074.60 |
| hybrid-inst | large | 6 | 6/6 | 28.70 | 35.14 | 28.11 | 6580.00 |
| solo-opus | small | 5 | 5/5 | 1.98 | 1.98 | 2.17 | 399.40 |
| solo-opus | medium | 8 | 8/8 | 4.41 | 4.41 | 4.07 | 576.35 |
| solo-opus | large | 8 | 8/8 | 15.92 | 15.92 | 11.83 | 1487.95 |
| solo-sonnet | small | 5 | 4/5 | 1.98 | 1.98 | 1.32 | 485.10 |
| solo-sonnet | medium | 8 | 8/8 | 1.81 | 1.81 | 2.46 | 630.70 |
| solo-sonnet | large | 7 | 5/7 | 11.62 | 11.62 | 2.73 | 870.90 |
<!-- /bench:table -->
<!-- bench:table run=full kind=paired -->
| comparison | tasks | ratio (deck) | 95% CI | ratio (billed rates) | 95% CI | ratio (Claude side only) | 95% CI | pass-rate diff | undefined draws |
|---|---|---|---|---|---|---|---|---|---|
| hybrid-forced_vs_solo-opus | 8 | 1.33 | [1.0315, 1.7782] | 1.75 | [1.3913, 2.2794] | 0.91 | [0.6726, 1.2789] | 0.00 | 0/10000 |
| hybrid-forced_vs_solo-opus@small | 2 | 1.21 | [1.0802, 1.2331] | 1.64 | [1.2455, 1.7097] | — | None | 0.00 | 0/10000 |
| hybrid-forced_vs_solo-opus@medium | 3 | 1.50 | [0.6581, 1.7067] | 1.98 | [0.8997, 2.2242] | — | None | 0.00 | 0/10000 |
| hybrid-forced_vs_solo-opus@large | 3 | 1.54 | [0.874, 2.5213] | 2.02 | [1.191, 3.264] | — | None | 0.00 | 0/10000 |
| hybrid-inst_vs_solo-opus | 8 | 1.68 | [1.3377, 2.1172] | 2.08 | [1.6703, 2.6609] | 1.17 | [0.9701, 1.3194] | 0.00 | 0/10000 |
| hybrid-inst_vs_solo-opus@small | 2 | 1.66 | [1.6361, 1.9059] | 2.23 | [1.9658, 2.6256] | — | None | 0.00 | 0/10000 |
| hybrid-inst_vs_solo-opus@medium | 3 | 1.32 | [1.1246, 1.3732] | 1.68 | [1.4323, 1.7073] | — | None | 0.00 | 0/10000 |
| hybrid-inst_vs_solo-opus@large | 3 | 1.80 | [1.1929, 2.8419] | 2.21 | [1.4568, 3.6679] | — | None | 0.00 | 0/10000 |
| solo-sonnet_vs_solo-opus | 8 | 0.58 | [0.4211, 0.8287] | 0.58 | [0.4211, 0.8287] | 0.58 | [0.4211, 0.8287] | -0.15 | 0/10000 |
| solo-sonnet_vs_solo-opus@small | 2 | 1.00 | [0.8373, 1.1861] | 1.00 | [0.8373, 1.1861] | — | None | -0.20 | 0/10000 |
| solo-sonnet_vs_solo-opus@medium | 3 | 0.41 | [0.254, 0.6196] | 0.41 | [0.254, 0.6196] | — | None | 0.00 | 0/10000 |
| solo-sonnet_vs_solo-opus@large | 3 | 0.73 | [0.4861, 1.6266] | 0.73 | [0.4861, 1.6266] | — | None | -0.29 | 360/10000 |
<!-- /bench:table -->
**Reading the numbers.**

- The hybrid arms' **Claude side alone** often matched or exceeded the solo run
  (zoekt-1105: `hybrid-forced` $12.09 vs `solo-opus` $4.80 per pass; k6-6169: $26.07 vs
  $12.86). Writing a specification for the executor and verifying its output means the
  conductor still reads the code, and the executor's tokens come on top. agy's 2–10
  minute turnaround per delegation, run one call at a time, is where the wall-clock goes.
- The hybrid won or tied on three tasks (caddy-7888 $1.33 vs $2.02, caddy-7877 $0.79 vs
  $0.73, cli-attach $32.49 vs $37.18 for `hybrid-forced`) and lost clearly on the rest;
  with n = 2–3 per cell those per-task differences are within noise.
- **Judges.** Both judges scored the empty patch 1.0 on every task (floor intact). They do
  **not** rank the author's patch highly (median rank 8 of 12 for both judges), so a judge
  score here measures conformance to the rubric more than agreement with the maintainers;
  inter-judge Spearman 0.43, 55 percent of candidates within one point, point-biserial
  against `pass` 0.37 (Claude judge) and 0.21 (Gemini judge). The Gemini judge compresses
  every arm into 4.3–4.75.
- **Sensitivity.** Without the four runs that slept (see deviations): `hybrid-forced`
  cost-of-pass $10.25 (n = 15), `hybrid-inst` $13.67 (14), `solo-opus` $7.88 (20),
  `solo-sonnet` $4.74 (20) — same ordering, same conclusion.

<!-- bench:table run=full kind=judge -->
| arm | judge | n | mean | consistency | edge_cases | scope | readability | robustness | maintainability |
|---|---|---|---|---|---|---|---|---|---|
| solo-opus | claude | 21 | 4.02 | 4.24 | 4.00 | 3.81 | 4.19 | 4.19 | 3.71 |
| solo-opus | gemini | 21 | 4.74 | 4.76 | 4.62 | 4.71 | 4.95 | 4.62 | 4.76 |
| solo-sonnet | claude | 20 | 3.65 | 3.90 | 3.35 | 3.85 | 3.90 | 3.50 | 3.40 |
| solo-sonnet | gemini | 20 | 4.29 | 4.70 | 3.80 | 4.10 | 4.75 | 4.15 | 4.25 |
| hybrid-forced | claude | 18 | 3.74 | 3.89 | 3.78 | 3.94 | 3.72 | 3.83 | 3.28 |
| hybrid-forced | gemini | 18 | 4.72 | 4.89 | 4.56 | 4.83 | 4.72 | 4.78 | 4.56 |
| author | gemini | 8 | 4.44 | 4.75 | 4.12 | 4.38 | 4.88 | 4.25 | 4.25 |
| author | claude | 8 | 3.79 | 4.12 | 3.88 | 3.50 | 3.75 | 3.88 | 3.62 |
| hybrid-inst | gemini | 16 | 4.75 | 4.81 | 4.56 | 4.56 | 4.88 | 4.94 | 4.75 |
| hybrid-inst | claude | 16 | 3.75 | 3.94 | 3.75 | 3.94 | 3.69 | 3.94 | 3.25 |
| null | gemini | 8 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| null | claude | 8 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |

anchors: {"author_rank_by_task": {"claude": {"caddy-7877": "3/10", "caddy-7888": "9/12", "caddy-7913": "8/10", "caddy-7995": "9/13", "cli-14136": "4/13", "cli-attach": "1/10", "k6-6169": "8/11", "zoekt-1105": "9/12"}, "gemini": {"caddy-7877": "2/10", "caddy-7888": "11/12", "caddy-7913": "8/10", "caddy-7995": "6/13", "cli-14136": "11/13", "cli-attach": "2/10", "k6-6169": "7/11", "zoekt-1105": "11/12"}}, "null_max_by_judge": {"claude": 1.0, "gemini": 1.0}, "null_mean_by_judge": {"claude": 1.0, "gemini": 1.0}}; agreement: {"n_candidates_both": 91, "spearman_mean": 0.4349, "within1_pct": 0.549}; judge-vs-pass: {"claude": 0.3688, "gemini": 0.2074}; failed judge calls: 0
<!-- /bench:table -->
**Billing reconciliation.** The project's billing export gives unit prices per SKU over
the run window: Claude Opus 5 $5 / $25 per Mtok with cache write 1.25× and cache read 0.1×
in both context tiers (no long-context premium billed); Claude Sonnet 5 $2 / $10 (Claude
Code's own `costUSD` used these rates; `prices.json`'s 3 / 15 is stale); **Gemini 3.8
Flash $1.50 / $7.50 / $0.15 (input / output / cached), twice the deck's promotional
$0.75 / $3.75 / $0.075**. Totals over the window (other activity on the shared project is
included on the billed side and cannot be separated): Claude Opus computed $458.57 vs
billed $636.07; Claude Sonnet $80.47 vs $137.06; Gemini 3.8 Flash $116.34 at the deck,
$232.68 at the billed rates, vs $277.06 billed; Claude Fable 5.1 $499.50 billed is the
operator's own Claude Code session that ran the study, not part of it. Both cost bases
are therefore shown in every table; the ordering of the arms is the same under either.

**Spend.** $856.82 across all attempts, of which $663.21 in the 75 counted runs, $144.01
in eight attempts interrupted by machine sleep and $49.60 in seven infrastructure
failures (all kept under `runs/*__attempts/`); judging $69.55; smoke and pilot before the
run about $120.

**What this means for the plugin.** The result is the one `docs/POC-PLAYBOOK.md` §0
predicts for repository editing: delegation does not remove the reading and verifying the
conductor must do to own the result, and here it added the executor's tokens and its
latency on top. For implementation work, use Claude Code directly; the plugin's saving
lives where the digest *is* the deliverable (research, log analysis, multi-source
lookups). Two operational findings from the run belong in the plugin itself: `prices.json`
should carry the Vertex-billed Gemini and Sonnet 5 rates, and agy started in a directory
it has never seen runs in its last project root until `agy --new-project` is issued there.

### Deviations log (kept as the run proceeds)

- 02:43Z — lane A relaunched detached 24 s into its first item (launcher change); the
  item was rerun; the partial attempt is kept as `interrupted`.
- 08:20Z — the laptop slept (battery, lid) during two running items; both were rerun
  as `suspended` and a separate retry budget was introduced for sleep interruptions
  (`bench/harness/schedule.py`). Spend on interrupted attempts is recorded but not
  part of any arm's numbers.
- 08:44Z — `k6-6169__solo-opus__r1` failed only on `websockets.TestLockingUpWithAJustGeneralCancel`,
  a test in a package the agent did not touch that passed 6/6 during curation; the
  checkout was already removed, so it **stays a failure** under the protocol. From the next
  item on, a test that fails in the full suite is rerun once in isolation and counted as a
  flake if it passes then (`full_suite.flaky_retry` in `run.json`); the final tables state
  how many runs' `pass` depended on that rule.
- 09:00Z — a second clamshell sleep (7 min) interrupted both running items; under the
  rule above they were rerun ($14.55 of attempts kept, not counted). Measured on those
  records: Claude Code retried the interrupted API call on wake and both runs had
  completed normally, so rerunning every slept run only burns money. From 10:00Z the
  wall cap counts active (monotonic) time, a run that slept but completed is kept and
  flagged (`suspended_s`), only a run that died of the sleep is rerun, and the final
  tables carry a sensitivity block without slept runs (`sensitivity_no_sleep`).
- 10:00Z–13:26Z — two harness bugs, both fixed and repaired in `queue.json` (`repairs`):
  (1) the per-run STOP check read the old lanes' `STOP` file instead of the new lanes'
  `STOP2`, so lanes C/D started four items whose run process exited at once; those
  attempts never ran and were removed from the items' histories. (2) In three hybrid
  runs that started within seconds of another session, Claude Code did not put the
  plugin's `bin/` on the agent's PATH; the agent found no `agy-delegate` and stopped with
  0 delegations. The harness now prepends the plugin's `bin/` itself, such a run is
  detected (`wrapper_not_found`, `env_failure: plugin_bin_missing`) and treated as an
  infrastructure failure. `caddy-7995__hybrid-forced__r1` was reclassified and rerun;
  the other two had already been rerun under the sleep rule.
- 22:28Z — k6's `websockets` test package hung for 27 minutes at 11 GB inside the full-suite
  gate of `k6-6169__hybrid-inst__r2` (hidden tests passed) and had failed
  `k6-6169__solo-opus__r1` earlier on one named test. That package imports nothing the k6
  task touches (checked with `go list -deps` at the base commit), so neither failure can be a
  regression from the agent's change. Rule added: a full-suite failure confined to packages
  with no dependency on the changed packages is rerun once whole; if it still fails it is an
  environment failure (`env_failure: suite_failure_in_unrelated_package`) and the item is
  rerun. Applied post hoc to those two runs (reclassified, rerun; the attempts are kept). The
  hanging test `TestLockingUpWithAJustGeneralCancel` is skipped in k6's suite gate from
  22:40Z (`task.json` `verify.skip_notes`). A failure in a package that *does* depend on the
  change remains a failure.
- 2026-09-16 00:39Z — `cli-14136__solo-opus__r3` ended 3.5 minutes after a wake with the
  message "API Error: getaddrinfo ENOTFOUND oauth2.googleapis.com" (Claude Code reports
  `subtype: success`, `is_error: true`), 0 files changed. A run whose final message is an
  API error is now classified `suspended` (after sleep) or `infra` (otherwise) and rerun;
  this one was reclassified post hoc and requeued, together with two other network deaths
  (`cli-attach__hybrid-inst__r1`, `zoekt-1105__hybrid-forced__r1`, both "ENOTFOUND" with 0
  files changed). Two Sonnet runs that had hit the turn cap were swept up by the first
  version of that rule and put back as the capped failures they are (`repairs` in
  `queue.json`); a cap is never treated as infrastructure. Note for the write-up: the
  turn cap binds for `solo-sonnet` (81 turns on a small task with a passing tree).
- 2026-09-16 13:39Z — a 2 h `cli-attach__hybrid-inst__r2` attempt ($24.41) died at a wake
  from a sleep entered on battery (lid closed), like every other run death so far. From
  14:20Z no new item starts while the machine is on battery power (running items are not
  touched); lanes C/D were replaced by E/F to pick the rule up. The waiting time is
  recorded per run (`waited_for_ac_s`).
- 2026-09-16 14:20Z — **stopping rule changed by the operator** at 72 of 96 runs done: the
  laptop must travel and sleeps when closed, and every run death so far came from those
  sleeps. The run stops once every task × arm cell has **n ≥ 2** instead of n = 3. At that
  point 10 cells had n = 3, 20 had n = 2 and 2 had n = 1 (both cli-attach hybrids, the
  2-hour runs most exposed to sleep); only those two cells' second repetitions were still
  run. The 21 skipped items are third repetitions (`status: skipped` in `queue.json`).
  The decision was taken on machine constraints, not on the interim numbers, and the
  pre-registered analysis is unchanged; the tables state n per cell.

## Versions and provenance

Claude Code 2.1.270, agy 1.2.2, Go 1.27.1, plugin 0.28.0 (`5392467`) for the hybrid
arms; prices frozen in `bench/prices.lock.json`; every run records these in `run.json`.

## What is not counted

Gemini context-cache storage (not reported by agy, so the Gemini side is a lower bound);
harness overhead (checkout, scoring); human time spent writing prompts; the cost of the
judging pass (reported separately).
