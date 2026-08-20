# QueryPilot — BIRD Mini-Dev baselines

**Metric:** execution accuracy — run gold and predicted SQL against the question's
database, compare result multisets (order-sensitive only when the gold query has an
`ORDER BY`). **Dataset:** BIRD Mini-Dev, a deterministic 100-question subset
(`fixed_subset(items, 100, seed=42)`), identical across every row below.

## Results

| provider | model | mode | accuracy | no-SQL | exec-fail | avg calls | avg latency |
|---|---|---|---|---|---|---|---|
| ollama (local) | qwen3:4b | single-shot | **44.0%** (44/100) | 9 | 5 | 1.00 | 54.5s |
| ollama (local) | qwen3:4b | agent | **41.0%** (41/100) | 16 | 2 | 7.42 | 287.7s |
| groq | openai/gpt-oss-120b | single-shot | **50.0%** (50/100) | 0 | 4 | 1.00 | 20.0s |

By difficulty (same 100 questions):

| | challenging (15) | moderate (52) | simple (33) |
|---|---|---|---|
| local single-shot | 8 (53%) | 19 (37%) | 17 (52%) |
| local agent | 5 (33%) | 16 (31%) | 20 (61%) |
| groq single-shot | 9 (60%) | 22 (42%) | 19 (58%) |

## What the numbers say

**A 4B local model reaches 88% of a 120B cloud model's accuracy** (44.0% vs 50.0%)
at $0/query. That is the headline: the gap between a quantized 4B running on a 6GB
laptop GPU and a frontier-adjacent hosted model is 6 points, not a chasm.

**Agentic self-correction did not pay for itself on the small model** — 41.0% vs
44.0% single-shot, at 7.4x the LLM calls and 5.3x the wall-clock time. This is the
opposite of the expected result and is worth stating plainly rather than burying.

The paired breakdown explains why. Against single-shot on the same questions, agent
mode **won 8 and lost 11**:

- **3 losses** were budget exhaustion — the episode hit the 12-call cap with no SQL
  ever produced. All 16 of agent mode's no-SQL failures are cap-outs, and every one
  sits at exactly 12 calls.
- **8 losses** were *wrong results after a normal-length episode* (5-8 calls). The
  agent explored the schema, wrote confident SQL, and got a different answer than it
  would have from a single shot at the full schema.

That second group is the real finding: the extra turns are not merely wasteful, they
are actively harmful on this model. Feeding the whole schema at once (single-shot)
beats letting a 4B model choose which tables to look at — its `get_schema` /
`sample_rows` choices narrow the context to the wrong tables, and it then writes
correct-looking SQL over an incomplete picture. Difficulty confirms the shape: agent
mode is the *only* configuration that beats single-shot on `simple` questions
(20/33 vs 17/33), where one self-correction pass has room to land, and it loses
ground on `moderate` (16 vs 19) and `challenging` (5 vs 8), where exploration
compounds early mistakes.

**`moderate` is the weakest bucket for every configuration**, including the 120B
cloud model (42%, below its 60% on `challenging`). A model-independent dip in the
middle difficulty band points at label noise rather than model capability — see the
2026 BIRD annotation-error literature, which reports substantial error rates in
published gold SQL.

## Cloud agent mode: attempted, not obtained

The agent-vs-single-shot comparison on a *large* model — the experiment that would
show whether the finding above is a small-model artifact — is **absent because free
tiers cannot fund it.** An agent episode costs ~13K tokens; 100 questions needs
~500-600 calls and ~900K tokens.

| provider | free-tier limit | real evaluations before quota | needed |
|---|---|---|---|
| groq | 200K tokens/day | 1 | ~100 |
| gemini | request/day cap | 16 (single-shot) | ~100 |

Both providers cap roughly 20x below the requirement per day. Partial results are
kept in `evals/results/*.quota_failures.jsonl` for the record, and are excluded from
every number above — a quota 429 records as a failed question, and counting those as
model errors would understate accuracy.

Two smaller findings from the attempt, worth keeping:

- `openai/gpt-oss-120b` emits malformed tool calls under our schema — one episode
  died on `attempted to call tool 'json' which was not in request.tools`, the model
  wrapping `final_answer`'s arguments in a phantom `json` tool. The loop does not
  currently recover from this.
- Groq free tier enforces a **tokens-per-day** ceiling separate from its RPM and TPM
  limits, and it is the binding constraint for agentic workloads. The published
  request-per-day number (1000) is irrelevant when a single episode costs 13K tokens.

## Reproducing

```bash
# local (needs Ollama; thinking must stay ENABLED - see runner.py)
OLLAMA_NO_THINK=false uv run python -m dbagent eval --dataset bird --mode single_shot --provider ollama --subset 100
uv run python -m dbagent eval --dataset bird --mode agent --provider ollama --subset 100

# cloud single-shot
uv run python -m dbagent eval --dataset bird --mode single_shot --provider groq --subset 100
```

Runs append to JSONL and resume by question id, so an interrupted run continues
where it stopped. Serve the local model at `OLLAMA_CONTEXT_LENGTH=8192`: at 16384
the KV cache spills the model off the GPU and generation drops from 59 to 32 tok/s.
