"""Registry of upstream sources.

Each source is pinned to a git commit and a SHA-256 of the exact bytes we tested against,
so an upstream edit can never silently change what the pipeline ingests.

Provenance: the Rossmann files are the public Kaggle "Rossmann Store Sales" competition data
(train.csv, store.csv); store_states.csv is the community-published store -> German state
mapping from the 3rd-place solution (entron/entity-embedding-rossmann). We fetch from GitHub
mirrors because Kaggle downloads require authentication.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    sha256: str
    description: str
    archive_member: str | None = None  # set when the file is a zip archive


_KAGGLE_MIRROR = (
    "https://raw.githubusercontent.com/Khayati1/Rossman_Store_Sales_Kaggle_Dataset/"
    "d1f4610d4ed6be7a55ffe5519b430ab4878e6abd"
)
_STATES_REPO = (
    "https://raw.githubusercontent.com/entron/entity-embedding-rossmann/"
    "39b137968f75ac065d62f4703a3a42263858a169"
)

SOURCES: dict[str, Source] = {
    "sales": Source(
        name="sales",
        # Despite the .csv name, this mirror stores a zip archive containing train.csv.
        url=f"{_KAGGLE_MIRROR}/train.csv",
        sha256="9221c78cb0d64a9a438eff073323ce57ab0653dc0a17c5f10eda84dc27c6f726",
        archive_member="train.csv",
        description="Daily sales per store, 2013-01-01..2015-07-31 (Kaggle train.csv)",
    ),
    "stores": Source(
        name="stores",
        url=f"{_KAGGLE_MIRROR}/store.csv",
        sha256="f56bd124a2849489e6bbb5c000f5fc9640204355e316475c918ae4d089afb344",
        description="Store reference: type, assortment, competition, Promo2 (Kaggle store.csv)",
    ),
    "store_states": Source(
        name="store_states",
        url=f"{_STATES_REPO}/store_states.csv",
        sha256="86df632ad7469a4ed961df9844ff8a15ef9cbcd545514945a7291ccfb32b9797",
        description="Store -> German federal state mapping (community dataset)",
    ),
}
