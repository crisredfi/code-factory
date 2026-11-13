"""Which reviewers a job runs: defaults, intake adjustments, labels, and the per-job override."""
from __future__ import annotations

from .util import FactoryError


def review_names(st: dict, cfg: dict) -> list[str]:
    rv = cfg["reviewers"]
    catalog = rv["catalog"]
    if st.get("reviewers_override") is not None:
        chosen = set(st["reviewers_override"])
    else:
        chosen = set(rv["defaults"]) | set(st.get("intake_add", []))
        chosen -= set(st.get("intake_drop", []))
        for name, label in rv.get("optional_review_labels", {}).items():
            if label in st.get("labels", []):
                chosen.add(name)
    chosen |= set(rv["always"])
    unknown = chosen - set(catalog)
    if unknown:
        raise FactoryError(f"unknown reviewers: {', '.join(sorted(unknown))} (catalog: {', '.join(catalog)})")
    return [n for n in catalog if n in chosen]
