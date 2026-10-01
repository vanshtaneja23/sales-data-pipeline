"""RAG unit tests: no database, no model downloads (fakes stand in for both)."""

from __future__ import annotations

import json

import pytest

from sales_rag import service as service_mod
from sales_rag import store
from sales_rag.corpus import Chunk, build_corpus, dbt_chunks, markdown_chunks
from sales_rag.evaluation import (
    Question,
    answer_correct,
    facts_present,
    parse_verdict,
    retrieval_metrics,
    threshold_sweep,
)
from sales_rag.service import NO_ANSWER, Answer, RagService, is_abstention
from sales_rag.settings import RagSettings

# --- corpus ------------------------------------------------------------------------------------


def test_markdown_chunks_keep_heading_path(tmp_path):
    md = tmp_path / "DOC.md"
    md.write_text("# Title\nintro text\n\n## Section A\nalpha\n\n### Sub B\nbeta\n\n## Section C\ngamma\n")
    chunks = {c.id: c for c in markdown_chunks(md)}
    assert set(chunks) == {
        "doc:DOC.md#intro", "doc:DOC.md#section-a", "doc:DOC.md#sub-b", "doc:DOC.md#section-c"
    }
    assert chunks["doc:DOC.md#sub-b"].title == "Title > Section A > Sub B"
    assert chunks["doc:DOC.md#section-c"].title == "Title > Section C"  # sub-heading reset


def test_long_markdown_section_is_split_with_stable_ids(tmp_path):
    md = tmp_path / "DOC.md"
    md.write_text("## Big\n" + "\n\n".join("p" * 700 for _ in range(4)))
    ids = [c.id for c in markdown_chunks(md)]
    assert ids[0] == "doc:DOC.md#big" and ids[1] == "doc:DOC.md#big-2"


def _manifest(tmp_path):
    manifest = {
        "nodes": {
            "model.p.fct": {"resource_type": "model", "name": "fct", "schema": "marts",
                            "config": {"materialized": "table"}, "description": "Daily  fact.",
                            "depends_on": {"nodes": ["model.p.stg"]},
                            "columns": {"amount": {"description": "Turnover."}}},
            "test.p.nn": {"resource_type": "test", "attached_node": "model.p.fct", "column_name": "amount",
                          "test_metadata": {"name": "not_null", "kwargs": {"column_name": "amount"}}},
            "test.p.rc": {"resource_type": "test", "attached_node": "model.p.fct", "column_name": None,
                          "test_metadata": {"name": "row_count_between",
                                            "kwargs": {"arguments": {"min_rows": 1, "max_rows": 9}}}},
            "test.p.recon": {"resource_type": "test", "name": "assert_recon", "test_metadata": None,
                             "raw_code": "-- Reconciles raw to fact.\nselect 1"},
        },
        "sources": {},
        "docs": {},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def test_dbt_chunks_include_descriptions_and_tests(tmp_path):
    chunks = {c.id: c for c in dbt_chunks(_manifest(tmp_path))}
    assert "Daily fact." in chunks["model:fct"].body  # whitespace normalised
    assert "row_count_between(min_rows=1, max_rows=9)" in chunks["model:fct"].body
    assert "Tests: not_null" in chunks["column:fct.amount"].body
    assert chunks["test:assert_recon"].body.endswith("Reconciles raw to fact.")


def test_duplicate_chunk_ids_rejected(tmp_path):
    md = tmp_path / "D.md"
    md.write_text("## Same\na\n\n## Same\nb\n")
    with pytest.raises(ValueError, match="duplicate chunk ids"):
        build_corpus(_manifest(tmp_path), None, (md,))


def test_content_hash_changes_with_text():
    a = Chunk("x", "doc", "t", "body", "s")
    assert a.content_hash != Chunk("x", "doc", "t", "body2", "s").content_hash


# --- retrieval helpers ---------------------------------------------------------------------------


def test_or_tsquery_splits_identifiers():
    q = store.or_tsquery("What does sales_per_customer mean?")
    assert set(q.split(" | ")) == {"what", "does", "sales", "per", "customer", "mean"}
    assert store.or_tsquery("? !") is None


def test_rrf_rewards_agreement_between_rankers():
    scores = store.rrf_fuse([["a", "b", "c"], ["b", "a", "d"]])
    assert max(scores, key=scores.get) in {"a", "b"}
    assert scores["a"] > scores["c"] and scores["b"] > scores["d"]


# --- scoring --------------------------------------------------------------------------------------


def test_facts_present_normalises_thousands_separators_and_case():
    assert facts_present("It has 1,017,209 Rows.", [["1017209"]])
    assert facts_present("Bremen or Lower Saxony", [["bremen"], ["lower saxony"]])
    assert not facts_present("Bremen only", [["bremen"], ["lower saxony"]])


@pytest.mark.parametrize(("raw", "verdict"), [
    ("VERDICT: CORRECT\nREASON: ok", "CORRECT"),
    ("**VERDICT:** incorrect - wrong number", "INCORRECT"),
    ("VERDICT: **PARTIAL**", "PARTIAL"),
    ("INCORRECT = the answer gives the wrong bound", "INCORRECT"),  # label dropped by a small judge
    ("The answer is CORRECT", "UNPARSEABLE"),  # only a *leading* verdict word counts
    ("I think it's fine", "UNPARSEABLE"),
])
def test_parse_verdict(raw, verdict):
    assert parse_verdict(raw) == verdict


def _ans(text: str, abstained: bool = False) -> Answer:
    return Answer("q", text, [], [], 0.9, False, abstained, "m", "v")


def test_answer_correct_for_answerable_and_out_of_scope():
    q = Question("q1", "?", True, "c", ["x"], [["0.25"]], "ref")
    assert answer_correct(q, _ans("The weight is 0.25 [1]."))
    assert not answer_correct(q, _ans(NO_ANSWER, abstained=True))
    u = Question("u1", "?", False, "out")
    assert answer_correct(u, _ans(NO_ANSWER, abstained=True))
    assert not answer_correct(u, _ans("Revenue was 12 billion."))  # confident hallucination


def test_retrieval_metrics_and_sweep():
    m = retrieval_metrics([1, 2, None, 5])
    assert m == {"n": 4, "hit@1": 0.25, "hit@3": 0.5, "hit@5": 0.75, "mrr@5": round((1 + 0.5 + 0.2) / 4, 3)}
    row = next(r for r in threshold_sweep([0.7, 0.8], [0.5, 0.65]) if r["threshold"] == 0.6)
    assert row == {"threshold": 0.6, "answerable_false_fallback_rate": 0.0, "unanswerable_caught_rate": 0.5}


def test_is_abstention():
    assert is_abstention(NO_ANSWER)
    assert is_abstention("I don’t know based on the pipeline documentation.")  # curly apostrophe
    assert not is_abstention("The weight is 0.25.")


# --- service control flow --------------------------------------------------------------------------


class FakeConn:
    """Implements just the SQL the service issues against rag.answer_cache / rag.meta."""

    def __init__(self):
        self.cache: dict[str, dict] = {}

    def execute(self, sql, params=()):
        result = None
        if sql.startswith("UPDATE rag.answer_cache"):
            result = (self.cache[params[0]],) if params[0] in self.cache else None
        elif sql.startswith("INSERT INTO rag.answer_cache"):
            self.cache[params[0]] = json.loads(params[2])
        elif "rag.meta" in sql:
            result = ("corpus-v1",)
        return type("R", (), {"fetchone": staticmethod(lambda: result)})()


class FakeLLM:
    name = "fake"

    def __init__(self, reply: str):
        self.reply, self.calls = reply, 0

    def generate(self, system, user, max_tokens=400):
        self.calls += 1
        return self.reply


def _service(monkeypatch, hits, llm_factory):
    monkeypatch.setattr(store, "search", lambda *a, **k: hits)
    return RagService(FakeConn(), lambda q: (0.0,), llm_factory, "fake", RagSettings(min_confidence=0.6))


def _hit(cid: str, sim: float) -> store.Hit:
    return store.Hit(cid, f"title {cid}", "body", sim, 0.1)


def test_low_confidence_falls_back_without_loading_llm(monkeypatch):
    def never():
        raise AssertionError("LLM must not be loaded for a low-confidence question")

    svc = _service(monkeypatch, [_hit("a", 0.41), _hit("b", 0.30)], never)
    ans = svc.answer("unrelated question")
    assert ans.is_fallback and ans.abstained and ans.citations == []
    assert "0.41" in ans.answer and "title a" in ans.answer


def test_confident_answer_parses_citations(monkeypatch):
    llm = FakeLLM("Weight is 0.25 [2]. Also see [1][9].")  # [9] is out of range and ignored
    svc = _service(monkeypatch, [_hit("a", 0.8), _hit("b", 0.7)], lambda: llm)
    ans = svc.answer("momentum weight?")
    assert not ans.is_fallback and not ans.abstained
    assert ans.citations == ["a", "b"] and llm.calls == 1


def test_llm_abstention_is_detected(monkeypatch):
    svc = _service(monkeypatch, [_hit("a", 0.9)], lambda: FakeLLM(NO_ANSWER))
    ans = svc.answer("something not in docs")
    assert ans.abstained and not ans.is_fallback


def test_second_identical_question_is_served_from_cache(monkeypatch):
    llm = FakeLLM("Answer [1].")
    svc = _service(monkeypatch, [_hit("a", 0.9)], lambda: llm)
    first = svc.answer("What is X?")
    monkeypatch.setattr(store, "search", lambda *a, **k: pytest.fail("cache hit must skip retrieval"))
    second = svc.answer("  what is   x? ")  # normalised to the same key
    assert not first.cached and second.cached
    assert second.answer == first.answer and llm.calls == 1


def test_cache_key_changes_with_model_and_corpus(monkeypatch):
    svc = _service(monkeypatch, [], lambda: FakeLLM(""))
    k1 = svc.cache_key("q")
    svc.corpus_version = "corpus-v2"
    assert svc.cache_key("q") != k1  # re-indexed docs must not serve stale answers


def test_prompt_numbers_excerpts():
    prompt = service_mod.build_user_prompt("Q?", [_hit("a", 0.9), _hit("b", 0.8)])
    assert "[1] title a" in prompt and "[2] title b" in prompt and prompt.endswith("Question: Q?\nAnswer:")
