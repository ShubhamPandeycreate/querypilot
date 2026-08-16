# QueryPilot

A data-analyst agent that answers natural-language questions over SQL databases. It explores
the schema, writes SQL, executes it behind a safety guard, **corrects its own errors**, and
returns answers with charts — portable across Gemini, Groq, OpenRouter, and local Ollama
through one OpenAI-compatible client, with a hand-rolled agent loop (no agent frameworks).

> 🚧 Under active development. Roadmap below.

## Roadmap

- [x] **Phase 0** — Project setup, provider smoke test across all four backends
- [x] **Phase 1** — SQL toolbelt + safety layer (sqlglot guard, read-only execution, 6 agent tools)
- [ ] **Phase 2** — The agent loop: tool calling, self-correction, JSONL tracing
- [ ] **Phase 3** — Eval harness: execution accuracy on BIRD Mini-Dev + Spider dev
- [ ] **Phase 4** — Streamlit app with live trace viewer, public deployment
- [ ] **Phase 5** — Error analysis, architecture docs, v1.0
- [ ] **Stretch** — QLoRA fine-tune of a small open model; GRPO RL ablation with execution rewards

## Quickstart

```bash
# install uv: https://docs.astral.sh/uv/
uv sync
cp .env.example .env   # add your free API keys (links inside)
uv run python scripts/smoke_test.py
```

## License

MIT
