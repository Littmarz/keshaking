# analytics/ — аналитика по логам

**Владелец:** 🔎 Разработчик 3 (👥 П3 — с 🧭, который делает API и дашборд) · **Подробный план:** [../docs/team/3-search-quality.md](../docs/team/3-search-quality.md) · **Схема логов:** [../docs/02-contracts.md §5](../docs/02-contracts.md)

Источник: SQLite backend (`query_logs`, `agent_steps`). Используется в `/analytics/*` (🧭).

```
analytics/
├── load.py       логи → pandas
├── summary.py    KPI + «сэкономлено часов»
├── failed.py     неудачные поиски + причина
├── gaps.py       чего нет в базе (кластеры отказов)
├── topics.py     топ тем
├── timeline.py   по дням / часам
├── fake_logs.py  правдоподобные логи для разработки дашборда
└── notebooks/
```
