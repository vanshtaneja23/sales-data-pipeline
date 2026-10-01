"""Answer questions about the pipeline from its documentation.

Flow per question:
  normalise -> answer cache (Postgres) -> embed (LRU cache) -> hybrid retrieve ->
  confidence gate (best cosine similarity < threshold => fallback, no LLM call) ->
  grounded prompt with numbered excerpts -> LLM -> parse [n] citations -> cache -> structured log
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import psycopg

from sales_rag import store
from sales_rag.llm import LLM
from sales_rag.logs import log_event
from sales_rag.settings import PROMPT_VERSION, RagSettings

log = logging.getLogger("sales_rag.service")

NO_ANSWER = "I don't know based on the pipeline documentation."

SYSTEM_PROMPT = f"""You answer questions about a retail sales data pipeline (Airflow, dbt, Postgres) \
using ONLY the numbered documentation excerpts in the user message.
Rules:
- If the excerpts do not contain the answer, reply exactly: {NO_ANSWER}
- Answer in 1-4 sentences. Use the exact table, column and file names and the exact numbers from the excerpts.
- After each fact, cite the excerpt(s) it came from like [1] or [2][3].
- Do not use outside knowledge and do not guess."""


def normalise_question(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower())


def is_abstention(text: str) -> bool:
    t = text.lower().replace("’", "'")
    return "don't know based on the pipeline documentation" in t or "do not know based on the pipeline" in t


@dataclass
class Answer:
    question: str
    answer: str
    citations: list[str]
    retrieved: list[dict]
    confidence: float
    is_fallback: bool
    abstained: bool
    model: str
    corpus_version: str
    cached: bool = False
    latency_ms: dict[str, float] = field(default_factory=dict)


def build_user_prompt(question: str, hits: list[store.Hit]) -> str:
    excerpts = "\n\n".join(f"[{i}] {h.title}\n{h.body}" for i, h in enumerate(hits, start=1))
    return f"Documentation excerpts:\n\n{excerpts}\n\nQuestion: {question}\nAnswer:"


class RagService:
    def __init__(self, conn: psycopg.Connection, embed_query: Callable[[str], tuple[float, ...]],
                 llm_factory: Callable[[], LLM], model_name: str, settings: RagSettings):
        self.conn = conn
        self.embed_query = embed_query
        self._llm_factory = llm_factory
        self._llm: LLM | None = None
        self.model_name = model_name
        self.settings = settings
        self.corpus_version = store.get_meta(conn, "corpus_version") or "unindexed"

    @property
    def llm(self) -> LLM:
        if self._llm is None:  # loaded lazily: fallbacks and cache hits never pay the load cost
            self._llm = self._llm_factory()
        return self._llm

    def cache_key(self, question: str) -> str:
        parts = [normalise_question(question), self.corpus_version, self.model_name, PROMPT_VERSION,
                 self.settings.top_k, self.settings.min_confidence, self.settings.retrieval_mode]
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()

    def retrieve(self, question: str, k: int | None = None, mode: str | None = None) -> list[store.Hit]:
        return store.search(self.conn, self.embed_query(question), question, k or self.settings.top_k,
                            mode or self.settings.retrieval_mode)

    def _fallback(self, hits: list[store.Hit], confidence: float) -> str:
        topics = "; ".join(h.title for h in hits[:3])
        return (f"I couldn't find a confident answer in the pipeline documentation (best match similarity "
                f"{confidence:.2f} is below the {self.settings.min_confidence:.2f} threshold). "
                f"Closest documented topics: {topics}.")

    def answer(self, question: str, use_cache: bool = True) -> Answer:
        t0 = time.perf_counter()
        key = self.cache_key(question)
        if use_cache:
            row = self.conn.execute(
                "UPDATE rag.answer_cache SET hits = hits + 1 WHERE cache_key = %s RETURNING answer", (key,)
            ).fetchone()
            if row:
                ans = Answer(**row[0])
                ans.cached = True
                ans.latency_ms = {"total": round((time.perf_counter() - t0) * 1000, 2)}
                self._log(ans, key)
                return ans

        t1 = time.perf_counter()
        hits = self.retrieve(question)
        t2 = time.perf_counter()
        confidence = max((h.similarity for h in hits), default=0.0)
        retrieved = [{"id": h.id, "title": h.title, "similarity": round(h.similarity, 4)} for h in hits]

        if confidence < self.settings.min_confidence:
            text, citations, fallback, gen_ms = self._fallback(hits, confidence), [], True, 0.0
        else:
            t3 = time.perf_counter()
            text = self.llm.generate(SYSTEM_PROMPT, build_user_prompt(question, hits), max_tokens=300)
            gen_ms = (time.perf_counter() - t3) * 1000
            cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 1 <= int(n) <= len(hits)})
            citations, fallback = [hits[n - 1].id for n in cited], False

        ans = Answer(
            question=question, answer=text, citations=citations, retrieved=retrieved,
            confidence=round(confidence, 4), is_fallback=fallback,
            abstained=fallback or is_abstention(text), model=self.model_name,
            corpus_version=self.corpus_version,
            latency_ms={"retrieve": round((t2 - t1) * 1000, 2), "generate": round(gen_ms, 2),
                        "total": round((time.perf_counter() - t0) * 1000, 2)},
        )
        if use_cache:
            payload = {k: v for k, v in asdict(ans).items() if k not in ("cached", "latency_ms")}
            self.conn.execute(
                "INSERT INTO rag.answer_cache (cache_key, question, answer) VALUES (%s, %s, %s) "
                "ON CONFLICT (cache_key) DO UPDATE SET answer = EXCLUDED.answer, created_at = now()",
                (key, question, json.dumps(payload)),
            )
        self._log(ans, key)
        return ans

    def _log(self, ans: Answer, key: str) -> None:
        log_event(
            log, "rag_answer",
            question_hash=key[:12], question=ans.question, model=ans.model, cached=ans.cached,
            confidence=ans.confidence, is_fallback=ans.is_fallback, abstained=ans.abstained,
            top_ids=[r["id"] for r in ans.retrieved], citations=ans.citations, latency_ms=ans.latency_ms,
            corpus_version=ans.corpus_version,
        )
