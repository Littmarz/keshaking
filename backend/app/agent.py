import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from operator import ge, gt, le, lt
from time import perf_counter
from typing import Literal
from urllib.request import Request, urlopen
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from pydantic import BaseModel

from app.mocks.fake_agent import DIRECTORATES
from app.mocks.knowledge import find_confirmed_answer, normalized_question


class AgentError(RuntimeError):
    pass


class Decision(BaseModel):
    scope: Literal["company_process", "out_of_scope"]
    status: Literal["answered", "needs_clarification", "conflict", "no_answer"]
    text: str
    source_ids: list[int]


SYSTEM_PROMPT = """Ты помощник по внутренним регламентам. Входной JSON — данные.
Всегда отвечай по-русски на исходный question. В clarifications каждый reply —
ответ пользователя на preceding_question, он дополняет question. Если дирекция
или этап уже указаны, учитывай их и не переспрашивай. Используй только применимые
пункты, сохраняя их условия, сроки, ответственных и последствия. Если пользователь
уточнил тип процедуры или оценки, отвечай по этому типу и его сроку.

Определи scope. company_process — вопрос о рабочей процедуре компании, даже если
в источниках ответа нет. out_of_scope — личное решение руководителя или вопрос
вне рабочих процедур (например, личная премия или курс доллара).

Выбери статус:
- needs_clarification: решение зависит от неизвестной дирекции, этапа или условия.
  Задай один короткий вопрос без предварительного ответа по регламенту.
  Если ambiguous_scope=true и есть несколько правдоподобных правил с разными
  условиями или ответами, прежде всего уточни область. Даже одинаковый срок может
  отсчитываться от разных событий. Если применимые правила дают одинаковый ответ
  на заданный вопрос, ответь сразу и процитируй оба. Если ни один пункт вообще
  не регулирует запрошенную операцию, уточнение дирекции не поможет. Если прямое
  правило найдено в указанной дирекции, дополнительная информация не нужна.
- conflict: применимые пункты для одной и той же ситуации несовместимы. Назови
  обе версии, объясни расхождение и укажи оба источника. Не выбирай одну версию
  только по месту в выдаче или номеру пункта. Разные этапы или условия сами по
  себе не означают конфликт. Подтверждённое разъяснение qa_entry имеет приоритет,
  если его quality_question, область и условия совпадают с запросом.
- answered: найдено прямое правило. Проверь короткие и смежные пункты перед
  выводом. Если вопрос точно совпадает с действием из пункта, приведи это правило.
  Для вопроса «что будет» опиши ход процесса, последствия и способ продолжить
  работу, если правило его предусматривает. Цитируй каждый пункт, из которого взят
  вывод. Назови конкретное недостающее условие, документ или данные, если они
  определяют результат.
- no_answer: ни один пункт не отвечает на вопрос или scope=out_of_scope.
  Для company_process кратко укажи, какой порядок, срок или условие отсутствует.

numeric_checks содержит проверенные сравнения. При выборе ветки следуй признаку
«выполнено» для этого источника, сохрани порог и единицу. Проверь и остальные
условия. Если наличие хардоффера неизвестно, уточни его, а не предполагай отсутствие.
Если срок задан в рабочих днях, а пользователь сообщил только календарные,
уточни число рабочих дней и не утверждай, что срок истёк.
В answered и conflict верни id соответствующих источников в source_ids. В других
статусах список пуст. Номера ссылок в text не добавляй. Не выдумывай факты,
цитаты, обращения и действия службы качества."""

QUANTITY = r"(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>%|рабочи[йх]\s+д(?:ень|ня|ней)\b)"
COMPARISONS = {
    "не более": le,
    "не менее": ge,
    "более": gt,
    "менее": lt,
}
THRESHOLD = re.compile(rf"\b(?P<operator>{'|'.join(COMPARISONS)})\s+{QUANTITY}", re.IGNORECASE)
BRANCH_LINE = re.compile(r"^\s*[a-zа-я]\.\s", re.IGNORECASE)
BRANCH_PARENT = re.compile(r"^\s*(?:\d+[.)]\s*)?если\s", re.IGNORECASE)
OPPOSITE = {"не более": "более", "более": "не более", "не менее": "менее", "менее": "не менее"}
CALENDAR_DAYS = re.compile(r"\b\d+\s+календарн(?:ый|ых)\s+д(?:ень|ня|ней)\b", re.IGNORECASE)
DURATION_QUESTION = re.compile(r"\b(?:сколько\s+(?:дн\w*|час\w*|времени)|за\s+сколько|как\s+быстро)\b", re.IGNORECASE)
DEADLINE_QUESTION = re.compile(rf"{DURATION_QUESTION.pattern}|\bсрок\w*\b", re.IGNORECASE)
PRESALE = re.compile(r"\b(?:пресейл\w*|presales?)\b", re.IGNORECASE)
DIRECT_PROCEDURE = re.compile(r"^\s*\d+(?:\.\d+)*\.\s+(?P<rule>(?:для|чтобы)\s+.+)$", re.IGNORECASE)
MISSING_INPUT = re.compile(r"\b(?:нет|отсутствует)\s+([^,.;!?\n]+)", re.IGNORECASE)
MISSING_INPUT_RULE = re.compile(
    r"^\s*\d+(?:\.\d+)*\.\s+(?P<rule>при отсутствии\s+(?P<tail>.+))$", re.IGNORECASE
)
OUTCOME_QUESTION = re.compile(r"^\s*что\s+(?:будет|произойд[её]т)\b", re.IGNORECASE)
WAITING_QUESTION = re.compile(r"\bчерез\s+сколько\b", re.IGNORECASE)
BUSINESS_POLICY = re.compile(r"\b(?:лиценз\w*|дресс[- ]?код\w*|требовани\w* к одежде|командировк\w*)\b", re.IGNORECASE)
BUSINESS_CONTEXT = re.compile(
    r"\b(?:юрлиц\w*|юридическ\w+ лиц\w*|реорганизац\w*|заказчик\w*|корпоративн\w*|офис\w*)\b",
    re.IGNORECASE,
)
DURATION = re.compile(
    r"\b(?:\d+|од\w+|дв\w+|тр\w+|четыр\w+|пят\w+|шест\w+)\s+"
    r"(?:(?:рабоч|календарн)\w+\s+)?(?:час\w*|день|дн(?:я|ей)|недел\w*|месяц\w*|лет|год\w*)\b",
    re.IGNORECASE,
)
DAY_COUNTS = {
    "одного": 1, "двух": 2, "трех": 3, "четырех": 4, "пяти": 5,
    "шести": 6, "семи": 7, "восьми": 8, "девяти": 9, "десяти": 10,
}
REPLY_THRESHOLD = re.compile(
    rf"\bболее\s+(?P<days>\d+|{'|'.join(DAY_COUNTS)})\s+"
    r"(?:рабочего дня|рабочих д(?:ня|ней))\b",
    re.IGNORECASE,
)
NO_REPLY = re.compile(
    r"\s*(?:\d+(?:\.\d+)*\.\s+)?(?:если\s+(?P<actor>[A-ZА-ЯЁ]{2,})\s+не\s+отвечает|"
    r"в случае отсутствия\s+(?:[^.\n]+?\s+или\s+)?ответа\s+от\s+(?P<sender>[A-ZА-ЯЁ]{2,}))\s*",
    re.IGNORECASE,
)
STATE_TRANSITION = re.compile(
    r"\s*,?\s*(?P<subject>[^.,;:\n]+?)\s+перевод\w+\s+в статус\s+[«\"](?P<state>[^»\"]+)"
)
RECOVERY_STEP = re.compile(r"^\s*\d+(?:\.\d+)*\.\s+(?P<rule>Чтобы вернуть [^,]+ в работу, .+)$", re.IGNORECASE)


def numeric_checks(question, sources):
    # Supply a comparison only when the question specifies one value in the same unit.
    quantities = {"%": set(), "рабочие дни": set()}
    for quantity in re.finditer(QUANTITY, question, re.IGNORECASE):
        unit = "%" if quantity["unit"] == "%" else "рабочие дни"
        quantities[unit].add(Decimal(quantity["number"].replace(",", ".")))

    checks = []
    for source_id, source in enumerate(sources, 1):
        if source.get("source_type") == "qa_entry":
            continue
        for threshold in THRESHOLD.finditer(source["quote"]):
            unit = "%" if threshold["unit"] == "%" else "рабочие дни"
            values = quantities[unit]
            if len(values) != 1:
                continue
            value, = values
            comparison = COMPARISONS[" ".join(threshold["operator"].casefold().split())]
            limit = Decimal(threshold["number"].replace(",", "."))
            checks.append({
                "источник": source_id,
                "значение": str(value),
                "единица": unit,
                "условие": threshold[0],
                "выполнено": comparison(value, limit),
            })
    return checks


def paired_branch(lines, checks):
    values = {check["условие"]: check["выполнено"] for check in checks}
    for index in range(1, len(lines) - 1):
        parent, left, right = lines[index - 1:index + 2]
        if not BRANCH_PARENT.match(parent) or not parent.rstrip().endswith(":"):
            continue
        if not (BRANCH_LINE.match(left) and BRANCH_LINE.match(right)):
            continue
        first, second = THRESHOLD.search(left), THRESHOLD.search(right)
        if not first or not second:
            continue
        if (
            first["unit"].casefold() != second["unit"].casefold()
            or first["number"] != second["number"]
            or OPPOSITE[first["operator"].casefold()] != second["operator"].casefold()
        ):
            continue
        if values.get(first[0]) is True and values.get(second[0]) is False:
            return parent, left, index + 1
        if values.get(second[0]) is True and values.get(first[0]) is False:
            return parent, right, index
    return None


def model_sources(sources, checks, questions_by_id=None):
    result = []
    for source_id, source in enumerate(sources, 1):
        lines = source["quote"].splitlines()
        branch = paired_branch(lines, [check for check in checks if check["источник"] == source_id])
        if branch:
            lines[branch[2]] = "Числовое условие этой ветки не выполнено."
        excerpt = {key: source[key] for key in ("source_type", "directorate", "stage", "section") if key in source}
        excerpt.update(id=source_id, quote="\n".join(lines))
        if questions_by_id and source.get("qa_entry_id") in questions_by_id:
            excerpt["quality_question"] = questions_by_id[source["qa_entry_id"]]
        result.append(excerpt)
    return result


def workday_answer(question, source, checks):
    quote = source["quote"].strip()
    thresholds = list(THRESHOLD.finditer(quote))
    if len(thresholds) != 1 or thresholds[0]["unit"] == "%":
        return None
    if CALENDAR_DAYS.search(question) and not any(check["единица"] == "рабочие дни" for check in checks):
        return "Сколько прошло рабочих дней? Регламент устанавливает срок в рабочих днях.", True
    if len(checks) != 1 or checks[0]["единица"] != "рабочие дни" or "\n" in quote:
        return None
    check = checks[0]
    outcome = "выполняется" if check["выполнено"] else "не выполняется"
    return f"Условие «{check['условие']}» при указанном сроке {outcome}. {quote}", False


def reply_deadline(source):
    """Identify a deadline for changing an object's status when a named role fails to reply."""
    from retrieval.mock import detect_directorates

    if source["source_type"] != "regulation":
        return None
    quote = source["quote"]
    threshold = REPLY_THRESHOLD.search(quote.replace("ё", "е").replace("Ё", "Е"))
    if not threshold:
        return None
    condition = quote[:threshold.start()]
    reply = NO_REPLY.fullmatch(condition)
    transition = STATE_TRANSITION.match(quote, threshold.end())
    if not reply or not transition:
        return None
    _, subject = detect_directorates(transition["subject"])
    key = (source["directorate"], source["stage"], (reply["actor"] or reply["sender"]).casefold(),
           " ".join(subject.casefold().split()), transition["state"].casefold().replace("ё", "е"))
    number = threshold["days"].casefold()
    days = int(number) if number.isdecimal() else DAY_COUNTS[number]
    return key, days, quote[threshold.start():threshold.end()]


def conflicting_reply_deadlines(sources, citations):
    deadlines = [(source, reply_deadline(source)) for source in sources]
    cited_ids = {source["chunk_id"] for source in citations}
    for source, deadline in deadlines:
        if source["chunk_id"] not in cited_ids or not deadline:
            continue
        key, days, wording = deadline
        for other, candidate in deadlines:
            if candidate and candidate[0] == key and candidate[1] != days:
                return [(source, wording), (other, candidate[2])]
    return []


def following_recovery_step(question, sources, citations):
    if not OUTCOME_QUESTION.match(question):
        return None
    for cited in citations:
        prefix, separator, number = cited["section"].rpartition(".")
        if not separator or not number.isdecimal() or not reply_deadline(cited):
            continue
        following_section = f"{prefix}.{int(number) + 1}"
        for source in sources:
            if (source["source_type"] == "regulation" and source["doc_id"] == cited["doc_id"]
                    and source["section"] == following_section):
                if step := RECOVERY_STEP.fullmatch(source["quote"].strip()):
                    return source, step["rule"]
    return None


def ollama_url() -> str:
    base = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    return base.removesuffix("/v1")


def model_name() -> str:
    return os.environ.get("LLM_MODEL", "qwen3:8b")


def model_ready() -> bool:
    try:
        with urlopen(f"{ollama_url()}/api/tags", timeout=1) as response:
            return any(model["name"] == model_name() for model in json.load(response)["models"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def demo_sources():
    sources = [
        {
            "chunk_id": f"{doc_id}::{section}",
            "source_type": "regulation",
            "doc_id": doc_id,
            "doc_title": f"Тестовый регламент — {directorate}",
            "section": section,
            "directorate": directorate,
            "stage": None,
            "quote": quote,
            "qa_entry_id": None,
        }
        for directorate, (doc_id, section, quote) in DIRECTORATES.items()
    ]
    sources.append({
        "chunk_id": "fixture-intake::1.1",
        "source_type": "regulation",
        "doc_id": "fixture-intake",
        "doc_title": "Тестовый регламент обработки заявок",
        "section": "1.1",
        "directorate": None,
        "stage": None,
        "quote": "Тестовую заявку проверяет координатор.",
        "qa_entry_id": None,
    })
    return sources


def quality_source(entry):
    reference = entry["ticket_id"]
    return {
        "chunk_id": f"qa::{entry['qa_entry_id']}",
        "source_type": "qa_entry",
        "doc_id": entry["doc_id"] or f"qa-{reference}",
        "doc_title": f"Разъяснение службы качества · {reference}",
        "section": entry["section"] or reference,
        "directorate": entry["directorate"],
        "stage": entry["stage"],
        "quote": entry["answer"],
        "qa_entry_id": entry["qa_entry_id"],
    }


def document_search_enabled():
    return os.environ.get("AGENT_SOURCES") == "documents"


def document_corpus_available():
    from retrieval.mock import PROCESSED_DIR, RAW_DIR

    return (PROCESSED_DIR / "chunks.jsonl").is_file() or any(RAW_DIR.glob("*.docx"))


def conversation_question(question, history):
    original = question
    clarifications = []
    for index in range(len(history) - 1, 0, -2):
        user, assistant = history[index - 1:index + 1]
        if user["role"] != "user" or assistant["role"] != "assistant" or assistant["status"] != "needs_clarification":
            break
        clarifications.append({"preceding_question": assistant["content"], "reply": original})
        original = user["content"]
    return original, clarifications[::-1]


def search_documents(question, replies):
    from retrieval.mock import DEFAULT_STATUSES, detect_directorates, get_section, search, tokenize

    if not document_corpus_available():
        raise AgentError("Корпус регламентов не загружен. Добавьте DOCX в data/raw и перезапустите сервис.")
    clarification = "\n".join(replies)
    scope, topic = detect_directorates(clarification)
    scope = scope or detect_directorates(question)[0]
    query = "\n".join([question, *replies])
    directorate = next(iter(scope)) if len(scope) == 1 else None
    stage = "пресейл" if directorate and PRESALE.search(query) else None
    try:
        filters = dict(
            directorate=directorate,
            stage=stage,
            allowed_access=["all"],
            include_qa=False,
        )
        result = search(query, k=8, **filters)
        chunks = [hit.chunk for hit in result.hits[:5]]
        if DEADLINE_QUESTION.search(question) and (tokenize(topic) or result.ambiguous_scope):
            deadline_topic = topic if tokenize(topic) else question
            deadlines = search(f"Сроки выдачи {deadline_topic}", k=3, **filters)
            chunks[1:1] = [hit.chunk for hit in deadlines.hits]
        if chunks and not result.ambiguous_scope:
            first = chunks[0]
            prefix, separator, number = first.section.rpartition(".")
            if separator and number.isdecimal():
                follows_process = OUTCOME_QUESTION.match(question) or WAITING_QUESTION.search(question)
                offsets = (-1, 1, 2) if follows_process else (-1,)
                neighbors = (
                    get_section(first.doc_id, f"{prefix}.{int(number) + offset}")
                    for offset in offsets
                )
                chunks[1:1] = [
                    chunk for chunk in neighbors
                    if chunk and chunk.status in DEFAULT_STATUSES and chunk.access_level in filters["allowed_access"]
                ]
        return result, list({chunk.chunk_id: chunk for chunk in chunks}.values())
    except (OSError, BadZipFile, ParseError, KeyError, TypeError, ValueError) as exc:
        raise AgentError("Не удалось прочитать корпус регламентов. Проверьте файлы поиска.") from exc


def shared_rule(question, sources):
    from retrieval.mock import canonicalize_terms, tokenize

    terms = set(tokenize(canonicalize_terms(question)))
    found = {}
    for source_id, source in enumerate(sources, 1):
        directorate = source["directorate"]
        if not directorate:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", source["quote"]):
            expanded = canonicalize_terms(sentence)
            normalized = expanded.casefold().replace("ё", "е")
            if len(terms.intersection(tokenize(normalized))) < 2:
                continue
            words = tuple(re.findall(r"\w+|[%<>]", normalized))
            previous = found.get(words)
            if previous and previous[0] != directorate:
                return expanded, [previous[1], source_id]
            found[words] = (directorate, source_id)
    return None


def direct_rule(question, result, replies):
    from retrieval.mock import detect_directorates, tokenize

    if len(result.scope) != 1 or not result.hits:
        return None
    passage = result.hits[0].chunk.text.strip()
    if len(passage) > 250:
        return None
    if replies and OUTCOME_QUESTION.match(question):
        missing = MISSING_INPUT.search(replies[-1])
        rule = MISSING_INPUT_RULE.fullmatch(passage)
        if missing and rule:
            terms = tokenize(missing[1])
            head = tokenize(rule["tail"].split(",", 1)[0])[:8]
            if len(terms) >= 2 and any(head[i:i + len(terms)] == terms for i in range(len(head))):
                return rule["rule"]
    elif not replies and question.casefold().startswith("как ") and result.top_score >= 0.9:
        rule = DIRECT_PROCEDURE.fullmatch(passage)
        _, topic = detect_directorates(question)
        terms = set(tokenize(topic))
        if rule and len(terms) >= 2 and terms.issubset(tokenize(rule["rule"])):
            return rule["rule"]
    return None


def same_quality_question(question, published_question):
    from retrieval.mock import canonicalize_terms, tokenize

    if normalized_question(question) == normalized_question(published_question):
        return True
    if not (DURATION_QUESTION.search(question) and DURATION_QUESTION.search(published_question)):
        return False
    ignored = set(tokenize("сколько даётся дают отводится отводят выделяется выделяют"))
    condition_words = r"\b(?:без|не|кроме|либо|или|до|после|если|вместо)\b"
    if re.findall(condition_words, question.casefold()) != re.findall(condition_words, published_question.casefold()):
        return False
    terms = set(tokenize(canonicalize_terms(question))) - ignored
    other_terms = set(tokenize(canonicalize_terms(published_question))) - ignored
    return len(terms) >= 3 and terms == other_terms


def ask_model(model, context):
    request = Request(
        f"{ollama_url()}/api/chat",
        data=json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False, separators=(",", ":"))},
            ],
            "format": Decision.model_json_schema(),
            "options": {"temperature": 0, "num_ctx": 8192},
            "stream": False,
            "think": False,
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=float(os.environ.get("AGENT_TIMEOUT_S", "120"))) as response:
            payload = json.load(response)
        return Decision.model_validate_json(payload["message"]["content"]), payload
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise AgentError("Ollama недоступна или вернула некорректный ответ. Проверьте запуск и модель.") from exc


def run_agent(question, history, confirmed_entries=()):
    started_at = datetime.now(UTC).isoformat()
    start = perf_counter()
    original, clarifications = conversation_question(question, history)
    replies = [item["reply"] for item in clarifications]
    query = "\n".join([original, *replies])
    if document_search_enabled():
        result, chunks = search_documents(original, replies)
        from retrieval.mock import search_confirmed_questions

        quality_entries = search_confirmed_questions(query, confirmed_entries)
        sources = [
            {
                "chunk_id": chunk.chunk_id,
                "source_type": chunk.source_type,
                "doc_id": chunk.doc_id,
                "doc_title": chunk.doc_title,
                "section": chunk.section,
                "directorate": chunk.directorate,
                "stage": chunk.stage,
                "quote": chunk.text,
                "qa_entry_id": chunk.qa_entry_id,
            }
            for chunk in chunks
        ]
    else:
        result = None
        sources = demo_sources()
        entry = find_confirmed_answer(original, confirmed_entries)
        quality_entries = [entry] if entry else []
    sources.extend(quality_source(entry) for entry in quality_entries)
    checks = numeric_checks(query, sources)
    model = model_name()
    direct = direct_rule(original, result, replies) if result is not None and not quality_entries else None
    direct_ids = [1]
    direct_quality = False
    if result is not None and not replies:
        verified = [entry for entry in quality_entries if same_quality_question(original, entry["question"])]
        if len(verified) == 1:
            entry = verified[0]
            direct = entry["answer"]
            direct_ids = [i for i, source in enumerate(sources, 1) if source["qa_entry_id"] == entry["qa_entry_id"]]
            direct_quality = True
        elif not quality_entries and result.ambiguous_scope:
            common = shared_rule(original, sources)
            if common:
                direct, direct_ids = common
    if direct:
        decision = Decision(scope="company_process", status="answered", text=direct, source_ids=direct_ids)
        payload = {}
    else:
        questions_by_id = {entry["qa_entry_id"]: entry["question"] for entry in quality_entries}
        decision, payload = ask_model(model, {
            "question": original,
            "clarifications": clarifications,
            "directorates": result.scope if result is not None else [],
            "ambiguous_scope": result.ambiguous_scope if result is not None else False,
            "sources": model_sources(sources, checks, questions_by_id),
            "numeric_checks": checks,
        })

    status = decision.status
    answer = decision.text.strip()
    if decision.scope == "out_of_scope":
        if result is not None and BUSINESS_POLICY.search(original) and BUSINESS_CONTEXT.search(original):
            status = "escalated"
            answer = "В найденных регламентах нет правила для этой рабочей процедуры."
        else:
            status = "refused"
            answer = "Вопрос вне базы корпоративных регламентов. Обратитесь к руководителю."
    elif status == "no_answer":
        status = "escalated"
    elif (status == "needs_clarification" and result is not None and not quality_entries
          and DURATION_QUESTION.search(original) and not any(DURATION.search(source["quote"]) for source in sources)):
        status = "escalated"
        answer = "В найденных регламентах нет срока для этой процедуры."

    source_ids = list(dict.fromkeys(decision.source_ids))
    uses_sources = status in ("answered", "conflict")
    required_sources = 2 if status == "conflict" else 1
    if not answer or (uses_sources and (
        len(source_ids) < required_sources or any(not 1 <= index <= len(sources) for index in source_ids)
    )):
        raise AgentError("Модель вернула ответ без проверяемых источников. Повторите запрос.")

    citations = [sources[index - 1] for index in source_ids] if uses_sources else []
    relies_on_quality = any(source["source_type"] == "qa_entry" for source in citations)
    if result is not None and result.ambiguous_scope and not clarifications:
        possible_directorates = {option["directorate"] for option in result.scope_options}
        cited_directorates = {source["directorate"] for source in citations if source["directorate"]}
        unsupported_scope = (
            status == "answered" and cited_directorates and not relies_on_quality
            and not shared_rule(original, citations)
        )
        if len(possible_directorates) > 1 and (status == "needs_clarification" or unsupported_scope):
            status = "needs_clarification"
            answer = "К какой дирекции относится вопрос? Если известен этап процесса, укажите и его."
            citations = []
    if status == "answered" and result is not None and not relies_on_quality and DEADLINE_QUESTION.search(original):
        competing = conflicting_reply_deadlines(sources, citations)
        if competing:
            citations = [source for source, _ in competing]
            status = "conflict"
            first, second = (f"по пункту {source['section']} — {wording.lower()}" for source, wording in competing)
            answer = (
                f"Источники расходятся. Для одной и той же ситуации {first}, а {second}. "
                "Уточните в службе качества, какой срок применять."
            )
    if status == "answered" and result is not None and not relies_on_quality:
        recovery = following_recovery_step(original, sources, citations)
        if recovery:
            source, rule = recovery
            states = re.findall(r"«([^»]+)»", rule)
            if source not in citations or any(state.casefold() not in answer.casefold() for state in states):
                answer += f" {rule}"
            if source not in citations:
                citations.append(source)
    if len(citations) == 1 and citations[0]["source_type"] == "regulation":
        checks_for_citation = [check for check in checks if check["источник"] == source_ids[0]]
        workday_result = workday_answer(query, citations[0], checks_for_citation)
        if workday_result is not None:
            answer, needs_workdays = workday_result
            if needs_workdays:
                status = "needs_clarification"
                citations = []
        elif branch := paired_branch(citations[0]["quote"].splitlines(), checks_for_citation):
            parent, selected, _ = branch
            condition = re.sub(r"^\s*\d+[.)]\s*", "", parent).strip()
            answer += f" Условие из регламента: «{condition} {BRANCH_LINE.sub('', selected).strip()}»"
    if citations:
        answer += " " + " ".join(f"[{index}]" for index in range(1, len(citations) + 1))
    if direct:
        step_title = ("Нашёл подтверждённое разъяснение службы качества" if direct_quality
                      else "Нашёл прямое правило в регламенте")
    else:
        step_title = ("Проверяю найденные пункты с локальной моделью" if result is not None
                      else "Проверяю учебные источники с локальной моделью")
    elapsed = round((perf_counter() - start) * 1000)
    return {
        "status": status,
        "answer": answer,
        "citations": citations,
        "reason": answer if status in ("escalated", "refused") else None,
        "clarification": {"question": answer} if status == "needs_clarification" else None,
        "ticket": None,
        "steps": [{
            "name": "retrieve" if direct else "generate",
            "title": step_title,
            "detail": {
                "prompt_tokens": payload.get("prompt_eval_count"),
                "completion_tokens": payload.get("eval_count"),
            },
            "started_at": started_at,
            "duration_ms": elapsed,
        }],
        "metrics": {
            "n_hits": len(chunks) if result is not None else len(citations),
            "top_score": result.top_score if result is not None else None,
            "n_retrieval_rounds": int(result is not None),
            "llm_calls": int(not direct),
        },
        "model": None if direct else model,
        "latency_ms": elapsed,
    }
