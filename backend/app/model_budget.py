"""Process-local, conservative spending admission for model requests.

The caller owns one ledger for the lifetime of the local application process.
Hosted, multi-instance deployment requires a shared atomic ledger instead.
"""

from __future__ import annotations

from decimal import Decimal
from threading import RLock

from app.model_adapter import ModelUsage
from app.settings import Settings

_MILLION = Decimal(1_000_000)


class BudgetExhausted(Exception):
    """A request cannot fit within the configured spending limits."""


class BudgetConfigurationError(Exception):
    """An active ledger cannot safely accept changed model pricing."""


def _money(value: float | None) -> Decimal:
    if value is None:
        raise BudgetConfigurationError("model pricing is unavailable")
    amount = Decimal(str(value))
    if not amount.is_finite() or amount <= 0:
        raise BudgetConfigurationError("model pricing must be positive and finite")
    return amount


class BudgetLedger:
    """Reserve worst-case spend atomically, then settle or forfeit once."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._charged = Decimal(0)
        self._identity: tuple[str, Decimal, Decimal] | None = None
        self._process_limit: Decimal | None = None

    @property
    def charged_usd(self) -> Decimal:
        """Settled and outstanding reservations charged to this process."""
        with self._lock:
            return self._charged

    def reserve(self, settings: Settings) -> BudgetReservation:
        """Charge the full allowed prompt and output before any network call."""
        if settings.model_name is None:
            raise BudgetConfigurationError("model name is unavailable")
        input_rate = _money(settings.model_input_usd_per_million_tokens)
        output_rate = _money(settings.model_output_usd_per_million_tokens)
        identity = (settings.model_name, input_rate, output_rate)
        worst_case = (
            Decimal(settings.model_max_prompt_tokens) * input_rate
            + Decimal(settings.model_max_output_tokens) * output_rate
        ) / _MILLION
        request_limit = Decimal(str(settings.model_request_budget_usd))
        process_limit = Decimal(str(settings.model_process_budget_usd))
        with self._lock:
            if self._identity is not None and identity != self._identity:
                raise BudgetConfigurationError("model or prices changed during this process")
            # A reload cannot increase the spending ceiling of an active ledger.
            effective_limit = min(self._process_limit, process_limit) if self._process_limit is not None else process_limit
            self._process_limit = effective_limit
            if worst_case > request_limit or self._charged + worst_case > effective_limit:
                raise BudgetExhausted("model budget exhausted")
            self._identity = identity
            self._charged += worst_case
            return BudgetReservation(self, worst_case, input_rate, output_rate,
                                     settings.model_max_prompt_tokens, settings.model_max_output_tokens)


class BudgetReservation:
    """One reserved request. Uncertain failures consume the full reservation."""

    def __init__(
        self,
        ledger: BudgetLedger,
        reserved: Decimal,
        input_rate: Decimal,
        output_rate: Decimal,
        max_input_tokens: int,
        max_output_tokens: int,
    ) -> None:
        self._ledger = ledger
        self.reserved_usd = reserved
        self._input_rate = input_rate
        self._output_rate = output_rate
        self._max_input_tokens = max_input_tokens
        self._max_output_tokens = max_output_tokens
        self._finished = False

    def settle(self, usage: ModelUsage) -> Decimal:
        """Replace the reservation with bounded, valid provider usage."""
        with self._ledger._lock:
            if self._finished:
                raise RuntimeError("reservation already finished")
            self._finished = True
            if (
                not isinstance(usage, ModelUsage)
                or type(usage.input_tokens) is not int
                or type(usage.output_tokens) is not int
                or not 0 <= usage.input_tokens <= self._max_input_tokens
                or not 0 <= usage.output_tokens <= self._max_output_tokens
            ):
                # Usage cannot be trusted, so the full reservation stays charged.
                raise ValueError("model usage is invalid or exceeds reserved bounds")
            actual = (
                Decimal(usage.input_tokens) * self._input_rate
                + Decimal(usage.output_tokens) * self._output_rate
            ) / _MILLION
            self._ledger._charged -= self.reserved_usd - actual
            return actual

    def forfeit(self) -> None:
        """Keep the full charge after a timeout or uncertain outcome."""
        with self._ledger._lock:
            if self._finished:
                raise RuntimeError("reservation already finished")
            self._finished = True
