"""Экономика запроса: токены → стоимость (для блока экономического расчёта на платформе).

Организаторы (чат задачи, 26.11): потребление токенов указываем по ценам ОФИЦИАЛЬНОГО провайдера
модели, чтобы сравнить с локальным inference; в первую очередь смотрят на стоимость в пересчёте
на токены за запрос. Нагрузка из брифа — до 200 запросов в день.

Два режима:
1) из логов — реальные usage-токены, которые агент кладёт в AgentResult.metrics
   (prompt_tokens, completion_tokens, llm_calls) → `python -m analytics.cost --logs eval/reports/agent_runs.jsonl`;
2) оценка до появления агента — по размеру контекста поиска и числу вызовов LLM
   → `python -m analytics.cost --estimate`.

Цены задаются явно (за 1 млн токенов) — подставить с официальной страницы провайдера выбранной модели:
    python -m analytics.cost --estimate --price-in 0.0 --price-out 0.0 --currency RUB
Только стандартная библиотека.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

# Грубая оценка для русского текста у токенизаторов Qwen: ~3 символа на токен.
# Для отчёта использовать реальные usage из API, оценка — только для планирования.
CHARS_PER_TOKEN = 3.0


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN))


@dataclass
class Usage:
    prompt_tokens: int
    completion_tokens: int
    llm_calls: int = 1

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def cost(u: Usage, price_in: float, price_out: float) -> float:
    """Стоимость одного запроса; цены — за 1 000 000 токенов."""
    return (u.prompt_tokens * price_in + u.completion_tokens * price_out) / 1_000_000


def estimate_request(k: int = 5, llm_calls: int = 5, system_tokens: int = 600, answer_tokens: int = 350,
                     service_out_tokens: int = 80) -> Usage:
    """Оценка одного запроса к агенту по реальному корпусу.

    Граф (docs/01-architecture.md): analyze → retrieve(+rewrite) → grade → generate → verify.
    Контекст из k пунктов уходит в grade, generate и verify; служебные шаги отвечают коротким JSON.
    """
    from retrieval import mock

    lens = [estimate_tokens(c.text) for c in mock._index().chunks if c.source_type == "regulation"]
    ctx = round(statistics.mean(sorted(lens)[-max(k * 10, 1):]) * k)  # берём «длинные» пункты — с запасом
    calls_with_ctx = min(3, llm_calls)
    prompt = llm_calls * system_tokens + calls_with_ctx * ctx
    completion = (llm_calls - 1) * service_out_tokens + answer_tokens
    return Usage(prompt, completion, llm_calls)


def from_logs(path: Path) -> list[Usage]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        m = json.loads(line).get("metrics") or {}
        if "prompt_tokens" in m:
            out.append(Usage(int(m["prompt_tokens"]), int(m.get("completion_tokens", 0)), int(m.get("llm_calls", 1))))
    return out


def report(usages: list[Usage], price_in: float, price_out: float, per_day: int, currency: str) -> str:
    if not usages:
        return "Нет данных о токенах."
    p = statistics.mean(u.prompt_tokens for u in usages)
    c = statistics.mean(u.completion_tokens for u in usages)
    avg = Usage(round(p), round(c), round(statistics.mean(u.llm_calls for u in usages)))
    one = cost(avg, price_in, price_out)
    lines = [
        "| Показатель | Значение |", "|---|---|",
        f"| Запросов в выборке | {len(usages)} |",
        f"| Вызовов LLM на запрос | {avg.llm_calls} |",
        f"| Токенов на запрос: вход / выход / всего | {avg.prompt_tokens} / {avg.completion_tokens} / {avg.total} |",
        f"| Цена провайдера за 1 млн: вход / выход | {price_in} / {price_out} {currency} |",
        f"| **Стоимость одного запроса** | **{one:.4f} {currency}** |",
        f"| В день ({per_day} запросов) | {one * per_day:.2f} {currency} · {avg.total * per_day:,} токенов |".replace(",", " "),
        f"| В месяц (22 рабочих дня) | {one * per_day * 22:.2f} {currency} · {avg.total * per_day * 22:,} токенов |".replace(",", " "),
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Стоимость запроса к агенту в токенах и деньгах")
    ap.add_argument("--logs", type=Path, help="jsonl с AgentResult (поле metrics.prompt_tokens/completion_tokens)")
    ap.add_argument("--estimate", action="store_true", help="оценка по корпусу, пока нет агента")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--llm-calls", type=int, default=5)
    ap.add_argument("--price-in", type=float, default=0.0, help="цена за 1 млн входных токенов у провайдера")
    ap.add_argument("--price-out", type=float, default=0.0, help="цена за 1 млн выходных токенов у провайдера")
    ap.add_argument("--per-day", type=int, default=200)
    ap.add_argument("--currency", default="RUB")
    a = ap.parse_args()
    if a.logs:
        usages = from_logs(a.logs)
    elif a.estimate:
        usages = [estimate_request(k=a.k, llm_calls=a.llm_calls)]
    else:
        ap.error("нужно --logs или --estimate")
    sys.stdout.reconfigure(encoding="utf-8")
    print(report(usages, a.price_in, a.price_out, a.per_day, a.currency))
    if a.price_in == a.price_out == 0:
        print("\n⚠️ Цены не заданы: подставь --price-in/--price-out с официальной страницы провайдера модели.")


if __name__ == "__main__":
    main()
