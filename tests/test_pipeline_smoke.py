"""Integration smoke test (owner: P2 as integration tester).
The whole graph must run end to end offline on the fixture. Run before every merge to main."""
from core import config, storage
from core.schemas import CheckedCard, ScoredItem


def test_graph_runs_end_to_end_offline():
    from graphs.batch_graph import graph

    state = graph.invoke({})
    assert state["opportunities"], "collector produced nothing"
    assert len(state["gates"]) == len(state["opportunities"])
    assert len(state["top"]) <= config.TOP_N
    assert len(state["cards"]) == len(state["top"])
    for c in state["checked"]:
        CheckedCard.model_validate(c)
    rows = storage.read_json(f"scored/{config.AGENCY}/latest.json")
    assert len(rows) == state["published"]


def test_scores_are_sorted_and_bounded():
    from graphs.batch_graph import graph

    state = graph.invoke({})
    scores = [ScoredItem(**s).score for s in state["scored"]]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 100 for s in scores)


def test_pipeline_main_writes_run_log():
    import pipeline

    assert pipeline.main() == 0
    log = storage.read_json("runs/latest.json")
    assert log["status"] == "succeeded"
    assert log["counts"]["opportunities"] > 0
