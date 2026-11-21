# agent/ — многошаговый агент (LangGraph)

**Владелец:** 🤖 Разработчик 4 — Агент и LLM · **Подробный план:** [../docs/team/4-agent.md](../docs/team/4-agent.md) · **Контракт:** [../docs/02-contracts.md §3](../docs/02-contracts.md)

analyze → plan → retrieve ⇄ rewrite → check_actuality → check_conflicts → generate → verify → answer | refuse

```
agent/
├── run.py       ⭐ run_agent() · run_agent_stream()
├── cli.py       отладка из консоли
├── config.py
├── llm/         client.py (OpenAI-совместимый: Ollama / vLLM) · schemas.py
├── prompts/     system · analyze · plan · rewrite · grade · conflicts · generate · verify · refuse
├── graph/       state · nodes · edges · build
├── tools/       search · versions · section
└── tests/
```

**Модель:** _впиши после бенчмарка (D-004 в docs/decisions.md)_
