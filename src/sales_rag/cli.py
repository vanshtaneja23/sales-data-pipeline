"""Command line: index the docs, ask a question, or run the evaluation.

    python -m sales_rag.cli index
    python -m sales_rag.cli ask "What does sales_per_customer mean?"
    python -m sales_rag.cli eval [--no-judge]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from sales_pipeline.config import REPO_ROOT, WarehouseConfig
from sales_rag import logs, store
from sales_rag.corpus import build_corpus, corpus_version
from sales_rag.embeddings import Embedder
from sales_rag.llm import get_llm, llm_name
from sales_rag.settings import RagSettings

EVAL_DIR = REPO_ROOT / "eval"


def _setup():
    settings = RagSettings.from_env()
    conn = store.connect(WarehouseConfig.from_env().conninfo())
    store.ensure_schema(conn, settings.embedding_dim)
    return settings, conn, Embedder(settings.embedding_model)


def cmd_index() -> None:
    settings, conn, embedder = _setup()
    chunks = build_corpus(settings.manifest, settings.catalog, settings.markdown_docs)
    stats = store.sync_chunks(conn, chunks, embedder.embed_passages)
    version = corpus_version(chunks)
    store.set_meta(conn, "corpus_version", version)
    by_kind: dict[str, int] = {}
    for c in chunks:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
    print(json.dumps({"corpus_version": version, **stats, "by_kind": by_kind}))


def _service(settings, conn, embedder):
    from sales_rag.service import RagService

    return RagService(conn, embedder.embed_query, lambda: get_llm(settings), llm_name(settings), settings)


def cmd_ask(question: str, use_cache: bool) -> None:
    settings, conn, embedder = _setup()
    ans = _service(settings, conn, embedder).answer(question, use_cache=use_cache)
    print(json.dumps(asdict(ans), indent=2))


def cmd_eval(use_judge: bool) -> None:
    from sales_rag.evaluation import load_questions, run_eval

    settings, conn, embedder = _setup()
    logs.configure(log_file=EVAL_DIR / "results" / "rag_queries.jsonl")
    summary = run_eval(_service(settings, conn, embedder), load_questions(EVAL_DIR / "questions.jsonl"),
                       EVAL_DIR / "results", use_judge=use_judge)
    print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="sales_rag")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--no-cache", action="store_true")
    ev = sub.add_parser("eval")
    ev.add_argument("--no-judge", action="store_true")
    args = parser.parse_args()

    logs.configure()
    if args.cmd == "index":
        cmd_index()
    elif args.cmd == "ask":
        cmd_ask(args.question, use_cache=not args.no_cache)
    else:
        cmd_eval(use_judge=not args.no_judge)


if __name__ == "__main__":
    main()
