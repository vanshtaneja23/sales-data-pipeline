"""Local sentence embeddings (no API key needed). BGE models expect a query instruction prefix
for queries but not for passages; getting this wrong measurably hurts retrieval."""

from __future__ import annotations

from functools import lru_cache

import numpy as np

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name, device="cpu")
        self._query_prefix = BGE_QUERY_PREFIX if "bge" in model_name.lower() else ""
        # In-process cache: repeated questions (eval reruns, retries) skip re-encoding.
        self.embed_query = lru_cache(maxsize=2048)(self._embed_query)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return self._model.encode(texts, normalize_embeddings=True, batch_size=32,
                                  show_progress_bar=False)

    def _embed_query(self, text: str) -> tuple[float, ...]:
        vec = self._model.encode([self._query_prefix + text], normalize_embeddings=True)[0]
        return tuple(float(x) for x in vec)
