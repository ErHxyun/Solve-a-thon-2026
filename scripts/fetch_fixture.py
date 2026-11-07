"""Refresh tests/fixtures/grants_sample_raw.json with real Grants.gov records.

Usage:  python scripts/fetch_fixture.py [rows_per_keyword]
The fixture is committed so everyone (and the tests) can work offline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.collector import fetch_detail, search  # noqa: E402
from core import config  # noqa: E402

# Mix of DNCR-relevant topics and deliberate noise (NIH research, BLM, health) to test the gates
KEYWORDS = ["historic preservation", "outdoor recreation", "museum", "library services", "arts", "trails",
            "battlefield", "disaster recovery", "biomedical research", "Bureau of Land Management", "school bus"]


def main(rows: int = 4) -> None:
    ids: dict[str, None] = {}
    for kw in KEYWORDS:
        for hit in search(kw, rows=rows):
            ids[hit["id"]] = None
    details = []
    for opp_id in ids:
        try:
            details.append(fetch_detail(opp_id))
        except Exception as exc:
            print(f"skip {opp_id}: {exc}")
    out = config.FIXTURES_DIR / "grants_sample_raw.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(details, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(details)} records to {out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 4)
