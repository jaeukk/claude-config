# Model refresh: September 27, 2026

This is a comparison of published evidence and an existing local experiment, not a
new live benchmark. API prices do not measure team subscription quota consumption.

## GPT-6 Sol versus GPT-5.6 Sol

| Metric | GPT-5.6 Sol | GPT-6 Sol |
|---|---:|---:|
| Standard input / output price per million tokens | $4 / $20 | $2 / $10 |
| Context / maximum output tokens | 1.05M / 128K | 1.05M / 128K |

The documented base token prices fall 50%. No directly comparable Sol 6 versus
Sol 5.6 quality or latency score was verified in the official sources consulted.
Do not substitute Astra's scores for Sol's or promise a 50% reduction per task.

Sources: [Sol 6](https://developers.openai.com/api/docs/models/gpt-6-sol),
[Sol 5.6](https://developers.openai.com/api/docs/models/gpt-5.6-sol),
[migration guidance](https://developers.openai.com/api/docs/guides/latest-model).
Preserve high effort for implementers. If integrating directly with an API, use
Responses for reasoning with tools; Sol 6 Chat Completions function calling requires
effort `none`. The current dispatcher uses the Codex CLI.

## Claude Opus 5.5 versus Opus 5

Anthropic's published comparison reports:

| Benchmark | Opus 5 | Opus 5.5 | Change |
|---|---:|---:|---:|
| Terminal-Bench 4.0 | 52.3% | 66.4% | +14.1 percentage points |
| FrontierCode v1.1 Main | 48.0% | 54.4% | +6.4 percentage points |
| AutomationBench | 26.9% | 40.0% | +13.1 percentage points |

These are vendor-reported results, not equal-effort local trials. Opus 5.5 uses max
effort except Terminal-Bench at xhigh; the announcement discloses safeguard-related
fallback models for some evaluations. Anthropic reports about 40% lower cost per task.
Source: [release comparison](https://www.anthropic.com/claude-opus-5-5).

The existing September 23 local experiment used 10 coding items, three repeats per
arm, and the dispatcher's CLI argument shape:

| Arm | Correct | Median latency | Reported API-equivalent cost per call |
|---|---:|---:|---:|
| Opus 5 high | 29/30 | 29.7 s | $0.0894 |
| Opus 5.5 high | 30/30 | 22.3 s | $0.0656 |
| Opus 5.5 medium | 30/30 | 17.7 s | $0.0528 |

Local source: `~/.claude/multiagent/tasks/2026-09-23-opus-55-benchmark/results/main/summary.csv`.
At high effort, Opus 5.5 reduced measured cost about 27% and median latency about 25%
versus Opus 5. The small,
near-ceiling suite cannot establish accuracy superiority. Its independent critic
review was recorded as incomplete; this refresh does not claim that review happened.

The verified model ID is `claude-opus-5-5`; published pricing is $4 input / $20 output
per million tokens. Adaptive thinking is always enabled. Direct API migrations must
also account for forced-tool-use and thinking-block compatibility changes; the
current dispatcher uses Claude Code CLI.
Source: [model documentation](https://platform.claude.com/docs/en/models/opus-5-5/overview).

## Routing decision

Upgrade `codex-core` to Sol 6 and `claude-core`, `claude-core-team`, and
`claude-frontier` to Opus 5.5. Retain high implementer effort, existing account
preferences, and the other role assignments. This is a model-pin refresh, not a
new cross-model ranking. New conductor sessions must match their asserted model;
editing policy cannot switch a running session. Account availability for Sol 6 has
not been live-tested in this refresh.
