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
    assert guard.used_today() == 2
