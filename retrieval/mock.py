"""Заглушка поиска по контракту (docs/02-contracts.md §2) — только стандартная библиотека.

Назначение: дать агенту (разработчик 4) и eval рабочий `search()` в первый час спринта,
пока нет настоящего индекса (BM25 + bge-m3 + реранкер).

Источник пунктов:
1. `data/processed/chunks.jsonl` от ingest (разработчик 2), если файл есть;
2. иначе — временный разбор 4 DOCX из `data/raw/` (`retrieval.docx_sections`).

Поиск: BM25 по псевдо-стеммингу (префикс 5 символов) + расширение запроса синонимами
из `retrieval/synonyms.json` (глоссарий заказчика). Подтверждённые записи службы
качества (`upsert_qa_entry`) хранятся в `data/processed/qa_entries.jsonl` и ищутся вместе
с регламентами (`source_type = "qa_entry"`).

Типы — dataclass с теми же полями, что в контракте; `to_dict()` — для JSON/SSE.
Когда появится `core/schemas.py`, поменяем импорт, поля совпадают.

    from retrieval.mock import search
    res = search("Кто назначает РП на проект ИБ?", k=5)
    for h in res.hits: print(h.chunk.chunk_id, round(h.score, 2))
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = Path(os.environ.get("RAW_DIR", ROOT / "data" / "raw"))
PROCESSED_DIR = Path(os.environ.get("PROCESSED_DIR", ROOT / "data" / "processed"))
SYNONYMS_PATH = Path(__file__).resolve().parent / "synonyms.json"
QA_PATH = PROCESSED_DIR / "qa_entries.jsonl"

DEFAULT_STATUSES = ("active", "unknown")

# Метаданные документов корпуса (временно здесь; потом — из documents.jsonl от ingest).
DOC_META: dict[str, dict] = {
    "ib-project": {"directorate": "ИБ", "stage": "проект", "doc_type": "регламент"},
    "ib-presale": {"directorate": "ИБ", "stage": "пресейл", "doc_type": "регламент"},
    "dbrct-project": {"directorate": "ДБРЦТ", "stage": None, "doc_type": "регламент"},
    "tech-project": {"directorate": "Техдирекция", "stage": None, "doc_type": "регламент"},
}


# ---------------------------------------------------------------- типы (контракт §1–2)

@dataclass
class Document:
    doc_id: str
    title: str
    doc_type: str = "регламент"
    directorate: str | None = None
    stage: str | None = None
    number: str | None = None
    version: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    status: str = "unknown"
    status_reason: str | None = None
    supersedes: list[str] = field(default_factory=list)
    superseded_by: str | None = None
    access_level: str = "all"
    source_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    section: str
    text: str
    source_type: str = "regulation"
    section_title: str | None = None
    heading_path: list[str] = field(default_factory=list)
    page: int | None = None
    directorate: str | None = None
    stage: str | None = None
    status: str = "unknown"
    access_level: str = "all"
    qa_entry_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Hit:
    chunk: Chunk
    score: float
    bm25_score: float | None = None
    dense_score: float | None = None
    # Родительские пункты для вложенного (1.1.1 → 1.1): условие часто задаёт родитель
    # («На данном этапе SS проводит: …»). Ближайший родитель — первым. Пункты-заголовки разделов не включаем.
    parents: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"chunk": self.chunk.to_dict(), "score": self.score,
                "bm25_score": self.bm25_score, "dense_score": self.dense_score, "parents": self.parents}


@dataclass
class SearchResult:
    hits: list[Hit]
    restricted: list[dict] = field(default_factory=list)
    top_score: float | None = None
    expanded_query: str = ""  # отладка: запрос после синонимов
    coverage: float | None = None  # доля «веса» слов запроса (по idf), найденных в лучшем пункте
    low_confidence: bool = False   # подсказка агенту: похоже, в регламентах ответа нет (НЕ решение)
    scope: list[str] = field(default_factory=list)  # дирекции, распознанные в вопросе
    mode: str = "bm25"             # bm25 | hybrid | hybrid+rerank
    ambiguous_scope: bool = False  # подсказка для clarify: подходящие пункты из РАЗНЫХ дирекций/этапов, а в вопросе их нет
    scope_options: list[dict] = field(default_factory=list)  # варианты для уточняющего вопроса (лучший пункт каждой области)

    def to_dict(self) -> dict:
        return {"hits": [h.to_dict() for h in self.hits], "restricted": self.restricted,
                "top_score": self.top_score, "expanded_query": self.expanded_query,
                "coverage": self.coverage, "low_confidence": self.low_confidence,
                "scope": self.scope, "mode": self.mode,
                "ambiguous_scope": self.ambiguous_scope, "scope_options": self.scope_options}


# ---------------------------------------------------------------- текст

_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.I)
_STOP = set("""и в во не на с со по за от до из к ко о об обо у для при без над под то же ли бы
это как что кто а но или если то есть все его ее её их он она они мы вы я быть был была были
также так этот эта эти этого этой должен должна должны может могут нужно надо который которая
которые которых чтобы где когда ли же ни the a an of to for and or in on is are be by""".split())


_ENDINGS = sorted("""иями ями ами ого его ому ему ыми ими ией иях ах ях ов ев ей ий ый ой ая яя ое ее ые ие ую юю ом ем ам ям
ть ла ло ли ет ит ют ат ят ешь ишь ется ится ются ются ок ек ы и а я о е у ю ь й""".split(), key=len, reverse=True)


def _stem(w: str) -> str:
    """Лёгкий стеммер: срезать окончание (основа ≥ 3 букв), затем обрезать до 5 символов."""
    w = w.lower().replace("ё", "е")
    if w.isascii():
        return w[:6]
    for e in _ENDINGS:
        if len(w) - len(e) >= 3 and w.endswith(e):
            w = w[: -len(e)]
            break
    return w[:5]


def tokenize(text: str) -> list[str]:
    return [_stem(w) for w in _WORD_RE.findall(text) if w.lower() not in _STOP]


@lru_cache(maxsize=1)
def _synonyms() -> list[tuple[str, list[str]]]:
    """[(каноническая форма, [алиасы])]"""
    if not SYNONYMS_PATH.exists():
        return []
    data = json.loads(SYNONYMS_PATH.read_text(encoding="utf-8"))
    return [(k, v) for k, v in data.items() if not k.startswith("_")]


def _is_abbr(phrase: str) -> bool:
    """Короткое сокращение в верхнем регистре («ПО», «РП», «ТП», «SS»): ищем с учётом регистра,
    иначе предлог «по» превращается в «программное обеспечение»."""
    return len(phrase) <= 5 and phrase.isupper()


def _phrase_in(phrase: str, text_low: str, text_orig: str = "") -> bool:
    if _is_abbr(phrase) and text_orig:
        return re.search(rf"(?<![A-Za-zА-Яа-яЁё0-9]){re.escape(phrase)}(?![A-Za-zА-Яа-яЁё0-9])", text_orig) is not None
    p = re.escape(phrase.lower().replace("ё", "е"))
    return re.search(rf"(?<![a-zа-я0-9]){p}(?![a-zа-я0-9])", text_low) is not None


_DIRECTORATE_RE = {
    "ИБ": re.compile(r"(?<![a-zа-я])(иб|диб|уиб)(?![a-zа-я])|информационн\w* безопасн\w*|infosec|information security"),
    "ДБРЦТ": re.compile(r"дбрцт|бизнес-решени\w* и цифров\w*|цифров\w* трансформ\w*"),
    "Техдирекция": re.compile(r"техдирекц\w*|тех\.? ?дирекц\w*|техническ\w* дирекц\w*"),
}
_DIRECTORATE_WORD_RE = re.compile(r"(?<![a-zа-я])(дирекци\w*|департамент\w*|управлени\w*)(?![a-zа-я])")
_DIRECTORATE_CANON = {
    "Дирекция информационной безопасности", "Управление информационной безопасности",
    "Департамент бизнес-решений и цифровой трансформации", "Техническая дирекция",
}


def detect_directorates(query: str) -> tuple[set[str], str]:
    """Найти упомянутые дирекции и убрать их из текста запроса (они — область, а не слова поиска)."""
    text = query.replace("ё", "е").replace("Ё", "Е")  # регистр сохраняем: он нужен сокращениям («ПО», «РП»)
    found = set()
    for name, rx in _DIRECTORATE_RE.items():
        rxi = re.compile(rx.pattern, re.I)
        if rxi.search(text):
            found.add(name)
            text = rxi.sub(" ", text)
    if found:
        text = re.compile(_DIRECTORATE_WORD_RE.pattern, re.I).sub(" ", text)
    return found, text


def expand(text: str, skip: set[str] | frozenset = frozenset()) -> str:
    """Добавляет к тексту каноническую форму для найденных алиасов и наоборот."""
    low = text.lower().replace("ё", "е")
    extra: list[str] = []
    for canon, aliases in _synonyms():
        if canon in skip:
            continue
        names = [canon, *aliases]
        if any(_phrase_in(n, low, text) for n in names):
            extra.extend(n for n in names if not _phrase_in(n, low, text))
    return text + ("\n" + " ".join(extra) if extra else "")


# ---------------------------------------------------------------- загрузка корпуса

def _chunks_from_jsonl(path: Path) -> list[Chunk]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            d.setdefault("source_type", "regulation")
            out.append(Chunk(**{k: v for k, v in d.items() if k in Chunk.__dataclass_fields__}))
    return out


def load_docx_corpus(*, require_complete: bool = False) -> tuple[list[Document], list[Chunk]]:
    from retrieval.docx_sections import parse_sections

    paths: dict[str, Path] = {}
    for path in RAW_DIR.glob("*"):
        if path.suffix.lower() != ".docx" or path.name.startswith("~$") or not path.is_file():
            continue
        canonical_id = path.stem.lower()
        doc_id = canonical_id if canonical_id in DOC_META else path.stem
        if doc_id in paths:
            raise ValueError(f"Повторяющийся doc_id {doc_id} у файлов {paths[doc_id].name} и {path.name}.")
        paths[doc_id] = path

    if require_complete:
        missing = [f"{doc_id}.docx" for doc_id in sorted(DOC_META.keys() - paths.keys())]
        if missing:
            raise ValueError(f"Не найдены DOCX в {RAW_DIR}: {', '.join(missing)}")

    docs, chunks = [], []
    for doc_id, path in sorted(paths.items()):
        meta = DOC_META.get(doc_id, {})
        try:
            title, sections = parse_sections(path)
        except (BadZipFile, KeyError, ParseError, ValueError) as error:
            raise ValueError(f"Не удалось прочитать DOCX {path.name}. {error}") from error
        source = path.resolve()
        if source.is_relative_to(ROOT):
            source = source.relative_to(ROOT)
        docs.append(Document(doc_id=doc_id, title=title, source_path=source.as_posix(),
                             **{k: meta.get(k) for k in ("directorate", "stage")},
                             doc_type=meta.get("doc_type", "регламент")))
        seen: Counter = Counter()
        for s in sections:
            seen[s.section] += 1
            cid = f"{doc_id}::{s.section}" + (f"#{seen[s.section]}" if seen[s.section] > 1 else "")
            chunks.append(Chunk(
                chunk_id=cid, doc_id=doc_id, doc_title=title, section=s.section, text=s.text,
                section_title=s.title, heading_path=s.heading_path,
                directorate=meta.get("directorate"), stage=meta.get("stage"),
            ))
    return docs, chunks


def _qa_chunks() -> list[Chunk]:
    if not QA_PATH.exists():
        return []
    out = []
    for line in QA_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("status") != "confirmed":
            continue
        out.append(Chunk(
            chunk_id=f"qa::{e['qa_entry_id']}", source_type="qa_entry", qa_entry_id=e["qa_entry_id"],
            doc_id=e.get("doc_id") or "qa", doc_title=e.get("doc_title") or "Служба качества",
            section=e.get("section") or "—", section_title="Подтверждено службой качества",
            text=f"Вопрос: {e['question']}\nОтвет службы качества: {e['answer']}",
            heading_path=["Служба качества", f"обращение {e.get('ticket_id', '')}"],
            directorate=e.get("directorate"), stage=e.get("stage"), status="active",
        ))
    return out


class _Index:
    """BM25 (k1=1.5, b=0.75) по тексту пункта + заголовкам + расшифровкам сокращений."""

    def __init__(self, docs: list[Document], chunks: list[Chunk]):
        self.docs = {d.doc_id: d for d in docs}
        self.chunks = chunks
        self.tf: list[Counter] = []
        self.len: list[int] = []
        df: Counter = Counter()
        self.dirs_by_token: dict[str, set[str]] = {}
        for c in chunks:
            body = " ".join([*c.heading_path[-1:], c.section_title or "", c.text])
            toks = tokenize(expand(body))
            tf = Counter(toks)
            self.tf.append(tf)
            self.len.append(len(toks))
            df.update(tf.keys())
            if c.source_type == "regulation" and c.directorate:
                for tok in tf:
                    self.dirs_by_token.setdefault(tok, set()).add(c.directorate)
        n = max(len(chunks), 1)
        self.avg = sum(self.len) / n if self.len else 1.0
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.df = df

    def score(self, q: list[str], i: int, k1: float = 1.5, b: float = 0.75) -> float:
        tf, ln, s = self.tf[i], self.len[i], 0.0
        for t in q:
            f = tf.get(t)
            if f:
                s += self.idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * ln / self.avg))
        return s

    def max_score(self, q: list[str], k1: float = 1.5) -> float:
        return sum(self.idf.get(t, 0.0) * (k1 + 1) for t in set(q)) or 1.0


_INDEX: _Index | None = None


def _index() -> _Index:
    global _INDEX
    if _INDEX is None:
        jsonl = PROCESSED_DIR / "chunks.jsonl"
        if jsonl.exists():
            chunks = _chunks_from_jsonl(jsonl)
            docs_path = PROCESSED_DIR / "documents.jsonl"
            docs = ([Document(**{k: v for k, v in json.loads(l).items() if k in Document.__dataclass_fields__})
                     for l in docs_path.read_text(encoding="utf-8").splitlines() if l.strip()]
                    if docs_path.exists() else [])
        else:
            docs, chunks = load_docx_corpus()
        _INDEX = _Index(docs, chunks + _qa_chunks())
    return _INDEX


def reload() -> None:
    """Перечитать корпус и записи качества (после ingest или подтверждения)."""
    global _INDEX
    _INDEX = None


# ---------------------------------------------------------------- API (контракт §2)

# Пороги подсказки «ответа, скорее всего, нет». Подобраны на eval/datasets/control.jsonl:
# лексические признаки отделяют только явно посторонние вопросы (дресс-код, отпуск, лицензии
# при реорганизации), а «похожие, но не про это» — нет. Окончательно решает агент (grade моделью).
LOW_SCORE = float(os.environ.get("LOW_SCORE_THRESHOLD_BM25", "0.25"))
LOW_COVERAGE = float(os.environ.get("LOW_COVERAGE_THRESHOLD", "0.40"))
# Неоднозначность области: среди top-AMBIG_TOP есть пункты ≥2 областей (дирекция+этап)
# с баллом не ниже AMBIG_RATIO от лучшего — и при этом в вопросе дирекция не названа.
AMBIG_TOP = int(os.environ.get("AMBIG_TOP", "8"))
AMBIG_RATIO = float(os.environ.get("AMBIG_RATIO", "0.5"))
# Термин, который встречается только в пунктах одной дирекции (TPS, «квалификационная анкета»),
# сам задаёт область: уточнять не нужно. Нужен устойчивый термин (≥ UNIQUE_MIN_DF пунктов),
# а не случайное слово, которое один раз встретилось в одном документе («подать»).
UNIQUE_MIN_DF = int(os.environ.get("UNIQUE_MIN_DF", "2"))
# Подтверждённые записи службы качества важнее пункта регламента (D-009, ответ организаторов 27.11).
QA_BOOST = float(os.environ.get("QA_BOOST", "1.3"))
QA_MIN_COVERAGE = float(os.environ.get("QA_MIN_COVERAGE", "0.45"))


def _scope_label(c: "Chunk") -> str:
    parts = [p for p in (c.directorate, c.stage) if p]
    return ", ".join(parts) or c.doc_title


def _implied_scope(idx: "_Index", q: list[str]) -> set[str]:
    """Дирекции, которые однозначно задаются редкими словами вопроса."""
    found: set[str] = set()
    for tok in q:
        dirs = idx.dirs_by_token.get(tok)
        if dirs and len(dirs) == 1 and idx.df.get(tok, 0) >= UNIQUE_MIN_DF:
            found |= dirs
    return found


def _ambiguity(hits: list["Hit"], scope: set[str], restricted_query: bool) -> tuple[bool, list[dict]]:
    if scope or restricted_query or not hits:
        return False, []
    top = hits[0].score or 0.0
    best: dict[tuple, Hit] = {}
    for h in hits[:AMBIG_TOP]:
        c = h.chunk
        if c.source_type != "regulation" or not c.directorate:
            continue
        if top and h.score < AMBIG_RATIO * top:
            continue
        key = (c.directorate, c.stage)
        if key not in best:
            best[key] = h
    if len(best) < 2:
        return False, []
    opts = [{"directorate": k[0], "stage": k[1], "label": _scope_label(h.chunk), "doc_id": h.chunk.doc_id,
             "doc_title": h.chunk.doc_title, "section": h.chunk.section, "score": h.score}
            for k, h in best.items()]
    return True, opts


def _coverage(idx: "_Index", q: list[str], i: int) -> float:
    total = sum(idx.idf.get(t, 0.0) for t in q)
    got = sum(idx.idf.get(t, 0.0) for t in q if t in idx.tf[i])
    return round(got / total, 3) if total else 0.0

def search(
    query: str,
    k: int = 8,
    allowed_access: list[str] | None = None,
    statuses: tuple[str, ...] | list[str] = DEFAULT_STATUSES,
    doc_ids: list[str] | None = None,
    directorate: str | None = None,
    stage: str | None = None,
    include_qa: bool = True,
) -> SearchResult:
    idx = _index()
    scope, stripped = detect_directorates(query)
    eq = expand(stripped, skip=_DIRECTORATE_CANON)
    q = list(dict.fromkeys(tokenize(eq)))  # без повторов, порядок сохраняем
    norm = idx.max_score(q)
    scored: list[tuple[float, int]] = []
    restricted: dict[str, str] = {}
    for i, c in enumerate(idx.chunks):
        if c.source_type == "qa_entry" and not include_qa:
            continue
        if c.status not in statuses:
            continue
        if doc_ids and c.doc_id not in doc_ids:
            continue
        if directorate and c.directorate and c.directorate != directorate:
            continue
        if stage and c.stage and c.stage != stage:
            continue
        s = idx.score(q, i)
        if s <= 0:
            continue
        if "." not in c.section and c.source_type == "regulation" and "\n" not in c.text:
            s *= 0.5  # пункт-заголовок раздела («2. Назначение РП») — сам по себе не ответ
        if scope and c.directorate:
            s *= 1.5 if c.directorate in scope else 0.6
        if c.source_type == "qa_entry":
            # запись качества — только если вопрос действительно про неё: иначе она «липнет» к соседним вопросам
            if _coverage(idx, q, i) < QA_MIN_COVERAGE:
                continue
            s *= QA_BOOST
        if allowed_access is not None and c.access_level not in allowed_access:
            restricted[c.doc_id] = c.doc_title
            continue
        scored.append((s, i))
    scored.sort(key=lambda x: -x[0])
    mode = "bm25"
    order = [i for _, i in scored]
    bm25 = {i: s for s, i in scored}
    dense_scores: dict[int, float] = {}
    if _hybrid_enabled():
        try:
            from retrieval import dense
            order, dense_scores, mode = dense.hybrid_rank(query, idx.chunks, order, k=k,
                                                          allowed=lambda i: _passes(idx.chunks[i], statuses, doc_ids,
                                                                                    directorate, stage, include_qa,
                                                                                    allowed_access))
        except Exception as e:  # нет пакетов/модели — тихо остаёмся на BM25
            _warn_once(f"гибридный поиск недоступен, работаю на BM25: {e}")
    hits = [Hit(chunk=idx.chunks[i], score=round(bm25.get(i, 0.0) / norm, 4), bm25_score=round(bm25.get(i, 0.0), 3),
                dense_score=dense_scores.get(i)) for i in order[:k]]
    for h in hits:
        h.parents = get_parents(h.chunk)
    if mode != "bm25" and hits and hits[0].dense_score is not None:
        for h in hits:  # в гибриде финальный score — от реранкера/dense
            h.score = round(h.dense_score, 4)
    top_i = order[0] if order else None
    cov = _coverage(idx, q, top_i) if top_i is not None else 0.0
    top = hits[0].score if hits else None
    amb, opts = _ambiguity(hits, scope | _implied_scope(idx, q), bool(doc_ids or directorate))
    return SearchResult(
        hits=hits,
        restricted=[{"doc_id": d, "doc_title": t} for d, t in restricted.items()],
        top_score=top,
        expanded_query=eq,
        coverage=cov,
        low_confidence=(not hits) or (mode == "bm25" and (top < LOW_SCORE or cov < LOW_COVERAGE)),
        scope=sorted(scope),
        mode=mode,
        ambiguous_scope=amb,
        scope_options=opts,
    )


def _passes(c: Chunk, statuses, doc_ids, directorate, stage, include_qa, allowed_access) -> bool:
    if c.source_type == "qa_entry" and not include_qa:
        return False
    if c.status not in statuses or (doc_ids and c.doc_id not in doc_ids):
        return False
    if directorate and c.directorate and c.directorate != directorate:
        return False
    if stage and c.stage and c.stage != stage:
        return False
    return allowed_access is None or c.access_level in allowed_access


def _hybrid_enabled() -> bool:
    return os.environ.get("RETRIEVAL_MODE", "auto") != "bm25"


_WARNED: set[str] = set()


def _warn_once(msg: str) -> None:
    if msg not in _WARNED:
        _WARNED.add(msg)
        import sys
        print(f"[retrieval] {msg}", file=sys.stderr)


def get_document(doc_id: str) -> Document | None:
    return _index().docs.get(doc_id)


def get_versions(doc_id: str) -> list[Document]:
    d = get_document(doc_id)
    return [d] if d else []  # в корпусе одна редакция на документ


def get_parents(c: Chunk) -> list[dict]:
    """Родительские пункты вложенного пункта регламента: «5.1.3.1» → [5.1.3, 5.1]."""
    if c.source_type != "regulation":
        return []
    parts = c.section.split(".")
    out = []
    for n in range(len(parts) - 1, 1, -1):
        p = get_section(c.doc_id, ".".join(parts[:n]))
        if p:
            out.append({"section": p.section, "section_title": p.section_title, "text": p.text})
    return out


def get_section(doc_id: str, section: str) -> Chunk | None:
    for c in _index().chunks:
        if c.doc_id == doc_id and c.section == section and c.source_type == "regulation":
            return c
    return None


def list_documents(allowed_access: list[str] | None = None) -> list[Document]:
    return [d for d in _index().docs.values() if allowed_access is None or d.access_level in allowed_access]


def upsert_qa_entry(entry: dict) -> None:
    """Публикует подтверждённую запись службы качества (контракт §4, QAEntry). Черновики игнорируются."""
    if entry.get("status") != "confirmed":
        return
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    if QA_PATH.exists():
        rows = [json.loads(l) for l in QA_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("qa_entry_id") != entry["qa_entry_id"]] + [entry]
    QA_PATH.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    reload()


if __name__ == "__main__":
    import sys

    res = search(" ".join(sys.argv[1:]) or "Кто назначает РП на проект ИБ?", k=5)
    for h in res.hits:
        print(f"{h.score:.3f}  {h.chunk.chunk_id:<28} {h.chunk.text[:110]!r}")
