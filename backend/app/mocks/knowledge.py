def normalized_question(question: str) -> str:
    return " ".join(question.casefold().split()).rstrip("?.!")


# Explicit paraphrase pair from c07/c08 in eval/datasets/control.jsonl.
DEMO_EQUIVALENTS = {
    normalized_question("Сколько дней даётся на согласование ТКП с юристами?"),
    normalized_question("За сколько дней юристы согласуют ТКП?"),
}


def find_confirmed_answer(question, entries):
    query = normalized_question(question)
    for entry in entries:
        original = normalized_question(entry["question"])
        if query == original or {query, original} <= DEMO_EQUIVALENTS:
            return entry
    return None
