from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from app.model_adapter import ModelUsage
from app.model_budget import BudgetConfigurationError, BudgetExhausted, BudgetLedger
from app.settings import Settings


def _settings(**overrides: object) -> Settings:
    values = {
        "model_name": "test-model",
        "model_max_prompt_tokens": 1,
        "model_max_output_tokens": 1,
        "model_input_usd_per_million_tokens": 1000.0,
        "model_output_usd_per_million_tokens": 1000.0,
        "model_request_budget_usd": 0.002,
        "model_process_budget_usd": 0.004,
    }
    return Settings.model_validate(values | overrides)


def test_exact_budget_boundary_and_reconciliation() -> None:
    ledger = BudgetLedger()
    first = ledger.reserve(_settings())
    second = ledger.reserve(_settings())
    assert first.reserved_usd == Decimal("0.002")
    assert ledger.charged_usd == Decimal("0.004")
    with pytest.raises(BudgetExhausted):
        ledger.reserve(_settings())

    assert first.settle(ModelUsage(1, 0)) == Decimal("0.001")
    assert ledger.charged_usd == Decimal("0.003")
    # 0.003 + 0.002 exceeds the cap.
    with pytest.raises(BudgetExhausted):
        ledger.reserve(_settings())
    assert second.settle(ModelUsage(0, 0)) == Decimal(0)
    assert ledger.reserve(_settings()).reserved_usd == Decimal("0.002")


def test_per_request_cap_blocks_before_process_reservation() -> None:
    ledger = BudgetLedger()
    with pytest.raises(BudgetExhausted):
        ledger.reserve(_settings(model_request_budget_usd=0.001))
    assert ledger.charged_usd == Decimal(0)


def test_concurrent_reservations_cannot_overspend() -> None:
    ledger = BudgetLedger()

    def attempt(_: int) -> bool:
        try:
            ledger.reserve(_settings()).forfeit()
        except BudgetExhausted:
            return False
        return True

    with ThreadPoolExecutor(max_workers=16) as pool:
        admitted = list(pool.map(attempt, range(40)))
    assert sum(admitted) == 2
    assert ledger.charged_usd == Decimal("0.004")


def test_forfeit_and_double_settlement_keep_full_charge() -> None:
    ledger = BudgetLedger()
    reservation = ledger.reserve(_settings())
    reservation.forfeit()
    assert ledger.charged_usd == Decimal("0.002")
    with pytest.raises(RuntimeError):
        reservation.forfeit()
    with pytest.raises(RuntimeError):
        reservation.settle(ModelUsage(0, 0))
    assert ledger.charged_usd == Decimal("0.002")


@pytest.mark.parametrize("usage", [
    ModelUsage(-1, 0), ModelUsage(0, 2), ModelUsage(True, 0),
    ModelUsage(0, 1.5), None,
])
def test_invalid_usage_forfeits_reservation(usage: object) -> None:
    ledger = BudgetLedger()
    reservation = ledger.reserve(_settings())
    with pytest.raises(ValueError):
        reservation.settle(usage)  # type: ignore[arg-type]
    assert ledger.charged_usd == Decimal("0.002")
    with pytest.raises(RuntimeError):
        reservation.forfeit()


def test_changed_model_or_prices_cannot_reset_live_ledger() -> None:
    ledger = BudgetLedger()
    ledger.reserve(_settings()).forfeit()
    for changed in (
        _settings(model_name="another-model"),
        _settings(model_input_usd_per_million_tokens=500.0),
    ):
        with pytest.raises(BudgetConfigurationError):
            ledger.reserve(changed)
    assert ledger.charged_usd == Decimal("0.002")
    with pytest.raises(BudgetExhausted):
        ledger.reserve(_settings(model_process_budget_usd=0.002))
    with pytest.raises(BudgetExhausted):
        ledger.reserve(_settings(model_process_budget_usd=0.004))


def test_missing_prices_or_model_fail_closed() -> None:
    ledger = BudgetLedger()
    with pytest.raises(BudgetConfigurationError):
        ledger.reserve(Settings())
    with pytest.raises(BudgetConfigurationError):
        ledger.reserve(_settings(model_input_usd_per_million_tokens=None))
    assert ledger.charged_usd == Decimal(0)
