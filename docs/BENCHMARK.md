# Benchmark: Claude Code alone vs Claude Code + this plugin on real pull requests

_Status: pilot in progress. Every table on this page is regenerated from
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

## Pilot

_Results will appear here as `bench:table` blocks once the pilot runs are scored._

## Full run

_Not started. Size to be decided from the pilot._

## Versions and provenance

Claude Code 2.1.270, agy 1.2.2, Go 1.27.1, plugin 0.28.0 (`5392467`) for the hybrid
arms; prices frozen in `bench/prices.lock.json`; every run records these in `run.json`.

## What is not counted

Gemini context-cache storage (not reported by agy, so the Gemini side is a lower bound);
harness overhead (checkout, scoring); human time spent writing prompts; the cost of the
judging pass (reported separately).
