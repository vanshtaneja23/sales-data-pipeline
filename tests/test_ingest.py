from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from sales_pipeline.ingest import SourceIntegrityError, download, read_raw
from sales_pipeline.sources import SOURCES, Source

CSV = b"Store,State\n1,HE\n2,\"HB,NI\"\n"


def _source(tmp_path, payload: bytes, *, sha: str | None = None, member: str | None = None) -> Source:
    upstream = tmp_path / "upstream.bin"
    upstream.write_bytes(payload)
    return Source(
        name="t", url=upstream.as_uri(), sha256=sha or hashlib.sha256(payload).hexdigest(),
        description="test", archive_member=member,
    )


def test_download_verifies_checksum(tmp_path):
    src = _source(tmp_path, CSV)
    path = download(src, tmp_path / "landing")
    assert path.read_bytes() == CSV


def test_checksum_mismatch_fails_and_leaves_no_file(tmp_path):
    src = _source(tmp_path, CSV, sha="0" * 64)
    with pytest.raises(SourceIntegrityError, match="sha256 mismatch"):
        download(src, tmp_path / "landing")
    assert not list((tmp_path / "landing").iterdir())


def test_cached_file_reused_only_when_checksum_matches(tmp_path):
    src = _source(tmp_path, CSV)
    landing = tmp_path / "landing"
    download(src, landing)
    (tmp_path / "upstream.bin").unlink()  # upstream gone: must be served from cache
    assert download(src, landing).read_bytes() == CSV

    (landing / "t.bin").write_bytes(b"tampered")  # cache corrupted: must re-download (and fail here)
    with pytest.raises(OSError):
        download(src, landing)


def test_read_raw_detects_zip_by_magic_bytes(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("inner.csv", CSV)
    src = _source(tmp_path, buf.getvalue(), member="inner.csv")
    df = read_raw(src, download(src, tmp_path / "landing"))
    assert df["State"].tolist() == ["HE", "HB,NI"]  # quoted comma survives


def test_read_raw_keeps_empty_strings(tmp_path):
    src = _source(tmp_path, b"a,b\n1,\n")
    df = read_raw(src, download(src, tmp_path / "landing"))
    assert df.loc[0, "b"] == ""


def test_all_sources_pinned_to_commits():
    for s in SOURCES.values():
        assert len(s.sha256) == 64
        assert "/main/" not in s.url and "/master/" not in s.url, f"{s.name} not pinned to a commit"
