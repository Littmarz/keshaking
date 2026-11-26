"""Смысловой поиск (bge-m3) + RRF + реранкер (bge-reranker-v2-m3) — включается сам, если доступны.

Нужно: `pip install numpy FlagEmbedding` и модели в `MODELS_DIR` (по умолчанию `models/hf`):
`models/hf/bge-m3`, `models/hf/bge-reranker-v2-m3` (см. docs/team/2-documents.md, «Модели»).
Нет пакетов или моделей → `retrieval.mock.search` остаётся на BM25 и пишет предупреждение один раз.
Принудительно выключить: `RETRIEVAL_MODE=bm25`. Без реранкера: `RERANK=0`.

⚠️ На машине разработчика 3 нет доступа к PyPI/HF — модуль написан по документации FlagEmbedding,
но ещё НЕ запускался. Первый запуск на Mac: `python -m eval.run` и сравнить с BM25 (`RETRIEVAL_MODE=bm25`).

Схема: BM25 top-N ∪ dense top-N → RRF (k=60) → реранкер на top-M → порядок.
Корпус маленький (сотни пунктов), поэтому векторы — в памяти (numpy), без Qdrant; кэш на диске.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = Path(os.environ.get("MODELS_DIR", ROOT / "models" / "hf"))
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "bge-reranker-v2-m3")
CACHE_DIR = Path(os.environ.get("PROCESSED_DIR", ROOT / "data" / "processed"))
TOP_N = int(os.environ.get("HYBRID_TOP_N", "30"))
RERANK_TOP_M = int(os.environ.get("RERANK_TOP_M", "20"))
RRF_K = 60

_embedder = None
_reranker = None
_matrix = None
_matrix_key = None


def _model_path(name: str) -> str:
    p = MODELS_DIR / name
    if p.exists():
        return str(p)
    if os.environ.get("HF_HUB_OFFLINE") == "1":
        raise FileNotFoundError(f"нет модели {p} (HF_HUB_OFFLINE=1)")
    return f"BAAI/{name}"


def _use_fp16() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def embedder():
    global _embedder
    if _embedder is None:
        from FlagEmbedding import BGEM3FlagModel
        _embedder = BGEM3FlagModel(_model_path(EMBED_MODEL), use_fp16=_use_fp16())
    return _embedder


def reranker():
    global _reranker
    if _reranker is None:
        from FlagEmbedding import FlagReranker
        _reranker = FlagReranker(_model_path(RERANK_MODEL), use_fp16=_use_fp16())
    return _reranker


def _passage(c) -> str:
    head = c.heading_path[-1] if c.heading_path else ""
    return f"{c.doc_title}. {head}\n{c.text}"


def _encode(texts: list[str]):
    import numpy as np
    out = embedder().encode(texts, batch_size=16, max_length=1024, return_dense=True,
                            return_sparse=False, return_colbert_vecs=False)["dense_vecs"]
    v = np.asarray(out, dtype="float32")
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


def chunk_matrix(chunks):
    """Векторы всех пунктов; кэш в памяти и на диске по хэшу текстов."""
    global _matrix, _matrix_key
    import numpy as np
    key = hashlib.sha1("\x1e".join(c.chunk_id + "\x1f" + c.text for c in chunks).encode("utf-8")).hexdigest()[:16]
    if _matrix is not None and _matrix_key == key:
        return _matrix
    path = CACHE_DIR / f"dense_{EMBED_MODEL}_{key}.npy"
    if path.exists():
        _matrix = np.load(path)
    else:
        _matrix = _encode([_passage(c) for c in chunks])
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.save(path, _matrix)
    _matrix_key = key
    return _matrix


def hybrid_rank(query: str, chunks, bm25_order: list[int], k: int,
                allowed: Callable[[int], bool]) -> tuple[list[int], dict[int, float], str]:
    """Вернёт (порядок индексов чанков, {индекс: итоговый score}, режим)."""
    import numpy as np

    m = chunk_matrix(chunks)
    q = _encode([query])[0]
    sims = m @ q
    dense_order = [int(i) for i in np.argsort(-sims) if allowed(int(i))][:TOP_N]

    fused: dict[int, float] = {}
    for rank, i in enumerate(bm25_order[:TOP_N]):
        fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, i in enumerate(dense_order):
        fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
    order = sorted(fused, key=lambda i: -fused[i])

    if os.environ.get("RERANK", "1") != "0":
        try:
            cand = order[:RERANK_TOP_M]
            scores = reranker().compute_score([[query, _passage(chunks[i])] for i in cand], normalize=True)
            if not isinstance(scores, list):
                scores = [scores]
            rr = {i: float(s) for i, s in zip(cand, scores)}
            order = sorted(cand, key=lambda i: -rr[i]) + order[RERANK_TOP_M:]
            return order, rr, "hybrid+rerank"
        except Exception:
            pass
    return order, {i: float(sims[i]) for i in order}, "hybrid"
