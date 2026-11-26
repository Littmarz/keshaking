"""Временный разбор DOCX на пункты для заглушки поиска (только стандартная библиотека).

Это НЕ основной ingest (зона разработчика 2). Нужен, чтобы агент и eval работали
до появления data/processed/chunks.jsonl. Когда chunks.jsonl появится, retrieval.mock
читает его, а этот модуль не используется.

Правила разбора (проверены на 4 DOCX заказчика):
- номера пунктов в документах — обычный текст («2.3. …»), не автонумерация Word;
- номер раздела растёт монотонно: «N.» принимается как новый раздел только если N = текущий + 1;
- «X.Y…» принимается, если X — текущий раздел (или следующий);
- всё остальное («1.», «a.», буллеты) — продолжение текущего пункта
  (так в п. 1.3 пресейла ИБ вложенные «1. 2. 3. / a. b.» остаются внутри 1.3).
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_NUM_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.\s+(.*)$", re.S)
_BULLET_CHARS = "•●▪–-"


@dataclass
class Section:
    section: str
    title: str | None
    heading_path: list[str]
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def _text_of(el: ET.Element) -> str:
    parts: list[str] = []
    for node in el.iter():
        if node.tag == W + "t" and node.text:
            parts.append(node.text)
        elif node.tag == W + "tab":
            parts.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            parts.append("\n")
    return "".join(parts)


def _numbering(z: zipfile.ZipFile) -> tuple[dict, dict]:
    """abstractNumId -> {ilvl: (numFmt, lvlText, start)}; numId -> abstractNumId."""
    try:
        root = ET.fromstring(z.read("word/numbering.xml"))
    except KeyError:
        return {}, {}
    absd: dict = {}
    for a in root.findall(W + "abstractNum"):
        lv = {}
        for lvl in a.findall(W + "lvl"):
            fmt, txt, st = lvl.find(W + "numFmt"), lvl.find(W + "lvlText"), lvl.find(W + "start")
            lv[int(lvl.get(W + "ilvl"))] = (
                fmt.get(W + "val") if fmt is not None else "decimal",
                txt.get(W + "val") if txt is not None else "",
                int(st.get(W + "val")) if st is not None else 1,
            )
        absd[a.get(W + "abstractNumId")] = lv
    nums = {n.get(W + "numId"): n.find(W + "abstractNumId").get(W + "val") for n in root.findall(W + "num")}
    return absd, nums


def _fmt(f: str, n: int) -> str:
    if f == "bullet":
        return "—"
    if f == "lowerLetter":
        return "abcdefghijklmnopqrstuvwxyz"[(n - 1) % 26]
    if f == "upperLetter":
        return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[(n - 1) % 26]
    return str(n)


def read_blocks(path: Path) -> list[str]:
    """Абзацы (с автонумерацией Word, если она есть) и строки таблиц по порядку."""
    with zipfile.ZipFile(path) as z:
        body = ET.fromstring(z.read("word/document.xml")).find(W + "body")
        absd, nums = _numbering(z)
    counters: dict[str, dict[int, int]] = {}
    out: list[str] = []
    for el in body:
        if el.tag == W + "p":
            t = _text_of(el).strip()
            if not t:
                continue
            np_ = el.find(f"{W}pPr/{W}numPr")
            if np_ is not None and np_.find(W + "numId") is not None:
                nid = np_.find(W + "numId").get(W + "val")
                il = np_.find(W + "ilvl")
                ilvl = int(il.get(W + "val")) if il is not None else 0
                aid = nums.get(nid)
                if aid is not None and aid in absd:
                    lv = absd[aid]
                    c = counters.setdefault(aid, {})
                    c[ilvl] = c.get(ilvl, lv.get(ilvl, ("", "", 1))[2] - 1) + 1
                    for k in [k for k in c if k > ilvl]:
                        del c[k]
                    f, tmpl, _ = lv.get(ilvl, ("decimal", "%1.", 1))
                    label = re.sub(
                        r"%(\d)",
                        lambda m: _fmt(lv.get(int(m.group(1)) - 1, (f,))[0], c.get(int(m.group(1)) - 1, 1)),
                        tmpl,
                    ).strip()
                    if label and label != "—":
                        t = f"{label} {t}"
                    elif label == "—":
                        t = f"— {t}"
            out.append(t)
        elif el.tag == W + "tbl":
            for tr in el.findall(W + "tr"):
                cells = [_text_of(tc).strip().replace("\n", " ") for tc in tr.findall(W + "tc")]
                cells = [c for c in cells if c]
                if cells:
                    out.append(" | ".join(cells))
    return out


def _clean_bullet(s: str) -> str:
    s = s.lstrip()
    if s and s[0] in _BULLET_CHARS:
        return "— " + s[1:].strip()
    return s


def parse_sections(path: Path) -> tuple[str, list[Section]]:
    """Вернёт (заголовок документа, пункты). Пункт-заголовок раздела («2. Назначение РП») тоже Section."""
    blocks = read_blocks(path)
    if not blocks:
        return path.stem, []
    doc_title = blocks[0]
    sections: list[Section] = []
    cur: Section | None = None
    top = 0
    top_title = ""
    body = blocks[1:]
    nums = [(_NUM_RE.match(b).group(1) if _NUM_RE.match(b) else None) for b in body]

    def next_num(i: int) -> str | None:
        for n in nums[i + 1:]:
            if n is not None:
                return n
        return None

    for i, raw in enumerate(body):
        m = _NUM_RE.match(raw)
        accepted = False
        if m:
            num, rest = m.group(1), m.group(2).strip()
            parts = [int(p) for p in num.split(".")]
            nxt = next_num(i)
            # «N.» — новый раздел, только если следом идёт «N.1.» (иначе это вложенный список)
            is_heading = nxt is None or nxt.startswith(f"{parts[0]}.")
            if len(parts) == 1 and parts[0] == top + 1 and is_heading:
                top = parts[0]
                top_title = f"{num}. {rest}"
                cur = Section(num, rest, [doc_title, top_title], [f"{num}. {rest}"])
                sections.append(cur)
                accepted = True
            elif len(parts) >= 2 and parts[0] in (top, top + 1) and top > 0:
                if parts[0] == top + 1:  # раздел без отдельного заголовка
                    top = parts[0]
                    top_title = f"{top}."
                title = rest.split(".")[0][:120] if rest else None
                cur = Section(num, title, [doc_title, top_title], [f"{num}. {rest}"])
                sections.append(cur)
                accepted = True
        if not accepted:
            if cur is None:
                cur = Section("0", "Вводная часть", [doc_title], [])
                sections.append(cur)
            cur.lines.append(_clean_bullet(raw))
    return doc_title, sections
