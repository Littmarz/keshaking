# eval/ — оценка качества

**Владелец:** 🔎 Разработчик 3 (👥 П2 — LLM-судья с 🤖) · **Подробный план:** [../docs/team/3-search-quality.md](../docs/team/3-search-quality.md) · **Формат набора:** [../docs/02-contracts.md §6](../docs/02-contracts.md)

```
eval/
├── datasets/   organizers · synthetic · traps · dev · test (.jsonl)
├── run.py      прогон агента по набору
├── metrics/    retrieval · citations · status · judge (LLM-судья) · latency
├── prompts/    judge.md
├── report.py   отчёт по категориям + худшие ошибки
└── reports/    ежедневные отчёты + progress.csv
```

```bash
make eval
```
