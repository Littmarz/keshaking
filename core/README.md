# core/ — общие схемы (контракты)

**Владелец:** 👥 все четверо · **Источник правды:** [../docs/02-contracts.md](../docs/02-contracts.md)

```
core/
└── schemas.py   Document · Chunk · Hit · SearchResult · UserContext · Citation · AgentStep · AgentResult · Ticket · QAEntry
```

⚠️ Меняем только через PR с ревью **двух** человек (автор модуля-потребителя + капитан) — от этих схем зависят все модули, но четыре ревью за 48 часов — слишком медленно.

Пишем по таблицам полей из `docs/02-contracts.md` (обновлены 26.11: `needs_clarification`, `escalated`, `Ticket`, `QAEntry`, `directorate`/`stage`, `source_type`). Перед изменением схем — сверить с реальными данными из DOCX.
