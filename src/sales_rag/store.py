"""pgvector storage and hybrid retrieval in the warehouse Postgres.

Retrieval = vector (cosine, HNSW) + Postgres full-text search, fused with Reciprocal Rank Fusion.
Vector search handles paraphrase ("average basket" ~ "sales per customer"); full text catches exact
identifiers and numbers ("is_zero_sales_while_open", "HB,NI") that embeddings blur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from sales_rag.corpus import Chunk

RRF_K = 60
CANDIDATES = 20

DDL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS rag;
CREATE TABLE IF NOT EXISTS rag.chunks (
  id            text PRIMARY KEY,
  kind          text NOT NULL,
  title         text NOT NULL,
  body          text NOT NULL,
  source        text NOT NULL,
  content_hash  text NOT NULL,
  embedding     vector({dim}) NOT NULL,
  tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED,
  indexed_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON rag.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON rag.chunks USING gin (tsv);
CREATE TABLE IF NOT EXISTS rag.answer_cache (
  cache_key   text PRIMARY KEY,
  question    text NOT NULL,
  answer      jsonb NOT NULL,
  hits        integer NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rag.meta (key text PRIMARY KEY, value text NOT NULL);
"""


@dataclass(frozen=True)
class Hit:
    id: str
    title: str
    body: str
    similarity: float  # cosine similarity to the query, always computed (used for confidence)
    score: float  # fused ranking score


def connect(conninfo: str) -> psycopg.Connection:
    conn = psycopg.connect(conninfo, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def ensure_schema(conn: psycopg.Connection, dim: int) -> None:
    conn.execute(DDL.format(dim=dim))


def sync_chunks(conn: psycopg.Connection, chunks: list[Chunk], embed) -> dict[str, int]:
    """Incremental indexing: only new or changed chunks are embedded; removed ones are deleted."""
    existing = dict(conn.execute("SELECT id, content_hash FROM rag.chunks").fetchall())
    todo = [c for c in chunks if existing.get(c.id) != c.content_hash]
    stats = {"total": len(chunks), "added": sum(c.id not in existing for c in todo),
             "updated": sum(c.id in existing for c in todo), "unchanged": len(chunks) - len(todo)}
    if todo:
        vectors = embed([c.embed_text for c in todo])
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rag.chunks (id, kind, title, body, source, content_hash, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, "
                "title = EXCLUDED.title, body = EXCLUDED.body, source = EXCLUDED.source, "
                "content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding, indexed_at = now()",
                [(c.id, c.kind, c.title, c.body, c.source, c.content_hash, np.asarray(v, dtype=np.float32))
                 for c, v in zip(todo, vectors, strict=True)],
            )
    stale = set(existing) - {c.id for c in chunks}
    if stale:
        conn.execute("DELETE FROM rag.chunks WHERE id = ANY(%s)", (list(stale),))
    stats["deleted"] = len(stale)
    return stats


def set_meta(conn: psycopg.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO rag.meta VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )


def get_meta(conn: psycopg.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM rag.meta WHERE key = %s", (key,)).fetchone()
    return row[0] if row else None


def or_tsquery(question: str) -> str | None:
    """Questions rarely contain *all* words of a passage; OR the terms and let ranking sort it out."""
    terms = {t for t in re.findall(r"[a-z0-9_]+", question.lower()) if len(t) > 1}
    # Also split snake_case identifiers so 'sales_per_customer' matches 'sales per customer'.
    # Postgres' parser indexes 'sales_per_customer' as its parts, so query with the parts too.
    terms = {p for t in terms for p in t.split("_") if len(p) > 1}
    return " | ".join(sorted(terms)) or None


def rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return scores


def search(conn: psycopg.Connection, query_vec, question: str, k: int = 5, mode: str = "hybrid") -> list[Hit]:
    if mode not in ("hybrid", "vector"):
        raise ValueError(f"unknown retrieval mode {mode!r}")
    qv = np.asarray(query_vec, dtype=np.float32)
    vec_ids = [r[0] for r in conn.execute(
        "SELECT id FROM rag.chunks ORDER BY embedding <=> %s LIMIT %s", (qv, CANDIDATES)).fetchall()]
    rankings = [vec_ids]
    if mode == "hybrid" and (tsq := or_tsquery(question)):
        fts_ids = [r[0] for r in conn.execute(
            "SELECT id FROM rag.chunks, to_tsquery('english', %s) q WHERE tsv @@ q "
            "ORDER BY ts_rank_cd(tsv, q) DESC LIMIT %s", (tsq, CANDIDATES)).fetchall()]
        rankings.append(fts_ids)

    fused = rrf_fuse(rankings)
    top = sorted(fused, key=lambda cid: (-fused[cid], cid))[:k]
    rows = conn.execute(
        "SELECT id, title, body, 1 - (embedding <=> %s) FROM rag.chunks WHERE id = ANY(%s)", (qv, top)
    ).fetchall()
    by_id = {r[0]: r for r in rows}
    return [Hit(cid, by_id[cid][1], by_id[cid][2], float(by_id[cid][3]), fused[cid]) for cid in top]
