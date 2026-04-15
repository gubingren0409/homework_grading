from src.worker.main import _compute_effective_batch_concurrency, _should_emit_batch_progress


def test_compute_effective_batch_concurrency_clamps_to_item_count() -> None:
    assert _compute_effective_batch_concurrency(total_items=10, configured_concurrency=6) == 6
    assert _compute_effective_batch_concurrency(total_items=3, configured_concurrency=6) == 3
    assert _compute_effective_batch_concurrency(total_items=0, configured_concurrency=6) == 1


def test_should_emit_batch_progress_always_emits_final_tick() -> None:
    assert _should_emit_batch_progress(
        completed_count=10,
        total_count=10,
        last_emitted_count=8,
        last_emit_ts=100.0,
        now_ts=100.1,
    )

