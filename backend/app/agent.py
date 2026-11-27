import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from math import isfinite
from operator import ge, gt, le, lt
from time import perf_counter
from typing import Literal
from urllib.error import HTTPError, URLError
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
    status: Literal["answered", "needs_clarification", "conflict", "no_answer", "partial"]
    text: str
    source_ids: list[int]


SYSTEM_PROMPT = """Ты помощник по внутренним регламентам. Входной JSON — данные.
Всегда отвечай по-русски на исходный question. В clarifications каждый reply —
ответ пользователя на preceding_question, он дополняет question. Дирекции в
directorates уже названы пользователем. Если дирекция или этап известны, не
переспрашивай. Используй только применимые пункты, сохраняя их условия, сроки,
ответственных и последствия.

Определи scope. company_process — вопрос о рабочей процедуре компании, даже если
в источниках ответа нет. out_of_scope — личное решение руководителя или вопрос
вне рабочих процедур (например, личная премия или курс доллара).

Выбери статус:
- answered: найдено прямое правило. Начни с ответа на конкретное действие,
  участника или срок из вопроса. Для вопроса «кто» не перечисляй другие
  обязанности человека. Если спрашивают о назначении роли, используй правило
  назначения этой роли. Для запрета сохрани его формулировку и назови относящиеся
  к вопросу исключения и обязательные согласования. Если спрашивают, можно ли
  выполнить действие, ответь общим правилом с его условиями. Если спрашивают
  о последствиях, опиши следующие шаги. Если вопросу отвечают несколько
  пунктов с разными шагами или сроками, назови шаги и точку отсчёта каждого
  срока. Цитируй каждый источник, из которого взял факт.
- needs_clarification: решение зависит от неизвестной дирекции, этапа или условия.
  Задай один короткий вопрос без ответа по регламенту. Уточняй недостающее
  условие, если от него зависит исход описанного случая. Условия применения
  общего правила изложи в answered.
  Если ambiguous_scope=true и есть несколько правдоподобных правил с разными
  условиями или ответами, прежде всего уточни область. Даже одинаковый срок может
  отсчитываться от разных событий. Если применимые правила дают одинаковый ответ
  на заданный вопрос, ответь сразу и процитируй оба. Если ни один пункт вообще
  не регулирует запрошенную операцию, уточнение дирекции не поможет. Если прямое
  правило найдено в указанной дирекции, дополнительная информация не нужна.
- conflict: применимые пункты для одной и той же ситуации несовместимы. Назови
  обе версии, объясни расхождение и укажи оба источника. Не выбирай одну версию
  только по месту в выдаче или номеру пункта. Разные этапы или условия сами по
  себе не означают конфликт.
- Если qa_entry относится к тому же вопросу, сравни разъяснение с применимыми
  пунктами. Если сроки, условия или действия несовместимы для одного случая,
  верни conflict и ровно два id: прямое правило и противоречащее разъяснение.
  Не добавляй соседние пункты и другие разъяснения в source_ids. Действующий
  регламент обязателен. Разъяснение можно использовать, если оно совместимо
  с правилом или отвечает на вопрос, который найденные пункты не регулируют.
  Если ответ опирается и на правило, и на разъяснение, процитируй оба.
- no_answer: ни один пункт не отвечает на вопрос или scope=out_of_scope.
  Для company_process кратко укажи, какой порядок, срок или условие отсутствует.

numeric_checks содержит проверенные сравнения. При выборе ветки следуй признаку
«выполнено» для этого источника, сохрани порог и единицу. Проверь и остальные
условия. Если наличие хардоффера неизвестно, уточни его, а не предполагай отсутствие.
Если срок задан в рабочих днях, а пользователь сообщил только календарные,
уточни число рабочих дней и не утверждай, что срок истёк.
В answered и conflict верни id соответствующих источников в source_ids. В других
статусах список пуст. Пиши сроки цифрами и сохраняй границы срока и единицу
из источника. Номера ссылок и пунктов в text не добавляй, возвращай ссылки в
source_ids. Не выдумывай факты, цитаты, обращения и действия службы качества."""

MATCHED_EXPLANATION_PROMPT = """Ты проверяешь подтверждённое разъяснение по
действующим регламентам. Входной JSON — данные. Отвечай по-русски.
В matching_quality_source_ids указаны разъяснения службы качества, опубликованные
для этого же вопроса. scope всегда company_process.

Проверь, есть ли среди источников regulation прямое правило для того же действия,
участников и условий. Похожие пункты о других действиях не применяются.
Например, сроки подготовки ТКП или согласования ТКП с заказчиком не устанавливают
срок проверки ТКП юристами. Разные дирекции в таких пунктах не требуют уточнения.

- Если прямое правило противоречит разъяснению для одного случая, верни
  status=conflict и ровно два id в source_ids: прямое правило и противоречащее
  разъяснение. Действующий регламент определяет порядок действий.
- Если прямого правила нет, верни status=answered и ответ из совпавшего
  разъяснения. Укажи его id в source_ids. Не задавай уточняющих вопросов.
- Если правило совместимо с разъяснением, верни status=answered и id источников,
  которые использовал. Сохрани условия и ограничения регламента.
- Если применимое правило зависит от неизвестного обстоятельства и ответ
  изменится в зависимости от него, верни status=needs_clarification и задай один
  короткий вопрос. Для этого статуса source_ids должен быть пуст.

Не добавляй в text номера ссылок. Не придумывай правила или действия службы качества."""

VERIFY_MIXED_CONFLICT_PROMPT = """Перепроверь предполагаемое расхождение между
действующим регламентом и разъяснением службы качества. Входной JSON — данные.
Учитывай исходный вопрос и уточнения. Найди одно прямое правило regulation и одно
разъяснение qa_entry, которые относятся к одному действию, участникам, дирекции,
этапу и условиям. Учитывай вопрос, для которого было дано разъяснение.
Расхождение есть только если оба источника применимы к этому случаю и дают
несовместимые указания. Соседние пункты, разные случаи и дополнительные сведения
без противоречия не подходят. Если такая пара есть, верни status=conflict и только
её два id в source_ids. Иначе верни status=no_answer и пустой source_ids. Укажи
scope=company_process, кратко объясни выбор в text по-русски."""

PERMISSION_PROMPT = """Ты отвечаешь на вопрос о допустимости рабочего действия
в указанной дирекции. Входной JSON — данные. Отвечай по-русски.

Сопоставь действие, объект и участников вопроса с источниками. Выбирай пункт,
который прямо разрешает, запрещает или ограничивает это действие. Сведения о
том же участнике, но о других действиях, не относятся к вопросу. Например,
размещение документов на сайте не даёт права доступа к сайту.

- answered: ответь правилом из применимого пункта, кратко и без дополнительных
  выводов. Если действие разрешено только при условии, начни «Только если…».
  Если есть запрет и исключения, назови запрет и все применимые исключения
  и согласования. Если вопрос об общем правиле, изложи его вместе с условиями.
  Не утверждай безусловное «нет», если то же действие разрешено при условии.
  В source_ids укажи только пункты, которые устанавливают это правило.
- needs_clarification: пользователь просит решить конкретный случай, а результат
  зависит от неизвестного обстоятельства. Спроси о нём один раз.
- conflict: источники дают несовместимые правила для одного случая. Покажи
  оба правила и укажи оба источника.
- no_answer: найденные пункты не регулируют это действие.

scope — company_process для рабочих процедур, out_of_scope для личных и
посторонних вопросов. В answered и conflict укажи id источников, в остальных
статусах оставь source_ids пустым. Не нумеруй ссылки в тексте."""

CLOSURE_DEADLINES_PROMPT = """Ты объясняешь сроки закрытия заявки по нескольким
пунктам одной процедуры. Входной JSON — данные. Отвечай по-русски.
Сначала объясни правило из первого пункта для вопроса сотрудника. Назови действия,
которые предшествуют закрытию, сроки этих действий и итоговый статус заявки.
Затем объясни порядок по остальным пунктам. Для каждого правила сохрани точку
отсчёта (например, от создания заявки или от последнего обновления), границы
срока, дни и условия из текста. Используй цифры для сроков.
Разные условия или точки отсчёта сами по себе не конфликтуют. Если правила
совместимы, верни answered. Если правила противоречат для одного и того же
случая и отсчёта, верни conflict и объясни расхождение. Верни в source_ids все
описанные пункты. scope — company_process. Не нумеруй ссылки в тексте."""

CLARIFICATION_LIMIT_PROMPT = """
Сотрудник не знает уточняющий факт или уже ответил на два уточнения. Не задавай
новый вопрос. Если можно ответить однозначно, верни answered. Иначе перечисли
только применимые варианты по найденным пунктам регламента, каждый с его
дирекцией, этапом или условием. Для этого верни partial и укажи id всех
использованных пунктов в порядке изложения. Если применимых вариантов нет,
верни no_answer.
"""

MAX_CLARIFICATION_ROUNDS = 2
UNKNOWN_CLARIFICATION = re.compile(
    r"(?:я\s+)?(?:не\s+знаю|не\s+уверен[а]?|затрудняюсь\s+ответить|не\s+могу\s+уточнить)",
    re.IGNORECASE,
)
SCOPE_CLARIFICATION = re.compile(r"\b(?:дирекци\w*|подразделени\w*|этап\w*|пресейл\w*)\b", re.IGNORECASE)

PERMISSION_QUESTION = re.compile(
    r"\b(?:можно|может ли|могут ли|вправе ли|допустимо ли|разрешено ли|разрешается ли)\b",
    re.IGNORECASE,
)
CONDITIONAL_CASE = re.compile(r"\b(?:если|когда|в случае|при условии)\b", re.IGNORECASE)
PROHIBITION = re.compile(r"\b(?:запрещ\w*|не (?:допуска\w*|может использоваться))\b", re.IGNORECASE)
PERMISSION_RULE = re.compile(r"\b(?:запрещ\w*|разреш\w*|допуска\w*|вправе|может|могут|только)\b", re.IGNORECASE)
EXCEPTION = re.compile(r"\b(?:кроме|исключени\w*)\b", re.IGNORECASE)
REQUEST_CLOSURE = re.compile(r"\b(?:закро\w*|закры\w*|дисквалифиц\w*)\b", re.IGNORECASE)
CLAUSE_STATUS = re.compile(r"\bстатус\w*\s+(?:заявки\s+)?(?:в\s+)?«([^»]+)»", re.IGNORECASE)
CLAUSE_NUMBER = re.compile(r"^\d+(?:\.\d+)*\.\s+")

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
    rf"\b(?P<operator>не\s+(?:более|менее)|более)\s+(?P<days>\d+|{'|'.join(DAY_COUNTS)})\s+"
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


def reply_deadline(source, *, include_inclusive=False):
    """Identify a deadline for changing an object's status when a named role fails to reply."""
    from retrieval.mock import detect_directorates

    if source["source_type"] != "regulation":
        return None
    quote = source["quote"]
    threshold = REPLY_THRESHOLD.search(quote.replace("ё", "е").replace("Ё", "Е"))
    if not threshold:
        return None
    operator = " ".join(threshold["operator"].casefold().split())
    if operator != "более" and not (include_inclusive and operator == "не менее"):
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
    return key, (operator, days), quote[threshold.start():threshold.end()]


def conflicting_reply_deadlines(sources, citations, *, include_inclusive=False):
    deadlines = [(source, reply_deadline(source, include_inclusive=include_inclusive)) for source in sources]
    cited_ids = {source["chunk_id"] for source in citations}
    for source, deadline in deadlines:
        if source["chunk_id"] not in cited_ids or not deadline:
            continue
        key, boundary, wording = deadline
        for other, candidate in deadlines:
            if candidate and candidate[0] == key and candidate[1] != boundary:
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

    if (PROCESSED_DIR / "chunks.jsonl").is_file():
        return True
    return RAW_DIR.is_dir() and any(
        path.is_file() and path.suffix.lower() == ".docx" and not path.name.startswith("~$")
        for path in RAW_DIR.iterdir()
    )


def document_search_ready():
    from retrieval.mock import regulations_available

    try:
        return document_corpus_available() and regulations_available()
    except (OSError, BadZipFile, ParseError, KeyError, TypeError, ValueError):
        return False


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


def search_documents(question, replies, *, include_scope_variants=False):
    from retrieval.mock import DEFAULT_STATUSES, canonicalize_terms, detect_directorates, get_section, search, tokenize

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
        if include_scope_variants:
            for option in result.scope_options:
                chunk = get_section(option["doc_id"], option["section"])
                if chunk and chunk.status in DEFAULT_STATUSES and chunk.access_level in filters["allowed_access"]:
                    chunks.append(chunk)
        if directorate and PERMISSION_QUESTION.search(question) and len(result.hits) > len(chunks):
            _, action = detect_directorates(PERMISSION_QUESTION.sub("", query))
            terms = set(tokenize(canonicalize_terms(action)))
            closest = max(
                result.hits,
                key=lambda hit: (
                    len(terms.intersection(tokenize(canonicalize_terms(hit.chunk.text)))),
                    -len(hit.chunk.text),
                ),
            )
            if closest.chunk not in chunks:
                chunks.insert(0, closest.chunk)
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


def closure_deadline_sources(question, chunks):
    """Keep rules that share both the waiting and closing states of the request."""
    asks_about_time = DEADLINE_QUESTION.search(question) or WAITING_QUESTION.search(question)
    if not chunks or not asks_about_time or not REQUEST_CLOSURE.search(question):
        return []
    first = chunks[0]
    statuses = set(CLAUSE_STATUS.findall(first.text.casefold()))
    if (first.source_type != "regulation" or len(statuses) < 2
            or not REQUEST_CLOSURE.search(first.text) or not DURATION.search(first.text)):
        return []

    related = [
        chunk for chunk in chunks[1:]
        if chunk.doc_id == first.doc_id and chunk.source_type == "regulation"
        and REQUEST_CLOSURE.search(chunk.text) and DURATION.search(chunk.text)
        and statuses <= set(CLAUSE_STATUS.findall(chunk.text.casefold()))
    ]
    return [first, *related] if related else []


def closure_deadline_answer(chunks):
    waiting = CLAUSE_STATUS.search(chunks[0].text)[1].casefold()
    rules = []
    for chunk in chunks:
        text = CLAUSE_NUMBER.sub("", chunk.text.strip())
        sentences = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])", text)
        start = next(
            (i for i, sentence in enumerate(sentences) if waiting in CLAUSE_STATUS.findall(sentence.casefold())), 0
        )
        rules.append(f"Пункт {chunk.section}. " + " ".join(sentences[start:]))
    return "\n\n".join(rules)


def direct_prohibition(question, sources):
    from retrieval.mock import canonicalize_terms, detect_directorates, tokenize

    if not PERMISSION_QUESTION.search(question) or CONDITIONAL_CASE.search(question):
        return None
    _, topic = detect_directorates(question)
    terms = set(tokenize(canonicalize_terms(PERMISSION_QUESTION.sub("", topic))))
    applicable = []
    for index, source in enumerate(sources, 1):
        passage = source["quote"].strip()
        if not PERMISSION_RULE.search(passage):
            continue
        matched = terms.intersection(tokenize(canonicalize_terms(passage)))
        if len(matched) >= 3 and len(terms - matched) <= 1:
            applicable.append((passage, index))
    if len(applicable) == 1:
        passage, index = applicable[0]
        if len(passage) <= 300 and PROHIBITION.search(passage):
            return CLAUSE_NUMBER.sub("", passage), [index]
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
    system_prompt = SYSTEM_PROMPT
    if context.get("verify_mixed_conflict"):
        system_prompt = VERIFY_MIXED_CONFLICT_PROMPT
    elif context.get("matching_quality_source_ids"):
        system_prompt = MATCHED_EXPLANATION_PROMPT
    elif context.get("closure_deadlines"):
        system_prompt = CLOSURE_DEADLINES_PROMPT
    elif context.get("permission_question"):
        system_prompt = PERMISSION_PROMPT
    if context.get("clarification_limit"):
        system_prompt += CLARIFICATION_LIMIT_PROMPT
    try:
        timeout = float(os.environ.get("AGENT_TIMEOUT_S", "120"))
    except ValueError as exc:
        raise AgentError("AGENT_TIMEOUT_S должен быть положительным числом секунд.") from exc
    if not isfinite(timeout) or timeout <= 0:
        raise AgentError("AGENT_TIMEOUT_S должен быть положительным числом секунд.")
    request = Request(
        f"{ollama_url()}/api/chat",
        data=json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
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
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        return Decision.model_validate_json(payload["message"]["content"]), payload
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, HTTPError) and exc.code == 503:
            raise AgentError("Ollama сейчас перегружена. Повторите вопрос позже.") from exc
        if isinstance(exc, TimeoutError) or (isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)):
            raise AgentError("Ollama не успела ответить. Повторите вопрос позже.") from exc
        raise AgentError("Ollama недоступна или вернула некорректный ответ. Проверьте запуск и модель.") from exc


def verify_mixed_conflict(model, question, clarifications, citations, quality_entries):
    query = "\n".join([question, *(item["reply"] for item in clarifications)])
    questions = {entry["qa_entry_id"]: entry["question"] for entry in quality_entries}
    checks = numeric_checks(query, citations)
    context = {
        "verify_mixed_conflict": True,
        "question": question,
        "clarifications": clarifications,
        "sources": model_sources(citations, checks, questions),
    }
    decision, payload = ask_model(model, context)
    ids = decision.source_ids
    if (decision.status == "conflict" and decision.scope == "company_process"
            and len(ids) == 2 and all(1 <= index <= len(citations) for index in ids)):
        pair = [citations[index - 1] for index in ids]
        if {source["source_type"] for source in pair} == {"regulation", "qa_entry"}:
            return pair, payload
    return [], payload


def run_agent(question, history, confirmed_entries=()):
    started_at = datetime.now(UTC).isoformat()
    start = perf_counter()
    original, clarifications = conversation_question(question, history)
    replies = [item["reply"] for item in clarifications]
    known_replies = [reply for reply in replies if not UNKNOWN_CLARIFICATION.fullmatch(reply.strip().rstrip(".!?…"))]
    clarification_limit = len(clarifications) >= MAX_CLARIFICATION_ROUNDS or len(known_replies) < len(replies)
    replies = known_replies
    query = "\n".join([original, *replies])
    closure_deadlines = []
    if document_search_enabled():
        result, chunks = search_documents(original, replies, include_scope_variants=clarification_limit)
        from retrieval.mock import search_confirmed_questions

        quality_entries = search_confirmed_questions(query, confirmed_entries)
        if len(result.scope) == 1 and not quality_entries and not DURATION.search(query):
            closure_deadlines = closure_deadline_sources(original, chunks)
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
            for chunk in closure_deadlines or chunks
        ]
    else:
        result = None
        sources = demo_sources()
        entry = find_confirmed_answer(original, confirmed_entries)
        quality_entries = [entry] if entry else []
    sources.extend(quality_source(entry) for entry in quality_entries)
    checks = numeric_checks(query, sources)
    model = model_name()
    permission_question = (
        result is not None and len(result.scope) == 1 and not quality_entries
        and not replies and PERMISSION_QUESTION.search(original)
    )
    direct = None
    direct_ids = [1]
    if result is not None and not quality_entries and not closure_deadlines:
        prohibition = direct_prohibition(original, sources) if permission_question else None
        if prohibition:
            direct, direct_ids = prohibition
        else:
            direct = direct_rule(original, result, replies)
    direct_quality = False
    matching_quality_ids = []
    if result is not None and not replies:
        verified = {entry["qa_entry_id"] for entry in quality_entries
                    if same_quality_question(original, entry["question"])}
        matching_quality_ids = [i for i, source in enumerate(sources, 1) if source["qa_entry_id"] in verified]
        if len(matching_quality_ids) == 1 and not chunks:
            direct_ids = matching_quality_ids
            direct = sources[direct_ids[0] - 1]["quote"]
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
        context = {
            "question": original,
            "clarifications": clarifications,
            "directorates": result.scope if result is not None else [],
            "ambiguous_scope": result.ambiguous_scope if result is not None else False,
            "sources": model_sources(sources, checks, questions_by_id),
            "numeric_checks": checks,
        }
        if clarification_limit:
            context["clarification_limit"] = True
        if matching_quality_ids:
            context["matching_quality_source_ids"] = matching_quality_ids
            del context["ambiguous_scope"]
        elif closure_deadlines:
            context["closure_deadlines"] = True
        elif permission_question:
            context["permission_question"] = True
        decision, payload = ask_model(model, context)

    status = decision.status
    answer = decision.text.strip()
    unresolved = "Не удалось однозначно определить применимое правило."
    source_ids = list(dict.fromkeys(decision.source_ids))
    variants = []
    if status == "partial" and decision.scope == "company_process":
        if (clarification_limit and answer and source_ids
                and all(1 <= index <= len(sources) and sources[index - 1]["source_type"] == "regulation"
                        for index in source_ids)):
            variants = [sources[index - 1] for index in source_ids]
        else:
            answer = unresolved
        status = "escalated"
    elif clarification_limit and (decision.scope == "out_of_scope" or status in ("no_answer", "needs_clarification")):
        status = "escalated"
        answer = unresolved
    elif decision.scope == "out_of_scope":
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

    uses_sources = status in ("answered", "conflict")
    required_sources = 2 if status == "conflict" else 1
    if not answer or (uses_sources and (
        len(source_ids) < required_sources or any(not 1 <= index <= len(sources) for index in source_ids)
    )):
        raise AgentError("Модель вернула ответ без проверяемых источников. Повторите запрос.")

    citations = [sources[index - 1] for index in source_ids] if uses_sources else []
    relies_on_quality = any(source["source_type"] == "qa_entry" for source in citations)
    if result is not None and result.ambiguous_scope and (not clarifications or clarification_limit):
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
    if (result is not None and not relies_on_quality and DEADLINE_QUESTION.search(original)
            and (status == "answered" or (status == "conflict" and len(citations) == 2))):
        model_conflict = status == "conflict"
        deadline_sources = citations if model_conflict else sources
        competing = conflicting_reply_deadlines(deadline_sources, citations, include_inclusive=model_conflict)
        if competing:
            citations = [source for source, _ in competing]
            status = "conflict"
            first, second = (f"п. {source['section']} — {wording.lower()}" for source, wording in competing)
            answer = (
                f"Источники расходятся. Для одной и той же ситуации в первом источнике {first}, "
                f"а во втором {second}. "
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
    if status == "answered" and closure_deadlines:
        answer = closure_deadline_answer(closure_deadlines)
        citations = sources
    if status == "answered" and permission_question and not CONDITIONAL_CASE.search(original):
        rules = [source for source in citations if PERMISSION_RULE.search(source["quote"])]
        if len(rules) == 1:
            passage = rules[0]["quote"].strip()
            if len(passage) <= 300 and PROHIBITION.search(passage) and EXCEPTION.search(passage):
                answer = CLAUSE_NUMBER.sub("", passage)
                citations = rules
    if clarification_limit and status == "needs_clarification":
        status = "escalated"
        answer = unresolved
        citations = []
    if variants:
        citations = variants

    clarification = None
    if status == "needs_clarification":
        clarification = {"question": answer}
        if result is not None and SCOPE_CLARIFICATION.search(answer):
            options = list(dict.fromkeys(option.get("label") or option["directorate"] for option in result.scope_options))
            if options:
                clarification["options"] = [*options, "Не знаю"]
    reason = answer if status in ("escalated", "refused") else None
    verification_step = None
    if status == "conflict":
        regulations = [source for source in citations if source["source_type"] == "regulation"]
        explanations = [source for source in citations if source["source_type"] == "qa_entry"]
        if regulations and explanations:
            if len(citations) > 2:
                verification_at = datetime.now(UTC).isoformat()
                verification_start = perf_counter()
                pair, verification_payload = verify_mixed_conflict(
                    model, original, clarifications, citations, quality_entries,
                )
                verification_step = {
                    "name": "verify_conflict",
                    "title": "Сверяю разъяснение с применимым пунктом регламента",
                    "detail": {
                        "prompt_tokens": verification_payload.get("prompt_eval_count"),
                        "completion_tokens": verification_payload.get("eval_count"),
                    },
                    "started_at": verification_at,
                    "duration_ms": round((perf_counter() - verification_start) * 1000),
                }
                regulations = [source for source in pair if source["source_type"] == "regulation"]
                explanations = [source for source in pair if source["source_type"] == "qa_entry"]
            citations = regulations + explanations
            if citations:
                rule = regulations[0]["quote"].strip()
                reason = "Разъяснение службы качества расходится с действующим регламентом."
                answer = f"{reason} Применяйте правило из регламента.\n«{rule}»"
            else:
                status = "escalated"
                answer = reason = "Не удалось надёжно сверить разъяснение службы качества с регламентом."
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
        "reason": reason,
        "clarification": clarification,
        "ticket": None,
        "steps": [{
            "name": "retrieve" if direct else "generate",
            "title": step_title,
            "detail": {
                "prompt_tokens": payload.get("prompt_eval_count"),
                "completion_tokens": payload.get("eval_count"),
            },
            "started_at": started_at,
            "duration_ms": elapsed - (verification_step["duration_ms"] if verification_step else 0),
        }] + ([verification_step] if verification_step else []),
        "metrics": {
            "n_hits": len(chunks) if result is not None else len(citations),
            "top_score": result.top_score if result is not None else None,
            "n_retrieval_rounds": int(result is not None),
            "llm_calls": int(not direct) + int(verification_step is not None),
        },
        "model": None if direct else model,
        "latency_ms": elapsed,
    }
