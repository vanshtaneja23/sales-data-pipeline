"""Build retrieval chunks from the dbt manifest/catalog and the markdown data dictionary.

Chunking follows the structure of the docs rather than fixed token windows:
  model:<name>            one overview per dbt model (description, grain, upstream, model-level tests)
  column:<model>.<col>    one per documented column (type, description, tests)
  source:raw.<table>      one per source table; columns as column:raw.<table>.<col>
  test:<name>             singular dbt tests, described by their header comment
  doc:<file>#<section>    markdown split at ##/### headings, with the heading path kept as title
Small, self-describing chunks make "what does column X mean" retrieve exactly the right text, and
stable ids make retrieval hit-rate measurable against a ground-truth set.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

MAX_DOC_CHARS = 1800


@dataclass(frozen=True)
class Chunk:
    id: str
    kind: str
    title: str
    body: str
    source: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.title}\n{self.body}".encode()).hexdigest()

    @property
    def embed_text(self) -> str:
        return f"{self.title}\n{self.body}"


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _test_label(test: dict) -> str:
    meta = test.get("test_metadata") or {}
    kwargs = {k: v for k, v in (meta.get("kwargs") or {}).items() if k not in ("model", "column_name")}
    args = kwargs.pop("arguments", None) or kwargs
    if meta.get("name") == "relationships":
        to = re.sub(r"ref\('([^']+)'\)", r"\1", str(args.get("to", "")))
        return f"relationships (every value exists in {to}.{args.get('field')})"
    if meta.get("name") == "accepted_values":
        return f"accepted_values {args.get('values')}"
    if args:
        rendered = ", ".join(f"{k}={v}" for k, v in args.items() if k != "config")
        return f"{meta.get('name')}({rendered})"
    return str(meta.get("name"))


def _header_comment(sql: str) -> str:
    lines = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            lines.append(line.strip()[2:].strip())
        elif lines:
            break
    return " ".join(lines)


def dbt_chunks(manifest_path: Path, catalog_path: Path | None = None) -> list[Chunk]:
    manifest = json.loads(manifest_path.read_text())
    catalog = json.loads(catalog_path.read_text()) if catalog_path and catalog_path.exists() else {}
    cat_nodes = {**catalog.get("nodes", {}), **catalog.get("sources", {})}
    nodes = manifest["nodes"]

    tests_by_node: dict[str, list[dict]] = {}
    for n in nodes.values():
        if n["resource_type"] == "test" and n.get("attached_node"):
            tests_by_node.setdefault(n["attached_node"], []).append(n)

    def col_type(uid: str, col: str) -> str:
        return cat_nodes.get(uid, {}).get("columns", {}).get(col, {}).get("type", "unknown")

    chunks: list[Chunk] = []
    for uid, n in sorted(nodes.items()):
        if n["resource_type"] == "model":
            fq = f"{n['schema']}.{n['name']}"
            tests = tests_by_node.get(uid, [])
            model_tests = sorted({_test_label(t) for t in tests if not t.get("column_name")})
            upstream = [d.split(".")[-1] if d.startswith("model.") else ".".join(d.split(".")[-2:])
                        for d in n["depends_on"]["nodes"]]
            body = (
                f"dbt model {fq}, materialized as {n['config']['materialized']}. "
                f"{_clean(n.get('description'))} Built from: {', '.join(upstream) or 'n/a'}. "
                f"Columns: {', '.join(n['columns'])}."
            )
            if model_tests:
                body += f" Model-level data tests: {'; '.join(model_tests)}."
            chunks.append(Chunk(f"model:{n['name']}", "model", f"Model {fq}", body, "dbt manifest"))
            for col, c in n["columns"].items():
                col_tests = sorted({_test_label(t) for t in tests if t.get("column_name") == col})
                cbody = f"Column {col} of {fq} (type {col_type(uid, col)}). {_clean(c.get('description'))}"
                if col_tests:
                    cbody += f" Tests: {', '.join(col_tests)}."
                chunks.append(Chunk(f"column:{n['name']}.{col}", "column", f"Column {fq}.{col}", cbody,
                                    "dbt manifest"))
        elif n["resource_type"] == "test" and not n.get("test_metadata"):
            purpose = _header_comment(n.get("raw_code", ""))
            chunks.append(Chunk(f"test:{n['name']}", "test", f"dbt data test {n['name']}",
                                f"Singular dbt data test {n['name']}: {purpose}", "dbt manifest"))

    for uid, s in sorted(manifest["sources"].items()):
        fq = f"{s['schema']}.{s['name']}"
        fresh = s.get("freshness") or {}
        body = f"Source table {fq} (loaded by the Airflow DAG). {_clean(s.get('description'))}"
        if fresh.get("error_after", {}).get("count"):
            body += (f" Freshness on {s.get('loaded_at_field')}: warn after "
                     f"{fresh['warn_after']['count']} {fresh['warn_after']['period']}s, error after "
                     f"{fresh['error_after']['count']} {fresh['error_after']['period']}s.")
        chunks.append(Chunk(f"source:{fq}", "source", f"Source {fq}", body, "dbt manifest"))
        for col, c in s["columns"].items():
            chunks.append(Chunk(f"column:{fq}.{col}", "column", f"Column {fq}.{col}",
                                f"Column {col} of source {fq} (type {col_type(uid, col)}). "
                                f"{_clean(c.get('description'))}", "dbt manifest"))

    for uid, d in manifest.get("docs", {}).items():
        if uid.startswith("doc.sales_dbt."):
            chunks.append(Chunk(f"doc:dbt#{d['name']}", "doc", "dbt project overview",
                                _clean(d["block_contents"]), "dbt docs"))
    return chunks


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def markdown_chunks(path: Path) -> list[Chunk]:
    """Split at ## and ### headings; keep '# Doc > ## Section > ### Sub' as the chunk title."""
    stem = path.name
    heads = {"h1": stem, "h2": "", "h3": ""}
    buf: list[str] = []
    chunks: list[Chunk] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        buf.clear()
        if not body:
            return
        heading = heads["h3"] or heads["h2"] or "intro"
        title = " > ".join(p for p in heads.values() if p)
        base = f"doc:{stem}#{_slug(heading)}"
        parts, cur = [], ""
        for para in re.split(r"\n\s*\n", body):
            if cur and len(cur) + len(para) > MAX_DOC_CHARS:
                parts.append(cur)
                cur = ""
            cur = f"{cur}\n\n{para}" if cur else para
        parts.append(cur)
        for i, part in enumerate(parts):
            chunks.append(Chunk(base if i == 0 else f"{base}-{i + 1}", "doc", title, part.strip(), stem))

    for line in path.read_text().splitlines():
        if line.startswith(("# ", "## ", "### ")):
            flush()
            level, text = line.split(" ", 1)
            depth = len(level)
            heads[f"h{depth}"] = text.strip()
            for deeper in range(depth + 1, 4):
                heads[f"h{deeper}"] = ""
        else:
            buf.append(line)
    flush()
    return chunks


def build_corpus(manifest: Path, catalog: Path | None, markdown_docs: tuple[Path, ...]) -> list[Chunk]:
    if not manifest.exists():
        raise FileNotFoundError(f"{manifest} not found: run the DAG (dbt docs generate) first")
    chunks = dbt_chunks(manifest, catalog)
    for p in markdown_docs:
        chunks.extend(markdown_chunks(p))
    ids = [c.id for c in chunks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate chunk ids would make retrieval eval ambiguous: {sorted(dupes)}")
    return chunks


def corpus_version(chunks: list[Chunk]) -> str:
    digest = hashlib.sha256()
    for c in sorted(chunks, key=lambda c: c.id):
        digest.update(f"{c.id}:{c.content_hash}\n".encode())
    return digest.hexdigest()[:16]
