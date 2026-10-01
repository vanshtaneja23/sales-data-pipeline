"""Download pinned sources, verify their checksum, and read them as untyped (all-string) frames.

Reading everything as strings is deliberate: type coercion happens in one place (clean.py)
where a bad value raises with context, instead of pandas guessing dtypes per chunk.
"""

from __future__ import annotations

import hashlib
import io
import logging
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

from sales_pipeline.sources import Source

log = logging.getLogger(__name__)

ZIP_MAGIC = b"PK\x03\x04"


class SourceIntegrityError(RuntimeError):
    """Downloaded bytes don't match the pinned checksum (upstream changed or transfer corrupted)."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(source: Source, landing_dir: Path, timeout: int = 120) -> Path:
    """Fetch `source` into `landing_dir`, reusing a cached copy only if its checksum still matches."""
    landing_dir.mkdir(parents=True, exist_ok=True)
    target = landing_dir / f"{source.name}.bin"

    if target.exists() and sha256_of(target) == source.sha256:
        log.info("source=%s cache_hit path=%s", source.name, target)
        return target

    tmp = target.with_suffix(".part")
    log.info("source=%s downloading url=%s", source.name, source.url)
    with urllib.request.urlopen(source.url, timeout=timeout) as resp, tmp.open("wb") as out:  # noqa: S310 (pinned https URL)
        while chunk := resp.read(1 << 20):
            out.write(chunk)

    actual = sha256_of(tmp)
    if actual != source.sha256:
        tmp.unlink(missing_ok=True)
        raise SourceIntegrityError(
            f"{source.name}: sha256 mismatch (expected {source.sha256}, got {actual}). "
            "Upstream file changed; review it and update the pin in sources.py deliberately."
        )
    tmp.replace(target)
    return target


def read_raw(source: Source, path: Path) -> pd.DataFrame:
    """Read a landed file into an all-string DataFrame. Detects zip by magic bytes, not extension."""
    payload = path.read_bytes()
    if payload[:4] == ZIP_MAGIC:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            member = source.archive_member or zf.namelist()[0]
            payload = zf.read(member)
    # keep_default_na=False: an empty cell stays "" so cleaning decides what "missing" means.
    return pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False)
