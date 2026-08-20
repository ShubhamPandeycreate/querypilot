# QueryPilot

A data-analyst agent that answers natural-language questions over SQL databases. It explores
the schema, writes SQL, executes it behind a safety guard, **corrects its own errors**, and
returns answers with charts — portable across Gemini, Groq, OpenRouter, and local Ollama
through one OpenAI-compatible client, with a hand-rolled agent loop (no agent frameworks).

> 🚧 Under active development. Roadmap below.

## Try it

**Live demo:** not deployed yet — [deploy/DEPLOY.md](deploy/DEPLOY.md) is the checklist for
Streamlit Community Cloud and the Hugging Face Spaces mirror.

Locally, with no API key at all if you have [Ollama](https://ollama.com) running:

```bash
uv sync
uv run streamlit run app/streamlit_app.py
```

The app is the agent with a face on it:

- **Two demo databases** — Chinook, and a synthetic retail database generated for this project
  (`scripts/build_demo_dbs.py`), so at least one demo is guaranteed to be outside every
  model's training data. Or upload your own read-only `.sqlite`.
- **Agent vs Single-shot**, switchable per question — the same two modes the benchmark table
  below measures, so you can watch the difference instead of taking the numbers on faith.
- **SQL / Data / Chart / Trace** tabs on every answer. The Trace tab is the agent's own JSONL
  trace: every model call, every tool call, every failed query and the retry that fixed it.
- **Bring your own key** (kept in your browser session, never logged), or an operator key
  with per-session and per-hour caps — see [`dbagent/budget.py`](src/dbagent/budget.py).

## Benchmark results

BIRD Mini-Dev, 100-question fixed subset, execution accuracy:

| model | mode | accuracy | avg latency |
|---|---|---|---|
| qwen3:4b (local, 6GB GPU) | single-shot | **44.0%** | 54.5s |
| qwen3:4b (local, 6GB GPU) | agent | 41.0% | 287.7s |
| gpt-oss-120b (Groq) | single-shot | **50.0%** | 20.0s |

A quantized 4B model on a laptop GPU lands within 6 points of a 120B hosted model at
$0/query. Agentic self-correction *underperformed* single-shot on the small model —
[the report](evals/reports/baseline.md) breaks down why, and why that is mostly not
about the call budget.

## Roadmap

- [x] **Phase 0** — Project setup, provider smoke test across all four backends
- [x] **Phase 1** — SQL toolbelt + safety layer (sqlglot guard, read-only execution, 6 agent tools)
- [x] **Phase 2** — The agent loop: tool calling, self-correction, JSONL tracing, golden-trace CI replay (17/20 on the local smoke set with a 4B model)
- [x] **Phase 3** — Eval harness + BIRD Mini-Dev baselines ([full report](evals/reports/baseline.md))
- [x] **Phase 4** — Streamlit app with live trace viewer, BYOK + capped demo key, deploy configs
- [ ] **Phase 5** — Error analysis, architecture docs, v1.0
- [ ] **Stretch** — QLoRA fine-tune of a small open model; GRPO RL ablation with execution rewards

## Quickstart (development)

```bash
# install uv: https://docs.astral.sh/uv/
uv sync
cp .env.example .env   # add your free API keys (links inside) — optional for local Ollama

uv run python scripts/smoke_test.py                  # check the providers you configured
uv run python -m dbagent chat --provider ollama      # the agent in a terminal
uv run streamlit run app/streamlit_app.py            # the agent in a browser
uv run python -m dbagent eval --dataset chinook      # execution-accuracy harness
uv run pytest                                        # 150 tests, no network required
```

Regenerate the synthetic demo database (it is committed, so this is only needed if you change
the generator): `uv run python scripts/build_demo_dbs.py`.

## License

MIT
