"""Разбор DOCX на пункты для выгрузки и запуска поиска напрямую из регламентов.

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


_NumberingLevel = tuple[str, str, int]


def _read_level(level: ET.Element) -> _NumberingLevel:
    fmt = level.find(W + "numFmt")
    text = level.find(W + "lvlText")
    start = level.find(W + "start")
    return (
        fmt.get(W + "val", "decimal") if fmt is not None else "decimal",
        text.get(W + "val", "") if text is not None else "",
        int(start.get(W + "val", "1")) if start is not None else 1,
    )


def _numbering(z: zipfile.ZipFile) -> dict[str, dict[int, _NumberingLevel]]:
    """Вернёт уровни нумерации каждого списка Word с учётом его переопределений."""
    try:
        root = ET.fromstring(z.read("word/numbering.xml"))
    except KeyError:
        return {}
    abstracts = {
        abstract.get(W + "abstractNumId"): {
            int(level.get(W + "ilvl", "0")): _read_level(level)
            for level in abstract.findall(W + "lvl")
        }
        for abstract in root.findall(W + "abstractNum")
    }
    numbering: dict[str, dict[int, _NumberingLevel]] = {}
    for instance in root.findall(W + "num"):
        num_id = instance.get(W + "numId")
        reference = instance.find(W + "abstractNumId")
        if not num_id or reference is None:
            continue
        inherited = abstracts.get(reference.get(W + "val"))
        if inherited is None:
            continue
        levels = inherited.copy()
        for override in instance.findall(W + "lvlOverride"):
            index = int(override.get(W + "ilvl", "0"))
            level = override.find(W + "lvl")
            if level is not None:
                levels[index] = _read_level(level)
            start = override.find(W + "startOverride")
            if start is not None:
                fmt, template, _ = levels.get(index, ("decimal", f"%{index + 1}.", 1))
                levels[index] = (fmt, template, int(start.get(W + "val", "1")))
        numbering[num_id] = levels
    return numbering


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
        numbering = _numbering(z)
    if body is None:
        raise ValueError("В DOCX отсутствует тело документа.")
    counters: dict[str, dict[int, int]] = {}
    out: list[str] = []
    for el in body:
        if el.tag == W + "p":
            t = _text_of(el).strip()
            if not t:
                continue
            np_ = el.find(f"{W}pPr/{W}numPr")
            if np_ is not None and np_.find(W + "numId") is not None:
                nid = np_.find(W + "numId").get(W + "val", "")
                il = np_.find(W + "ilvl")
                ilvl = int(il.get(W + "val")) if il is not None else 0
                lv = numbering.get(nid)
                if lv is not None:
                    c = counters.setdefault(nid, {})
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
                cells = []
                for cell in tr.findall(W + "tc"):
                    paragraphs = [_text_of(paragraph).strip() for paragraph in cell.iter(W + "p")]
                    cells.append(" ".join(paragraph for paragraph in paragraphs if paragraph).replace("\n", " "))
                if any(cells):
                    out.append(f"| {' | '.join(cells)} |")
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
    if _NUM_RE.match(blocks[0]):
        doc_title, body = path.stem, blocks
    else:
        doc_title, body = blocks[0], blocks[1:]
    sections: list[Section] = []
    cur: Section | None = None
    top = 0
    top_title = ""
    last_nested_item: int | None = None
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
            major = parts[0]
            has_subsections = nxt is not None and nxt.startswith(f"{major}.")
            is_flat_section = (nxt is None or nxt == str(major + 1)) and last_nested_item != major - 1
            if len(parts) == 1 and major == top + 1 and (has_subsections or is_flat_section):
                top = major
                top_title = f"{num}. {rest}"
                cur = Section(num, rest, [doc_title, top_title], [f"{num}. {rest}"])
                sections.append(cur)
                accepted = True
            elif len(parts) >= 2 and major > 0 and major in (top, top + 1):
                if major == top + 1:  # раздел без отдельного заголовка
                    top = major
                    top_title = f"{top}."
                title = rest.split(".")[0][:120] if rest else None
                cur = Section(num, title, [doc_title, top_title], [f"{num}. {rest}"])
                sections.append(cur)
                accepted = True
        if accepted:
            last_nested_item = None
            continue
        if cur is None:
            cur = Section("0", "Вводная часть", [doc_title], [])
            sections.append(cur)
        if m and len(parts) == 1 and "." in cur.section:
            last_nested_item = major
        cur.lines.append(_clean_bullet(raw))
    return doc_title, sections
