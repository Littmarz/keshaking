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
import os
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


def _judge(c: dict, statuses: list[str], last: dict, ms: float) -> dict:
    """Общая проверка ответа агента (и для --target agent, и для --target api)."""
    exp_st = c["expected_status"]
    cites = last.get("citations") or []
    cited = [(x.get("doc_id"), x.get("section"), x.get("source_type", "regulation")) for x in cites]
    answer = last.get("answer") or ""
    normalized = answer.casefold()
    missing = [m for m in c.get("must_contain", []) if m.casefold() not in normalized]
    missing.extend(
        " / ".join(variants) for variants in c.get("must_contain_any", [])
        if not any(variant.casefold() in normalized for variant in variants)
    )
    src_ok = all(any(_match(e, *g) for g in cited) for e in c.get("expected_sources", []))
    if last.get("status") == "conflict":
        src_ok = src_ok and len(set(cited)) >= 2
    leak = [f"{d}::{s}" for d, s, st in cited if any(_match(n, d, s, st) for n in c.get("must_not_cite", []))]
    tok = _tokens(last)
    return {
        "id": c["id"], "category": c["category"], "query": " → ".join(c["turns"]),
        "status_ok": statuses == exp_st, "statuses": statuses, "expected_status": exp_st,
        "facts_ok": not missing, "missing": missing, "sources_ok": src_ok, "leak": leak,
        "ms": round(ms), "answer": answer[:300], **tok,
    }


def _tokens(result: dict) -> dict:
    """Токены запроса: из metrics (контракт) или суммой по steps[].detail (так их отдаёт агент Влада)."""
    m = result.get("metrics") or {}
    p, c, calls = m.get("prompt_tokens"), m.get("completion_tokens"), m.get("llm_calls")
    if p is None:
        details = [s.get("detail") or {} for s in result.get("steps") or []]
        pt = [d.get("prompt_tokens") for d in details if d.get("prompt_tokens") is not None]
        ct = [d.get("completion_tokens") for d in details if d.get("completion_tokens") is not None]
        p, c, calls = (sum(pt) if pt else None), (sum(ct) if ct else None), (len(pt) or None)
    return {"prompt_tokens": p, "completion_tokens": c, "llm_calls": calls}


def _agent_summary(rows: list[dict], target: str) -> dict:
    n = len(rows) or 1
    with_tok = [r for r in rows if r.get("prompt_tokens") is not None]
    return {
        "target": target, "cases": len(rows),
        "status_ok": round(sum(r["status_ok"] for r in rows) / n, 3),
        "facts_ok": round(sum(r["facts_ok"] for r in rows) / n, 3),
        "sources_ok": round(sum(r["sources_ok"] for r in rows) / n, 3),
        "leaks": sum(bool(r["leak"]) for r in rows),
        "p50_ms": sorted(r["ms"] for r in rows)[len(rows) // 2] if rows else 0,
        "p95_ms": sorted(r["ms"] for r in rows)[min(len(rows) - 1, int(len(rows) * 0.95))] if rows else 0,
        "avg_prompt_tokens": round(sum(r["prompt_tokens"] for r in with_tok) / len(with_tok)) if with_tok else None,
        "avg_completion_tokens": round(sum(r["completion_tokens"] or 0 for r in with_tok) / len(with_tok)) if with_tok else None,
    }


def run_agent(cases: list[dict], k: int) -> tuple[list[dict], dict]:
    try:
        agent = importlib.import_module("agent.run")
    except ImportError as e:
        sys.exit(f"agent.run ещё нет ({e}). Запусти с --target retrieval или --target api.")
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
        rows.append(_judge(c, statuses, last, (time.perf_counter() - t0) * 1000))
    return rows, _agent_summary(rows, "agent")


class _Api:
    """Минимальный клиент HTTP API «Пункта» (backend/app/main.py) на стандартной библиотеке."""

    def __init__(self, base: str, timeout: float):
        self.base, self.timeout = base.rstrip("/"), timeout

    def call(self, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
        import urllib.request
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "null")

    def login(self, user_id: str) -> str:
        variable = f"PUNKT_{user_id.upper()}_PASSWORD"
        password = os.environ.get(variable)
        if not password:
            sys.exit(f"Перед прогоном API задайте {variable}.")
        return self.call("POST", "/auth/login", {"user_id": user_id, "password": password})["token"]


def run_api(cases: list[dict], k: int, base: str = "http://127.0.0.1:8000", timeout: float = 180) -> tuple[list[dict], dict]:
    """Прогон через работающий стенд: каждый сценарий — новый чат; сценарий 3 — ответ и подтверждение от службы качества."""
    api = _Api(base, timeout)
    try:
        health = api.call("GET", "/health")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Стенд не отвечает на {base}/health ({e}). Запусти backend или передай --base-url.")
    emp, qual = api.login("employee"), api.login("quality")
    rows, raw = [], []
    for c in cases:
        sid = api.call("POST", "/chat/sessions", {}, emp)["session_id"]
        statuses, last, t0 = [], {}, time.perf_counter()
        for turn in c["turns"]:
            try:
                last = api.call("POST", "/chat/ask", {"session_id": sid, "question": turn}, emp)
            except Exception as e:  # noqa: BLE001 — ошибка стенда = провал сценария, прогон продолжаем
                last = {"status": "error", "answer": f"Ошибка API: {e}"}
            statuses.append(last.get("status"))
        row = _judge(c, statuses, last, (time.perf_counter() - t0) * 1000)
        if c.get("qa_confirm") and (last.get("ticket") or {}).get("ticket_id"):
            q = c["qa_confirm"]
            entry = api.call("POST", f"/tickets/{last['ticket']['ticket_id']}/answer",
                             {k2: q.get(k2) for k2 in ("answer", "doc_id", "section", "directorate", "stage")}, qual)
            api.call("POST", f"/qa/{entry['qa_entry_id']}/confirm", {}, qual)
            row["answer"] += f"  [служба качества подтвердила ответ по {last['ticket']['ticket_id']}]"
        rows.append(row)
        raw.append({"id": c["id"], **last})
    summary = _agent_summary(rows, "api")
    summary["health"] = health
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{dt.datetime.now():%Y-%m-%d_%H%M}_api_runs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in raw) + "\n", encoding="utf-8")
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
        out += ["| id | кат. | статусы | факты | источники | утечка | мс | токены вх/вых |", "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            out.append(f"| {r['id']} | {r['category']} | {'✅' if r['status_ok'] else '❌'} {r['statuses']} | "
                       f"{'✅' if r['facts_ok'] else '❌ ' + ', '.join(r['missing'])} | {'✅' if r['sources_ok'] else '❌'} | "
                       f"{', '.join(r['leak'])} | {r['ms']} | {r.get('prompt_tokens')}/{r.get('completion_tokens')} |")
        bad = [r for r in rows if not (r["status_ok"] and r["facts_ok"] and r["sources_ok"]) or r["leak"]]
        if bad:
            out += ["", "## Ответы с ошибками", ""] + [f"- **{r['id']}** {r['query']} → {r['statuses']}: {r['answer']}" for r in bad]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", choices=["retrieval", "agent", "api"], default="retrieval")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000", help="для --target api: backend «Пункта» (или http://127.0.0.1:8080/api)")
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--dataset", type=Path, default=DATASET)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--only", default="")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    cases = load(a.dataset, set(a.only.split(",")) if a.only else None)
    if a.target == "api":
        rows, summary = run_api(cases, a.k, a.base_url, a.timeout)
    else:
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
