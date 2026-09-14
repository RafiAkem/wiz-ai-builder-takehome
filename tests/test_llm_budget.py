import threading

from app import llm_budget
from app.llm_budget import guard


def test_per_ip_limit_blocks_and_reset_frees(monkeypatch):
    monkeypatch.setenv("LLM_RATE_PER_IP_HOUR", "2")
    monkeypatch.setenv("LLM_RATE_GLOBAL_DAY", "800")
    assert [guard.try_consume("203.0.113.9") for _ in range(3)] == [True, True, False]
    assert guard.try_consume("198.51.100.4") is True  # every IP gets its own window
    assert guard.used_today() == 3

    guard.reset()
    assert guard.used_today() == 0
    assert guard.try_consume("203.0.113.9") is True


def test_global_daily_limit_blocks_regardless_of_ip(monkeypatch):
    monkeypatch.setenv("LLM_RATE_PER_IP_HOUR", "30")
    monkeypatch.setenv("LLM_RATE_GLOBAL_DAY", "2")
    assert guard.try_consume("203.0.113.9") is True
    assert guard.try_consume("198.51.100.4") is True
    assert guard.try_consume("192.0.2.7") is False


def test_gate_for_charges_the_bound_client_ip(monkeypatch):
    monkeypatch.setenv("LLM_RATE_PER_IP_HOUR", "1")
    gate = guard.gate_for("203.0.113.9")
    assert gate.consume() is True
    assert gate.consume() is False, "second attempt in the window is refused"
    assert guard.try_consume("198.51.100.4") is True  # other IPs are unaffected


def test_try_consume_is_atomic_under_threading(monkeypatch):
    """Concurrent consumers must never exceed the global cap or lose units."""
    monkeypatch.setenv("LLM_RATE_PER_IP_HOUR", "1000")
    monkeypatch.setenv("LLM_RATE_GLOBAL_DAY", "50")

    def hammer(ip: int):
        for _ in range(20):
            guard.try_consume(f"203.0.113.{ip}")

    threads = [threading.Thread(target=hammer, args=(ip,)) for ip in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert guard.used_today() == 50, "count is exactly the cap: no lost, no duplicated units"


def test_prune_stale_reclaims_only_dead_windows(monkeypatch):
    start = 1_000_000.0
    assert guard.try_consume("203.0.113.9", now=start) is True
    assert guard.try_consume("203.0.113.9", now=start + llm_budget.HOUR - 1) is True
    # The second hit is still inside its hour window, so the bucket must survive.
    assert guard.prune_stale(now=start + llm_budget.HOUR + 1) == 0
    # Once every timestamp has aged out of the window, the bucket is reclaimable.
    assert guard.prune_stale(now=start + 2 * llm_budget.HOUR) == 1
    assert guard.try_consume("203.0.113.9", now=start + 2 * llm_budget.HOUR) is True, (
        "reclaiming an empty bucket must not carry over stale hits"
    )


def test_stale_buckets_are_swept_automatically(monkeypatch):
    start = 2_000_000.0
    for number in range(10):
        guard.try_consume(f"198.51.100.{number}", now=start)
    monkeypatch.setattr(guard, "SWEEP_THRESHOLD", 5)
    # The next call crosses the threshold; its sweep prunes every window that has
    # aged past the hour cutoff, so the map cannot grow without bound.
    assert guard.try_consume("203.0.113.1", now=start + llm_budget.HOUR + 1) is True
    assert len(guard._per_ip) <= 5
