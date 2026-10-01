"""LLM providers behind one tiny interface.

* AnthropicLLM: Claude via the official SDK (used when ANTHROPIC_API_KEY is set).
* LocalLLM: a small open instruct model via transformers, greedy decoding (deterministic), runs on
  Apple-silicon GPU (MPS) when available. No key, no cost; lower quality, and measured as such.
"""

from __future__ import annotations

import os
from typing import Protocol

from sales_rag.settings import RagSettings


class LLM(Protocol):
    name: str

    def generate(self, system: str, user: str, max_tokens: int = 400) -> str: ...


class LLMRefusalError(RuntimeError):
    pass


class AnthropicLLM:
    def __init__(self, model: str):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model
        self.name = f"anthropic:{model}"

    def generate(self, system: str, user: str, max_tokens: int = 400) -> str:
        # Current Claude models reject temperature/top_p, so determinism comes from the prompt.
        # Short grounded Q&A -> low effort. Server-side refusal fallbacks are enabled by default.
        resp = self.client.beta.messages.create(
            model=self.model,
            max_tokens=max(max_tokens, 2048),  # adaptive thinking tokens count toward max_tokens
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            raise LLMRefusalError(f"model declined: {resp.stop_details}")
        return "".join(b.text for b in resp.content if b.type == "text").strip()


class LocalLLM:
    def __init__(self, model_name: str):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        dtype = torch.float16 if self.device == "mps" else torch.float32
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(model_name, dtype=dtype).to(self.device).eval()
        self.name = f"local:{model_name}"

    def generate(self, system: str, user: str, max_tokens: int = 400) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        inputs = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
        ).to(self.device)
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False,
                                      temperature=None, top_p=None, top_k=None,
                                      pad_token_id=self.tokenizer.eos_token_id)
        new_tokens = out[0, inputs["input_ids"].shape[1]:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


def resolve_provider(settings: RagSettings) -> str:
    if settings.llm_provider == "auto":
        return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "local"
    if settings.llm_provider not in ("anthropic", "local"):
        raise ValueError(f"LLM_PROVIDER must be auto|anthropic|local, got {settings.llm_provider!r}")
    return settings.llm_provider


def llm_name(settings: RagSettings) -> str:
    """Model identity without loading it (used in cache keys)."""
    if resolve_provider(settings) == "anthropic":
        return f"anthropic:{settings.anthropic_model}"
    return f"local:{settings.local_model}"


def get_llm(settings: RagSettings) -> LLM:
    if resolve_provider(settings) == "anthropic":
        return AnthropicLLM(settings.anthropic_model)
    return LocalLLM(settings.local_model)
