"""RAG settings from environment variables (.env), with defaults that work locally."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sales_pipeline.config import REPO_ROOT

PROMPT_VERSION = "v1"


@dataclass(frozen=True)
class RagSettings:
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    llm_provider: str = "auto"  # auto | anthropic | local
    anthropic_model: str = "claude-opus-5"
    local_model: str = "Qwen/Qwen2.5-3B-Instruct"
    top_k: int = 5
    # "vector" or "hybrid" (vector + full-text, RRF). Measured on the eval set, hybrid *lowered*
    # hit@1 (0.732 vs 0.805): OR-ed full-text favours long glossary chunks. Vector is the default.
    retrieval_mode: str = "vector"
    # Below this cosine similarity of the best retrieved chunk we do not call the LLM at all and
    # return a fallback. From the calibration sweep: answerable questions never scored below 0.644,
    # 7 of 8 out-of-scope ones scored <= 0.62. Chosen in-sample on 49 questions; the margin is thin.
    min_confidence: float = 0.625
    manifest: Path = REPO_ROOT / "dbt" / "target" / "manifest.json"
    catalog: Path = REPO_ROOT / "dbt" / "target" / "catalog.json"
    markdown_docs: tuple[Path, ...] = (
        REPO_ROOT / "docs" / "DATA_DICTIONARY.md",
        REPO_ROOT / "analytics" / "TIERING.md",
    )

    @classmethod
    def from_env(cls) -> RagSettings:
        d = cls()
        return cls(
            embedding_model=os.environ.get("EMBEDDING_MODEL", d.embedding_model),
            llm_provider=os.environ.get("LLM_PROVIDER", d.llm_provider),
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", d.anthropic_model),
            local_model=os.environ.get("LOCAL_LLM_MODEL", d.local_model),
            top_k=int(os.environ.get("RAG_TOP_K", d.top_k)),
            retrieval_mode=os.environ.get("RAG_RETRIEVAL_MODE", d.retrieval_mode),
            min_confidence=float(os.environ.get("RAG_MIN_CONFIDENCE", d.min_confidence)),
        )
