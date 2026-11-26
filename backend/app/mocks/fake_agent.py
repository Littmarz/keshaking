from datetime import UTC, datetime

from app.mocks.knowledge import find_confirmed_answer


# Synthetic fixtures let the frontend exercise citations before DOCX ingestion.
DIRECTORATES = {
    "Дирекция ИБ": (
        "fixture-ib",
        "2.2",
        "Координатор запрашивает недостающие сведения в карточке заявки.",
    ),
    "ДБРЦТ": (
        "fixture-dbrct",
        "1.1.1",
        "Заявка ожидает заполненную анкету до взятия в работу.",
    ),
}
DEMO_QUESTION = "Что будет, если по заявке не хватает информации?"


def run_mock(question, history, confirmed_entries=()):
    result = {
        "status": "escalated",
        "answer": "В тестовом наборе подходящий пункт не найден.",
        "citations": [],
        "reason": "Нет подходящего пункта в тестовом наборе.",
        "clarification": None,
        "ticket": None,
        "steps": [],
        "metrics": {"n_hits": 0, "top_score": None, "n_retrieval_rounds": 0},
        "model": "mock",
        "latency_ms": 0,
    }
    previous = history[-1] if history else None
    clarified = previous and previous["status"] == "needs_clarification"
    original = history[-2]["content"] if clarified else question
    knowledge = find_confirmed_answer(question, confirmed_entries)

    if knowledge:
        reference = knowledge["ticket_id"]
        title = f"Разъяснение службы качества · {reference}"
        result.update(
            status="answered",
            answer=f"{knowledge['answer']} [1]",
            reason=None,
            citations=[{
                "chunk_id": f"qa::{knowledge['qa_entry_id']}",
                "source_type": "qa_entry",
                "doc_id": knowledge["doc_id"] or f"qa-{reference}",
                "doc_title": title,
                "section": knowledge["section"] or reference,
                "directorate": knowledge["directorate"],
                "stage": knowledge["stage"],
                "quote": knowledge["answer"],
                "qa_entry_id": knowledge["qa_entry_id"],
            }],
            metrics={"n_hits": 1, "top_score": None, "n_retrieval_rounds": 1},
        )
    elif clarified:
        normalized = question.casefold()
        for directorate, (doc_id, section, quote) in DIRECTORATES.items():
            if directorate.casefold() in normalized:
                result.update(
                    status="answered",
                    answer=f"{quote} [1]",
                    reason=None,
                    citations=[{
                        "chunk_id": f"{doc_id}::{section}",
                        "source_type": "regulation",
                        "doc_id": doc_id,
                        "doc_title": f"Тестовый регламент — {directorate}",
                        "section": section,
                        "directorate": directorate,
                        "stage": None,
                        "quote": quote,
                        "qa_entry_id": None,
                    }],
                    metrics={"n_hits": 1, "top_score": None, "n_retrieval_rounds": 1},
                )
                break
        else:
            result.update(
                status="refused",
                answer="Не удалось определить применимую дирекцию.",
                reason="Уточнение не определило дирекцию.",
            )
    elif question.casefold().rstrip("?") == DEMO_QUESTION.casefold().rstrip("?"):
        clarification = "К какой дирекции относится заявка?"
        result.update(
            status="needs_clarification",
            answer=clarification,
            reason=None,
            clarification={"question": clarification, "options": list(DIRECTORATES)},
        )
    elif question.casefold().startswith("кто проверяет тестовую заявку"):
        quote = "Тестовую заявку проверяет координатор."
        result.update(
            status="answered",
            answer=f"{quote} [1]",
            reason=None,
            citations=[{
                "chunk_id": "fixture-intake::1.1",
                "source_type": "regulation",
                "doc_id": "fixture-intake",
                "doc_title": "Тестовый регламент обработки заявок",
                "section": "1.1",
                "directorate": None,
                "stage": None,
                "quote": quote,
                "qa_entry_id": None,
            }],
            metrics={"n_hits": 1, "top_score": None, "n_retrieval_rounds": 1},
        )

    result["steps"] = [{
        "name": "analyze",
        "title": "Проверяю тестовый сценарий",
        "detail": {"mock": True, "question": original},
        "started_at": datetime.now(UTC).isoformat(),
        "duration_ms": 0,
    }]
    return result
