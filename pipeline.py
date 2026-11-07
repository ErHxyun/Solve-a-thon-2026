"""Daily job entry point (MODE=job). Runs the batch graph once and writes a run log.

Usage:  python pipeline.py
Exit code 0 = succeeded, 1 = failed (Azure marks the job execution Failed).
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone

from core import storage
from core.schemas import RunLog
from graphs.batch_graph import graph


def main() -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log = RunLog(run_id=run_id, started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        state = graph.invoke({}, config={"recursion_limit": 25})
        log.counts = {
            "opportunities": len(state.get("opportunities", [])),
            "passed_gates": sum(1 for g in state.get("gates", []) if g["passed"]),
            "scored": len(state.get("scored", [])),
            "cards": len(state.get("cards", [])),
            "cards_retried": sum(1 for n in state.get("attempts", {}).values() if n > 1),
            "low_confidence_cards": sum(1 for c in state.get("checked", []) if c["confidence"] == "Low"),
            "alerts": len(state.get("alerts", [])),
        }
        log.status = "succeeded"
        return_code = 0
    except Exception as exc:
        log.errors.append(f"{type(exc).__name__}: {exc}")
        log.status = "failed"
        traceback.print_exc()
        return_code = 1
    log.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    storage.write_json(f"runs/{run_id}.json", log.model_dump())
    storage.write_json("runs/latest.json", log.model_dump())
    print(log.model_dump_json(indent=2))
    return return_code


if __name__ == "__main__":
    sys.exit(main())
