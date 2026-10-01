"""Evaluation harness for the docs Q&A assistant.

Measures, on a hand-written ground-truth set (eval/questions.jsonl):
  1. Retrieval hit-rate @1/3/5 and MRR, for vector-only vs hybrid retrieval (ablation, no LLM).
  2. Confidence-threshold calibration: how a fallback threshold trades false fallbacks on
     answerable questions against catching unanswerable ones.
  3. Answer accuracy, deterministic: every required key fact present (answerable), or a correct
     abstention (unanswerable).
  4. LLM-as-judge: CORRECT / PARTIAL / INCORRECT against a written reference. An answer that did
     not abstain and was judged INCORRECT is flagged "confidently wrong". Judge-vs-deterministic
     agreement is reported so the judge itself is evaluated, not trusted blindly.
  5. Latency (p50/p95) and the answer cache's hit vs miss latency.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from sales_rag.llm import LLM
from sales_rag.service import Answer, RagService

JUDGE_SYSTEM = """You grade answers produced by a documentation assistant. Compare the ANSWER with \
the REFERENCE for the QUESTION.
Reply with exactly two lines:
VERDICT: CORRECT or PARTIAL or INCORRECT
REASON: <one short sentence>
CORRECT = states the reference's key facts and contradicts none (extra correct detail is fine).
PARTIAL = some key facts right, some missing; nothing contradicted.
INCORRECT = contradicts the reference, gives wrong names or numbers, or claims not to know when the \
reference contains the answer. If the reference says the documentation does not cover the question, \
the answer is CORRECT only if it says it does not know / cannot find it."""

UNANSWERABLE_REFERENCE = (
    "The documentation does not contain this information; the assistant should say it doesn't know."
)


@dataclass
class Question:
    id: str
    question: str
    answerable: bool
    category: str
    expected_ids: list[str] = field(default_factory=list)
    must_include: list[list[str]] = field(default_factory=list)
    reference: str = ""


def load_questions(path: Path) -> list[Question]:
    qs = [Question(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
    ids = [q.id for q in qs]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate question ids")
    for q in qs:
        if q.answerable and not (q.expected_ids and q.must_include and q.reference):
            raise ValueError(f"{q.id}: answerable questions need expected_ids, must_include and reference")
    return qs


def normalise(text: str) -> str:
    t = text.lower().replace("’", "'").replace("–", "-").replace("−", "-")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 1,017,209 -> 1017209
    return re.sub(r"\s+", " ", t)


def facts_present(answer: str, must_include: list[list[str]]) -> bool:
    a = normalise(answer)
    return all(any(normalise(alt) in a for alt in group) for group in must_include)


def answer_correct(q: Question, ans: Answer) -> bool:
    if not q.answerable:
        return ans.abstained
    return not ans.abstained and facts_present(ans.answer, q.must_include)


def retrieval_rank(q: Question, retrieved_ids: list[str]) -> int | None:
    for rank, rid in enumerate(retrieved_ids, start=1):
        if rid in q.expected_ids:
            return rank
    return None


def retrieval_metrics(ranks: list[int | None]) -> dict[str, float]:
    n = len(ranks)
    return {
        "n": n,
        "hit@1": round(sum(r is not None and r <= 1 for r in ranks) / n, 3),
        "hit@3": round(sum(r is not None and r <= 3 for r in ranks) / n, 3),
        "hit@5": round(sum(r is not None and r <= 5 for r in ranks) / n, 3),
        "mrr@5": round(sum(1 / r for r in ranks if r is not None) / n, 3),
    }


def parse_verdict(text: str) -> str:
    m = re.search(r"VERDICT:\s*\**\s*(CORRECT|PARTIAL|INCORRECT)", text, re.IGNORECASE)
    # Small judges sometimes drop the "VERDICT:" label and lead with the word ("INCORRECT = ...").
    m = m or re.match(r"\s*\**\s*(CORRECT|PARTIAL|INCORRECT)\b", text, re.IGNORECASE)
    return m.group(1).upper() if m else "UNPARSEABLE"


def judge(llm: LLM, q: Question, ans: Answer) -> tuple[str, str]:
    reference = q.reference if q.answerable else UNANSWERABLE_REFERENCE
    raw = llm.generate(JUDGE_SYSTEM, f"QUESTION: {q.question}\nREFERENCE: {reference}\nANSWER: {ans.answer}",
                       max_tokens=80)
    return parse_verdict(raw), raw


def threshold_sweep(conf_answerable: list[float], conf_unanswerable: list[float]) -> list[dict]:
    rows = []
    for i in range(17):
        t = round(0.40 + i * 0.025, 3)
        rows.append({
            "threshold": t,
            "answerable_false_fallback_rate": round(
                sum(c < t for c in conf_answerable) / len(conf_answerable), 3),
            "unanswerable_caught_rate": round(
                sum(c < t for c in conf_unanswerable) / max(len(conf_unanswerable), 1), 3),
        })
    return rows


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))], 1)


def _share(rows: list[dict], pred) -> float | None:
    return round(sum(bool(pred(r)) for r in rows) / len(rows), 3) if rows else None


def _timed_answer(service: RagService, question: str) -> tuple[Answer, float]:
    t = time.perf_counter()
    ans = service.answer(question)
    return ans, (time.perf_counter() - t) * 1000


def run_eval(service: RagService, questions: list[Question], out_dir: Path, use_judge: bool = True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    answerable = [q for q in questions if q.answerable]
    unanswerable = [q for q in questions if not q.answerable]

    # 1-2. Retrieval ablation + confidence calibration (no LLM involved).
    retrieval: dict[str, dict] = {}
    conf: dict[str, float] = {}
    for mode in ("vector", "hybrid"):
        ranks = []
        for q in questions:
            hits = service.retrieve(q.question, k=5, mode=mode)
            if mode == service.settings.retrieval_mode:
                conf[q.id] = max(h.similarity for h in hits)
            if q.answerable:
                ranks.append(retrieval_rank(q, [h.id for h in hits]))
        retrieval[mode] = retrieval_metrics(ranks)
    sweep = threshold_sweep([conf[q.id] for q in answerable], [conf[q.id] for q in unanswerable])

    # 3-4. End-to-end answers (cache bypassed so every answer is freshly generated) + judge.
    details = []
    for q in questions:
        ans = service.answer(q.question, use_cache=False)
        verdict, raw = judge(service.llm, q, ans) if use_judge else ("SKIPPED", "")
        details.append({
            "id": q.id, "category": q.category, "answerable": q.answerable, "question": q.question,
            "answer": ans.answer, "abstained": ans.abstained, "is_fallback": ans.is_fallback,
            "confidence": ans.confidence, "retrieved": [r["id"] for r in ans.retrieved],
            "retrieval_rank": retrieval_rank(q, [r["id"] for r in ans.retrieved]) if q.answerable else None,
            "citations": ans.citations, "facts_correct": answer_correct(q, ans),
            "judge_verdict": verdict, "judge_raw": raw,
            "confidently_wrong": (not ans.abstained) and verdict == "INCORRECT",
            "latency_ms": ans.latency_ms,
        })

    # 5. Cache: re-ask a sample with the cache on (first call fills it, second must hit).
    miss_ms, hit_ms = [], []
    for q in questions[:10]:
        service.conn.execute("DELETE FROM rag.answer_cache WHERE cache_key = %s",
                             (service.cache_key(q.question),))
        _, miss = _timed_answer(service, q.question)
        second, hit = _timed_answer(service, q.question)
        if not second.cached:
            raise RuntimeError(f"cache miss on repeated question {q.id}: answer cache is broken")
        miss_ms.append(miss)
        hit_ms.append(hit)

    ans_rows = [d for d in details if d["answerable"]]
    un_rows = [d for d in details if not d["answerable"]]
    answered = [d for d in details if not d["abstained"]]
    judged = [d for d in details if d["judge_verdict"] in ("CORRECT", "PARTIAL", "INCORRECT")]
    gen = [d["latency_ms"]["total"] for d in details if not d["is_fallback"]]
    summary = {
        "model": service.model_name,
        "embedding_model": service.settings.embedding_model,
        "corpus_version": service.corpus_version,
        "n_questions": len(questions), "n_answerable": len(answerable), "n_unanswerable": len(unanswerable),
        "min_confidence": service.settings.min_confidence,
        "retrieval_mode": service.settings.retrieval_mode,
        "retrieval": retrieval,
        "threshold_sweep": sweep,
        "answer_accuracy": {
            "overall": _share(details, lambda d: d["facts_correct"]),
            "answerable": _share(ans_rows, lambda d: d["facts_correct"]),
            "unanswerable_correctly_declined": _share(un_rows, lambda d: d["facts_correct"]),
        },
        "fallback_rate": {
            "answerable": _share(ans_rows, lambda d: d["is_fallback"]),
            "unanswerable": _share(un_rows, lambda d: d["is_fallback"]),
        },
        "llm_abstained_answerable": _share(ans_rows, lambda d: d["abstained"] and not d["is_fallback"]),
        "judge": {
            "verdicts": {v: sum(d["judge_verdict"] == v for d in details)
                         for v in ("CORRECT", "PARTIAL", "INCORRECT", "UNPARSEABLE", "SKIPPED")},
            "correct_rate": _share(judged, lambda d: d["judge_verdict"] == "CORRECT"),
            "agreement_with_fact_check": _share(
                judged, lambda d: (d["judge_verdict"] == "CORRECT") == d["facts_correct"]),
            "confidently_wrong": [d["id"] for d in details if d["confidently_wrong"]],
            "confidently_wrong_rate_of_answered": _share(answered, lambda d: d["confidently_wrong"]),
        },
        # Second, deterministic signal: answered (no abstention) but required facts missing/wrong.
        "answered_but_failed_fact_check": [d["id"] for d in answered if not d["facts_correct"]],
        "false_abstentions": [d["id"] for d in ans_rows if d["abstained"]],
        "latency_ms": {"p50_generated": pct(gen, 50), "p95_generated": pct(gen, 95),
                       "cache_miss_p50": pct(miss_ms, 50), "cache_hit_p50": pct(hit_ms, 50)},
    }
    (out_dir / "latest.json").write_text(json.dumps({"summary": summary, "details": details}, indent=2))
    (out_dir / "report.md").write_text(render_report(summary, details))
    return summary


def render_report(s: dict, details: list[dict]) -> str:
    r = s["retrieval"]
    lines = [
        "# RAG evaluation report", "",
        f"Model: `{s['model']}` · embeddings: `{s['embedding_model']}` · corpus `{s['corpus_version']}` · "
        f"{s['n_questions']} questions ({s['n_answerable']} answerable, "
        f"{s['n_unanswerable']} out-of-scope) · "
        f"retrieval `{s['retrieval_mode']}` · fallback threshold {s['min_confidence']}", "",
        "## Retrieval (answerable questions)", "",
        "| mode | hit@1 | hit@3 | hit@5 | MRR@5 |", "|---|---|---|---|---|",
        *[f"| {m} | {r[m]['hit@1']} | {r[m]['hit@3']} | {r[m]['hit@5']} | {r[m]['mrr@5']} |" for m in r],
        "", "## Answers", "",
        f"* Answer accuracy (deterministic key-fact check): overall **{s['answer_accuracy']['overall']}**, "
        f"answerable {s['answer_accuracy']['answerable']}, out-of-scope correctly declined "
        f"{s['answer_accuracy']['unanswerable_correctly_declined']}",
        f"* Fallback (low retrieval confidence) rate: answerable {s['fallback_rate']['answerable']}, "
        f"out-of-scope {s['fallback_rate']['unanswerable']}",
        f"* LLM-as-judge verdicts: {s['judge']['verdicts']}; judge agrees with fact check on "
        f"{s['judge']['agreement_with_fact_check']} of judged answers",
        f"* Confidently wrong (answered, judged INCORRECT): {s['judge']['confidently_wrong']} "
        f"({s['judge']['confidently_wrong_rate_of_answered']} of answered)",
        f"* Answered but failed the key-fact check: {s['answered_but_failed_fact_check']}",
        f"* False abstentions (answerable, but the system said it doesn't know): {s['false_abstentions']}",
        f"* Latency: p50 {s['latency_ms']['p50_generated']} ms, p95 {s['latency_ms']['p95_generated']} ms "
        f"(generated); cache miss p50 {s['latency_ms']['cache_miss_p50']} ms vs hit "
        f"{s['latency_ms']['cache_hit_p50']} ms",
        "", "## Per question", "",
        "| id | answerable | rank | fallback | facts ok | judge | answer |", "|---|---|---|---|---|---|---|",
    ]
    for d in details:
        text = d["answer"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {d['id']} | {d['answerable']} | {d['retrieval_rank']} | {d['is_fallback']} | "
                     f"{d['facts_correct']} | {d['judge_verdict']} | {text[:160]} |")
    return "\n".join(lines) + "\n"
