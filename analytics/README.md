# analytics/ — аналитика по логам

> ⏬ P2 (D-006): требование про дашборд неудачных поисков относилось к другому кейсу. Сначала три сценария; из логов сейчас нужны только задержки и объёмы для калькулятора стоимости.

**Владелец:** 🔎 Разработчик 3 (👥 П3 — с 🧭, который делает API и дашборд) · **Подробный план:** [../docs/team/3-search-quality.md](../docs/team/3-search-quality.md) · **Схема логов:** [../docs/02-contracts.md §5](../docs/02-contracts.md)

## ✅ Экономика запроса — `analytics/cost.py`

```bash
python -m analytics.cost --estimate --price-in <₽ за 1 млн вх.> --price-out <₽ за 1 млн вых.>   # пока нет агента
python -m analytics.cost --logs <jsonl с AgentResult>  --price-in … --price-out …               # по реальным usage
```

Оценка по корпусу (k = 5, 5 вызовов LLM): **≈ 7.6 тыс. входных + 0.7 тыс. выходных токенов на запрос**, ≈ 1.65 млн токенов в день при 200 запросах. Цены — с официальной страницы провайдера выбранной модели (так требуют организаторы). Агент должен класть `prompt_tokens`, `completion_tokens`, `llm_calls` в `AgentResult.metrics`.

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
