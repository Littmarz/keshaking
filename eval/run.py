"""Прогон контрольного набора (eval/datasets/control.jsonl) — только стандартная библиотека.

    python -m eval.run                       # поиск (retrieval.mock), отчёт в eval/reports/
    python -m eval.run --target agent        # агент, когда появится agent.run.run_agent
    python -m eval.run --k 5 --only c04,c13  # отдельные сценарии

Поиск (`--target retrieval`):
- многоходовой сценарий склеивается в один запрос (так агент сформулирует вопрос после уточнения);
- hit@k — хоть один ожидаемый пункт в топ-k; recall@k — доля найденных ожидаемых пунктов;
- MRR — 1 / позиция первого ожидаемого;
- leak — в топ-k попал источник из must_not_cite (чужая дирекция);
- сценарии без expected_sources (эскалация, конфликт) не входят в средние, по ним печатаем top_score
  — по нему подбираем порог «ответа нет».
Совпадение пункта: ожидаемый «2.2» засчитывается и для «2.2.1» (вложенный пункт).

Агент (`--target agent`): статусы по ходам, must_contain в ответе, источники в citations, must_not_cite.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "eval" / "datasets" / "control.jsonl"
REPORTS = ROOT / "eval" / "reports"


def load(path: Path, only: set[str] | None) -> list[dict]:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if not only or r["id"] in only]


def _match(exp: dict, doc_id: str, section: str, source_type: str = "regulation") -> bool:
    if "source_type" in exp:
        return exp["source_type"] == source_type
    if exp["doc_id"] != doc_id:
        return False
    s = exp.get("section")
    return s is None or section == s or section.startswith(s + ".")


# Для каких категорий ждём подсказку ambiguous_scope на ПЕРВОМ ходе (сценарий 2 — уточнение)
AMBIG_YES = {"clarify"}
AMBIG_NO = {"single", "synonym", "boundary"}


def run_retrieval(cases: list[dict], k: int) -> tuple[list[dict], dict]:
    import tempfile
    from retrieval import mock

    # Сценарий 3 гоняем на временном хранилище записей качества — боевое не трогаем.
    tmp = tempfile.TemporaryDirectory()
    real_qa = mock.QA_PATH
    mock.QA_PATH = Path(tmp.name) / "qa_entries.jsonl"
    mock.reload()
    rows = []
    try:
        for c in cases:
            query = " ".join(c["turns"])
            t0 = time.perf_counter()
            res = mock.search(query, k=k)
            ms = (time.perf_counter() - t0) * 1000
            got = [(h.chunk.doc_id, h.chunk.section, h.chunk.source_type) for h in res.hits]
            exp = c.get("expected_sources", [])
            found, first = [], None
            for e in exp:
                pos = next((i for i, g in enumerate(got) if _match(e, *g)), None)
                if pos is not None:
                    found.append(e)
                    first = pos if first is None else min(first, pos)
            leak = [f"{d}::{s}" for d, s, st in got if any(_match(n, d, s, st) for n in c.get("must_not_cite", []))]
            # подсказка для уточнения — по ПЕРВОМУ ходу диалога
            first_turn = mock.search(c["turns"][0], k=k) if len(c["turns"]) > 1 else res
            amb_exp = True if c["category"] in AMBIG_YES else (False if c["category"] in AMBIG_NO else None)
            lc_exp = c.get("expect_low_confidence")
            row = {
                "id": c["id"], "category": c["category"], "query": query, "expected": exp,
                "scored": bool(exp), "hit": bool(found), "recall": len(found) / len(exp) if exp else None,
                "rr": 1 / (first + 1) if first is not None else 0.0, "leak": leak,
                "top": [f"{d}::{s}" if st == "regulation" else f"qa::{d}" for d, s, st in got],
                "top_score": res.top_score, "ms": round(ms, 1),
                "amb_exp": amb_exp, "amb_got": first_turn.ambiguous_scope,
                "amb_opts": [o["label"] for o in first_turn.scope_options],
                "lc_exp": lc_exp, "lc_got": res.low_confidence, "qa_note": "",
            }
            if c.get("qa_confirm"):  # сценарий 3: до подтверждения записи в выдаче быть не должно
                leaked = any(st == "qa_entry" for _, _, st in got)
                mock.upsert_qa_entry(c["qa_confirm"])
                row["qa_note"] = ("❌ запись качества найдена ДО подтверждения" if leaked
                                  else "✅ до подтверждения записи нет → опубликована")
            rows.append(row)
    finally:
        mock.QA_PATH = real_qa
        mock.reload()
        tmp.cleanup()
    scored = [r for r in rows if r["scored"]]
    n = len(scored) or 1
    amb = [r for r in rows if r["amb_exp"] is not None]
    lc = [r for r in rows if r["lc_exp"] is not None]
    summary = {
        "target": "retrieval", "k": k, "cases": len(rows), "scored": len(scored),
        f"hit@{k}": round(sum(r["hit"] for r in scored) / n, 3),
        f"recall@{k}": round(sum(r["recall"] for r in scored) / n, 3),
        "mrr": round(sum(r["rr"] for r in scored) / n, 3),
        "leaks": sum(bool(r["leak"]) for r in rows),
        "clarify_hint_acc": round(sum(r["amb_got"] == r["amb_exp"] for r in amb) / (len(amb) or 1), 3),
        "clarify_hint_n": len(amb),
        "low_conf_acc": round(sum(r["lc_got"] == r["lc_exp"] for r in lc) / (len(lc) or 1), 3),
        "qa_loop": "ok" if all("❌" not in r["qa_note"] for r in rows) and any(r["qa_note"] for r in rows) else "fail",
        "p50_ms": sorted(r["ms"] for r in rows)[len(rows) // 2] if rows else 0,
    }
    return rows, summary


def run_agent(cases: list[dict], k: int) -> tuple[list[dict], dict]:
    try:
        agent = importlib.import_module("agent.run")
    except ImportError as e:
        sys.exit(f"agent.run ещё нет ({e}). Запусти с --target retrieval.")
    user = {"user_id": "eval", "name": "Eval", "department": "QA", "role": "employee", "allowed_access": ["all"]}
    rows = []
    for c in cases:
        history: list[dict] = []
        statuses, last, t0 = [], None, time.perf_counter()
        for turn in c["turns"]:
            res = agent.run_agent(turn, user, history)
            r = res.to_dict() if hasattr(res, "to_dict") else (res.model_dump() if hasattr(res, "model_dump") else res)
            statuses.append(r["status"])
            history += [{"role": "user", "content": turn}, {"role": "assistant", "content": r["answer"], "status": r["status"]}]
            last = r
        ms = (time.perf_counter() - t0) * 1000
        exp_st = c["expected_status"]
        cited = [(x["doc_id"], x["section"]) for x in last.get("citations", [])]
        missing = [m for m in c.get("must_contain", []) if m.lower() not in last["answer"].lower()]
        src_ok = all(any(_match(e, d, s) for d, s in cited) for e in c.get("expected_sources", [])) if last["status"] == "answered" else True
        leak = [f"{d}::{s}" for d, s in cited if any(_match(n, d, s) for n in c.get("must_not_cite", []))]
        rows.append({
            "id": c["id"], "category": c["category"], "query": " → ".join(c["turns"]),
            "status_ok": statuses == exp_st, "statuses": statuses, "expected_status": exp_st,
            "facts_ok": not missing, "missing": missing, "sources_ok": src_ok, "leak": leak,
            "ms": round(ms), "answer": last["answer"][:300],
        })
    n = len(rows) or 1
    summary = {
        "target": "agent", "cases": len(rows),
        "status_ok": round(sum(r["status_ok"] for r in rows) / n, 3),
        "facts_ok": round(sum(r["facts_ok"] for r in rows) / n, 3),
        "sources_ok": round(sum(r["sources_ok"] for r in rows) / n, 3),
        "leaks": sum(bool(r["leak"]) for r in rows),
        "p50_ms": sorted(r["ms"] for r in rows)[len(rows) // 2] if rows else 0,
    }
    return rows, summary


def report_md(rows: list[dict], summary: dict) -> str:
    out = [f"# Прогон контрольного набора — {summary['target']}", "",
           f"_{dt.datetime.now():%Y-%m-%d %H:%M}_", "", "| Метрика | Значение |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in summary.items()]
    out += [""]
    if summary["target"] == "retrieval":
        out += ["| id | кат. | hit | recall | RR | top_score | уточнение (1-й ход) | нет ответа? | утечка | топ |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            mark = ("✅" if r["hit"] else "❌") if r["scored"] else "—"
            rec = f"{r['recall']:.2f}" if r["recall"] is not None else "—"
            amb = "—" if r["amb_exp"] is None else ("✅" if r["amb_got"] == r["amb_exp"] else "❌")
            amb += f" {'/'.join(r['amb_opts'])}" if r["amb_got"] else ""
            lc = "—" if r["lc_exp"] is None else ("✅" if r["lc_got"] == r["lc_exp"] else "❌")
            out.append(f"| {r['id']} | {r['category']} | {mark} | {rec} | {r['rr']:.2f} | {r['top_score']} | {amb} | {lc} | "
                       f"{', '.join(r['leak']) or ''} | {', '.join(r['top'][:5])} |")
        notes = [f"- **{r['id']}**: {r['qa_note']}" for r in rows if r["qa_note"]]
        if notes:
            out += ["", "## Сценарий 3 — пополнение базы через службу качества", ""] + notes
        bad = [r for r in rows if r["scored"] and (not r["hit"] or r["leak"])]
        if bad:
            out += ["", "## Ошибки", ""]
            for r in bad:
                exp = ", ".join(f"{e['doc_id']}::{e.get('section')}" for e in r["expected"])
                out.append(f"- **{r['id']}** «{r['query']}» — ждали {exp}; получили {', '.join(r['top'][:5])}"
                           + (f"; утечка {', '.join(r['leak'])}" if r["leak"] else ""))
    else:
        out += ["| id | кат. | статусы | факты | источники | утечка | мс |", "|---|---|---|---|---|---|---|"]
        for r in rows:
            out.append(f"| {r['id']} | {r['category']} | {'✅' if r['status_ok'] else '❌'} {r['statuses']} | "
                       f"{'✅' if r['facts_ok'] else '❌ ' + ', '.join(r['missing'])} | {'✅' if r['sources_ok'] else '❌'} | "
                       f"{', '.join(r['leak'])} | {r['ms']} |")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", choices=["retrieval", "agent"], default="retrieval")
    ap.add_argument("--dataset", type=Path, default=DATASET)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--only", default="")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    cases = load(a.dataset, set(a.only.split(",")) if a.only else None)
    rows, summary = (run_retrieval if a.target == "retrieval" else run_agent)(cases, a.k)
    md = report_md(rows, summary)
    sys.stdout.reconfigure(encoding="utf-8")
    print(md)
    if not a.no_save:
        REPORTS.mkdir(parents=True, exist_ok=True)
        stamp = f"{dt.datetime.now():%Y-%m-%d_%H%M}"
        (REPORTS / f"{stamp}_{a.target}.md").write_text(md, encoding="utf-8")
        with (REPORTS / "progress.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": stamp, **summary}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
