# QueryPilot — BIRD Mini-Dev baselines

**Metric:** execution accuracy — run gold and predicted SQL against the question's
database, compare result multisets (order-sensitive only when the gold query has an
`ORDER BY`).

**Two scopes are reported below**, and it matters which is which:

- **The full Mini-Dev set (500 questions)** for the local single-shot configuration.
  This is the best estimate of that configuration's accuracy.
- **A deterministic 100-question subset** (`fixed_subset(items, 100, seed=42)`) for the
  three-way comparison, because cloud quota and local wall-clock could not fund the
  other two configurations at full scale. Every row of that table is the same 100
  questions.

## Full Mini-Dev, local single-shot

| provider | model | mode | accuracy | 95% CI | no-SQL | exec-fail | avg latency |
|---|---|---|---|---|---|---|---|
| ollama (local) | qwen3:4b | single-shot | **47.0%** (235/500) | [42.7, 51.4] | 34 | 13 | 48.2s |

Run 2026-08-21, 5.2 hours of wall clock for the 400 questions the subset did not cover.

By difficulty, with Wilson intervals:

| bucket | score | 95% CI |
|---|---|---|
| simple (148) | 94 (**64%**) | [56, 71] |
| moderate (250) | 111 (**44%**) | [38, 51] |
| challenging (102) | 30 (**29%**) | [21, 39] |

**Denominator note.** Mini-Dev ships 500 rows but only 498 distinct `question_id`s:
`bird_137` and `bird_138` each appear twice, same database, byte-identical question
text. Both copies were run. Deduplicating to first occurrence gives 233/498 = **46.8%**,
so the choice does not move the headline. Worth recording as a small, concrete data
point about the benchmark's editorial quality — and as a variance illustration, since
one of those duplicate pairs scored a miss and a match on identical input.

## Three-way comparison, shared 100-question subset

| provider | model | mode | accuracy | no-SQL | exec-fail | avg calls | avg latency |
|---|---|---|---|---|---|---|---|
| ollama (local) | qwen3:4b | single-shot | **44.0%** (44/100) | 9 | 5 | 1.00 | 54.5s |
| ollama (local) | qwen3:4b | agent | **43.0%** (43/100) | 8 | 1 | 6.66 | 212.3s |
| groq | openai/gpt-oss-120b | single-shot | **50.0%** (50/100) | 0 | 4 | 1.00 | 20.0s |

By difficulty (same 100 questions):

| | challenging (15) | moderate (52) | simple (33) |
|---|---|---|---|
| local single-shot | 8 (53%) | 19 (37%) | 17 (52%) |
| local agent | 5 (33%) | 19 (37%) | 19 (58%) |
| groq single-shot | 9 (60%) | 22 (42%) | 19 (58%) |

## What the numbers say

**A 4B local model lands within 6 points of a 120B cloud model** on the shared subset,
44.0% vs 50.0%, at $0/query. That is the headline: the gap between a quantized 4B on a
6GB laptop GPU and a frontier-adjacent hosted model is not a chasm. (The local model's
full-set figure of 47.0% is *not* comparable to Groq's number, which is subset-only.)

**The subset was imprecise, not misleading.** Its 44.0% carried a 95% interval of
[34.7, 53.8], and the full-set value of 47.0% falls inside it. Running the other 400
questions narrowed the interval from ±9.5 to ±4.4 points.

**Agent mode and single-shot are indistinguishable on accuracy.** 43.0% vs 44.0%.
Paired on the same 100 questions: 34 both correct, 47 both wrong, agent-only 9,
single-shot-only 10. That is **19 discordant pairs, McNemar exact two-sided
p = 1.000**, which is as close to no difference as a paired comparison can produce.
Agent mode pays 6.7x the model calls (6.66 vs 1.00) and 3.9x the wall clock (5.9h vs
1.5h) for that parity. Distinguishing a real difference of a few points would need
roughly 400 paired questions, about 32 hours of local agent inference.

The per-question data still shows a mechanism worth reporting, independent of
significance. Of the questions agent mode loses, most are wrong answers after a normal
five to eight call episode rather than budget exhaustion: the agent explored the schema,
wrote confident SQL, and got a different answer than a single shot at the full schema
would have. The plausible reading is that a 4B model choosing which tables to inspect
narrows its own context onto the wrong ones. Suggestive, not established.

**What the loop fix changed, and what it did not.** These agent numbers come from a
re-run after fixing a loop bug where a truncated reply (no content, no tool calls) was
answered with a nudge, which produced another truncated reply, until the 12-call cap.
Re-running the same 100 questions with the fix:

| | old loop | fixed loop |
|---|---|---|
| accuracy | 41.0% | 43.0% |
| produced no SQL at all | 16 | 8 |
| hit the 12-call cap | 16 | 2 |
| model calls | 742 | 666 |
| wall clock | 7.99h | **5.90h** |

The accuracy change is noise: gained 9, lost 7, McNemar p = 0.804. What the fix
delivered is robustness and cost, halving the dead episodes and removing a quarter of
the runtime. Two details worth keeping: of the 16 old cap-outs, 8 now produce SQL and
only **1** of those is correct, so the treadmill was not hiding good answers; and the
new early-stop almost never fires, because the retry at a larger token budget usually
rescues the episode instead. The published number before this re-run described a loop
that no longer exists, which is why the re-run came before any attempt to extend the
comparison.

**A retraction.** An earlier version of this report argued that `moderate` was the
weakest bucket for every configuration and that a model-independent dip in the middle
difficulty band pointed at label noise. **The full 500-question run does not support
that.** At full scale the local single-shot ordering is exactly what difficulty labels
predict: simple 64%, moderate 44%, challenging 29%. The apparent dip came from the
subset's `challenging` bucket holding only **15 questions**, where 8/15 = 53% is well
within sampling noise. The Groq row shows the same pattern on the same 15 questions and
is most likely the same artifact.

The annotation-quality argument is not abandoned, but it now rests on better evidence:
the duplicate rows documented above, and the manual error analysis planned for Phase 5,
rather than on a difficulty pattern that dissolved under more data.

## Cloud agent mode: attempted, not obtained

The agent-vs-single-shot comparison on a *large* model — the experiment that would show
whether the finding above is a small-model artifact — is **absent because free tiers
cannot fund it.** An agent episode costs ~13K tokens; 100 questions needs ~500-600 calls
and ~900K tokens.

| provider | free-tier limit | real evaluations before quota | needed |
|---|---|---|---|
| groq | 200K tokens/day | 1 | ~100 |
| gemini | request/day cap | 16 (single-shot) | ~100 |

Both providers cap roughly 20x below the requirement per day. Partial results are kept in
`evals/results/*.quota_failures.jsonl` for the record, and are excluded from every number
above — a quota 429 records as a failed question, and counting those as model errors
would understate accuracy.

Two smaller findings from the attempt, worth keeping:

- `openai/gpt-oss-120b` emits malformed tool calls under our schema — one episode died on
  `attempted to call tool 'json' which was not in request.tools`, the model wrapping
  `final_answer`'s arguments in a phantom `json` tool. The loop does not currently
  recover from this.
- Groq free tier enforces a **tokens-per-day** ceiling separate from its RPM and TPM
  limits, and it is the binding constraint for agentic workloads. The published
  request-per-day number (1000) is irrelevant when a single episode costs 13K tokens.

## Reproducing

```bash
# full Mini-Dev, local single-shot (~5-6 hours, resumable)
uv run python -m dbagent eval --dataset bird --mode single_shot --provider ollama --subset 0 \
  --out evals/results/bird_single_shot_ollama_full500.jsonl

# the shared 100-question subset
uv run python -m dbagent eval --dataset bird --mode single_shot --provider ollama --subset 100
uv run python -m dbagent eval --dataset bird --mode agent --provider ollama --subset 100 \n  --out evals/results/bird_agent_ollama_fixedloop.jsonl
uv run python -m dbagent eval --dataset bird --mode single_shot --provider groq --subset 100
```

Runs append to JSONL and resume by question id, so an interrupted run continues where it
stopped. Serve the local model at `OLLAMA_CONTEXT_LENGTH=8192`: at 16384 the KV cache
spills the model off the GPU and generation drops from 59 to 32 tok/s. Set
`OLLAMA_KEEP_ALIVE=5m` for long runs, or the engine reloads the model on every call.

A note on qwen3's thinking: earlier versions of this report told you to set
`OLLAMA_NO_THINK=false`. That setting is now off by default, and measurement on
2026-08-20 (Ollama 0.32.13) found that **none** of `/no_think` in the system message,
`/no_think` in the user message, `think: false`, or `chat_template_kwargs` actually
suppresses the model's reasoning. Every number in this report was produced with thinking
active, whatever the flag said.
