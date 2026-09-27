from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from app.model_adapter import ModelUsage, provider_input_token_ceiling
from app.model_budget import BudgetConfigurationError, BudgetExhausted, BudgetLedger
from app.settings import Settings


def _settings(**overrides: object) -> Settings:
    base = Settings(
        model_name="test-model",
        model_max_prompt_tokens=1,
        model_max_output_tokens=1,
        model_input_usd_per_million_tokens=1.0,
        model_output_usd_per_million_tokens=1.0,
    )
    cost = (Decimal(provider_input_token_ceiling(base)) + 1) / Decimal(1_000_000)
    return Settings.model_validate(base.model_dump() | {
        "model_request_budget_usd": float(cost),
        "model_process_budget_usd": float(2 * cost),
    } | overrides)


def _cost() -> Decimal:
    return (Decimal(provider_input_token_ceiling(_settings())) + 1) / Decimal(1_000_000)


def test_exact_budget_boundary_and_reconciliation() -> None:
    ledger = BudgetLedger()
    first = ledger.reserve(_settings())
    second = ledger.reserve(_settings())
    cost = _cost()
    assert first.reserved_usd == cost
    assert ledger.charged_usd == 2 * cost
    with pytest.raises(BudgetExhausted):
        ledger.reserve(_settings())

    assert first.settle(ModelUsage(1, 0)) == Decimal("0.000001")
    assert ledger.charged_usd == cost + Decimal("0.000001")
    with pytest.raises(BudgetExhausted):
        ledger.reserve(_settings())
    assert second.settle(ModelUsage(0, 0)) == Decimal(0)
    assert ledger.reserve(_settings()).reserved_usd == cost


def test_per_request_cap_blocks_before_process_reservation() -> None:
    ledger = BudgetLedger()
    with pytest.raises(BudgetExhausted):
        ledger.reserve(_settings(model_request_budget_usd=float(_cost() - Decimal("0.000001"))))
    assert ledger.charged_usd == Decimal(0)


def test_provider_overhead_blocks_near_prompt_only_cap() -> None:
    ledger = BudgetLedger()
    prompt_only = Decimal(2) / Decimal(1_000_000)
    with pytest.raises(BudgetExhausted):
        ledger.reserve(_settings(model_request_budget_usd=float(prompt_only)))
    assert ledger.charged_usd == Decimal(0)


def test_usage_up_to_full_provider_ceiling_settles() -> None:
    settings = _settings()
    ledger = BudgetLedger()
    reservation = ledger.reserve(settings)
    ceiling = provider_input_token_ceiling(settings)
    assert ceiling > settings.model_max_prompt_tokens
    assert reservation.settle(ModelUsage(ceiling, 1)) == reservation.reserved_usd
    assert ledger.charged_usd == reservation.reserved_usd


def test_usage_over_provider_ceiling_forfeits_full_reservation() -> None:
    settings = _settings()
    ledger = BudgetLedger()
    reservation = ledger.reserve(settings)
    with pytest.raises(ValueError):
        reservation.settle(ModelUsage(provider_input_token_ceiling(settings) + 1, 0))
    assert ledger.charged_usd == reservation.reserved_usd


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
    assert ledger.charged_usd == 2 * _cost()


def test_forfeit_and_double_settlement_keep_full_charge() -> None:
    ledger = BudgetLedger()
    reservation = ledger.reserve(_settings())
    reservation.forfeit()
    assert ledger.charged_usd == _cost()
    with pytest.raises(RuntimeError):
        reservation.forfeit()
    with pytest.raises(RuntimeError):
        reservation.settle(ModelUsage(0, 0))
    assert ledger.charged_usd == _cost()


@pytest.mark.parametrize("usage", [
    ModelUsage(-1, 0), ModelUsage(0, 2), ModelUsage(True, 0),
    ModelUsage(0, 1.5), None,
])
def test_invalid_usage_forfeits_reservation(usage: object) -> None:
    ledger = BudgetLedger()
    reservation = ledger.reserve(_settings())
    with pytest.raises(ValueError):
        reservation.settle(usage)  # type: ignore[arg-type]
    assert ledger.charged_usd == _cost()
    with pytest.raises(RuntimeError):
        reservation.forfeit()


def test_changed_model_or_prices_cannot_reset_live_ledger() -> None:
    ledger = BudgetLedger()
    ledger.reserve(_settings()).forfeit()
    for changed in (
        _settings(model_name="another-model"),
        _settings(model_input_usd_per_million_tokens=0.5),
    ):
        with pytest.raises(BudgetConfigurationError):
            ledger.reserve(changed)
    assert ledger.charged_usd == _cost()
    with pytest.raises(BudgetExhausted):
        ledger.reserve(_settings(model_process_budget_usd=float(_cost())))
    with pytest.raises(BudgetExhausted):
        ledger.reserve(_settings(model_process_budget_usd=float(2 * _cost())))


def test_missing_prices_or_model_fail_closed() -> None:
    ledger = BudgetLedger()
    with pytest.raises(BudgetConfigurationError):
        ledger.reserve(Settings())
    with pytest.raises(BudgetConfigurationError):
        ledger.reserve(_settings(model_input_usd_per_million_tokens=None))
    assert ledger.charged_usd == Decimal(0)
