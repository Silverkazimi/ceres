"""Decimal harvest routing, matching the current official Trade Hub pool math.

No function in this module signs, broadcasts, or treats projected harvest as received.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, localcontext
from typing import Any, Mapping

from . import resources as _res

QUANTUM = Decimal("0.001")
ZERO = Decimal(0)
ONE = Decimal(1)
INVENTORY = "inventory"
COST_TOKENS = _res.symbols("cost_input")  # registry view: GRAIN WOOD STONE IRON
DEC_SOURCES = frozenset({"wallet", "swap_half"})


class RoutingError(ValueError):
    pass


class ProceedsTooSmall(RoutingError):
    """A dependent step's confirmed proceeds cannot fund its operation."""


def amount(value: Any, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise RoutingError("Missing or invalid amount")
    raw = str(value)
    if len(raw) > MAX_AMOUNT_CHARS:  # S1 / L1-02: bound the input before any parsing or fixed-point formatting
        raise RoutingError("Invalid amount")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise RoutingError("Invalid amount") from exc
    exponent = number.as_tuple().exponent if number.is_finite() else 0
    if not isinstance(exponent, int) or exponent < -MAX_AMOUNT_SCALE or exponent > MAX_AMOUNT_CHARS:
        raise RoutingError("Invalid amount")  # e.g. 0e-1000000000 would format to a gigabyte of zeros
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        raise RoutingError("Amount must be finite and nonnegative")
    if number > Decimal("1000000000000000"):
        raise RoutingError("Amount exceeds supported precision")
    return number


def floor(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_DOWN)


def rounded(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_HALF_UP)


def ceiling(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_CEILING)


def text(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class Pool:
    id: int
    symbol: str
    resource: Decimal
    dec: Decimal
    shares: Decimal
    external: bool = False

    @classmethod
    def parse(cls, row: Mapping[str, Any]) -> "Pool":
        identifier = row.get("id")
        symbol = row.get("token_symbol")
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
            raise RoutingError("Invalid pool identity")
        if not isinstance(symbol, str) or not symbol.isascii() or not symbol.isupper() or not symbol.isalnum():
            raise RoutingError("Invalid pool symbol")
        external = row.get("is_external_resource")
        if not isinstance(external, bool):
            raise RoutingError("Pool settlement type is unknown")
        return cls(identifier, symbol, amount(row.get("resource_quantity"), positive=True),
                   amount(row.get("dec_quantity"), positive=True), amount(row.get("total_shares"), positive=True), external)

    def factor(self, double_leg: bool) -> Decimal:
        return Decimal("0.995") if self.symbol == "SPS" else Decimal("0.95" if double_leg else "0.9")


def output_for_input(value: Decimal, input_reserve: Decimal, output_reserve: Decimal, factor: Decimal) -> Decimal:
    if value <= 0 or input_reserve <= 0 or output_reserve <= 0:
        raise RoutingError("The pool cannot quote this amount")
    with localcontext() as ctx:
        ctx.prec = 50
        effective = value * factor
        result = rounded(effective * output_reserve / (input_reserve + effective))
    if result <= 0 or result >= output_reserve:
        raise RoutingError("The requested swap has no spendable output")
    return result


@dataclass(frozen=True)
class SwapQuote:
    source_symbol: str
    destination_symbol: str
    input: Decimal
    output: Decimal
    minimum: Decimal
    intermediate_dec: Decimal = ZERO
    external: bool = False
    price_impact_large: bool = False

    def public(self) -> dict[str, Any]:
        return {"source_symbol": self.source_symbol, "destination_symbol": self.destination_symbol,
                "input": text(self.input), "output": text(self.output), "minimum": text(self.minimum),
                "intermediate_dec": text(self.intermediate_dec), "external": self.external,
                "price_impact_large": self.price_impact_large}


def validate_slippage(value: Any) -> Decimal:
    tolerance = amount(value)
    if not Decimal("0.5") <= tolerance <= Decimal("25"):
        raise RoutingError("Slippage must be between 0.5 and 25 percent")
    return tolerance


def swap_quote(source: str, destination: str, value: Any, pools: Mapping[str, Pool], slippage: Any) -> SwapQuote:
    quantity = amount(value, positive=True)
    if quantity != floor(quantity):
        raise RoutingError("Use at most three decimal places")
    tolerance = validate_slippage(slippage)
    first = pools.get(source) if source != "DEC" else None
    second = pools.get(destination) if destination != "DEC" else None
    if (source != "DEC" and first is None) or (destination != "DEC" and second is None) or source == destination == "DEC":
        raise RoutingError("No supported pool route exists")
    intermediate = ZERO
    if first and second:
        intermediate = output_for_input(quantity, first.resource, first.dec, first.factor(True))
        if first.id == second.id:
            # The first leg changes this same pool before the return leg. Its fee is
            # split between burning and retained liquidity with official floor rounding.
            fee = quantity * (ONE - first.factor(True))
            fee_half = floor(fee / 2)
            resource_added = quantity - fee_half
            dec_reserve = rounded(second.dec - intermediate)
            resource_reserve = rounded(second.resource + resource_added)
        else:
            dec_reserve, resource_reserve = second.dec, second.resource
        result = output_for_input(intermediate, dec_reserve, resource_reserve, second.factor(True))
        impact = first.id != second.id and (quantity > first.resource / 5 or intermediate > second.dec / 5)
    elif first:
        result = output_for_input(quantity, first.resource, first.dec, first.factor(False))
        impact = quantity > first.resource / 5
    else:
        result = output_for_input(quantity, second.dec, second.resource, second.factor(False))
        impact = quantity > second.dec / 5
    minimum = floor(result * (ONE - tolerance / 100))
    if minimum <= 0:
        raise RoutingError("Protected output is below one resource unit of precision")
    return SwapQuote(source, destination, quantity, result, minimum, intermediate,
                     bool((first and first.external) or (second and second.external)), impact)


def input_for_delivery(symbol: str, needed: Any, maximum: Any, pools: Mapping[str, Pool], slippage: Any,
                       *, source_symbol: str | None = None) -> SwapQuote | None:
    """Smallest three-decimal input whose protected output covers the gap."""
    source = source_symbol or symbol
    target, limit = amount(needed, positive=True), floor(amount(maximum))
    if limit <= 0:
        return None
    try:
        largest = swap_quote(source, symbol, limit, pools, slippage)
    except RoutingError:
        return None
    if largest.minimum < target:
        return largest
    low, high = 1, int(limit / QUANTUM)
    while low < high:
        middle = (low + high) // 2
        try:
            candidate = swap_quote(source, symbol, Decimal(middle) * QUANTUM, pools, slippage)
            sufficient = candidate.minimum >= target
        except RoutingError:
            sufficient = False
        if sufficient:
            high = middle
        else:
            low = middle + 1
    return swap_quote(source, symbol, Decimal(low) * QUANTUM, pools, slippage)


def plain(value: Decimal) -> str:
    shown = text(value)
    return shown.rstrip("0").rstrip(".") if "." in shown else shown


def region_label(region: str) -> str:
    """Screens name a region by its number, never by its internal id ()."""
    tail = region.rsplit("-", 1)[-1]
    return f"Region {tail}" if region != INVENTORY and tail.isdigit() else "your wallet"


@dataclass
class Ledger:
    balances: dict[tuple[str, str], Decimal]
    reserves: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    harvested: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    allocated: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    harvested_allocated: dict[tuple[str, str], Decimal] = field(default_factory=dict)

    def available(self, region: str, symbol: str, *, harvest_only: bool = False) -> Decimal:
        key = (region, symbol)
        if key not in self.balances:
            raise RoutingError(f"Balance unavailable for {region} / {symbol}")
        used = self.allocated.get(key, ZERO)
        available = max(ZERO, self.balances[key] - self.reserves.get(key, ZERO) - used)
        if harvest_only:
            harvested_used = self.harvested_allocated.get(key, ZERO)
            available = min(available, max(ZERO, self.harvested.get(key, ZERO) - harvested_used))
        return floor(available)

    def allocate(self, region: str, symbol: str, value: Any, *, harvest_only: bool = False) -> Decimal:
        debit = amount(value, positive=True)
        if debit != floor(debit):
            raise RoutingError("Allocation has more than three decimal places")
        if debit > self.available(region, symbol, harvest_only=harvest_only):
            held = self.reserves.get((region, symbol), ZERO)
            if held > ZERO:
                raise RoutingError(f"{region_label(region)} keeps {plain(held)} {symbol} back for its next harvest cost, "
                                   f"so at most {plain(self.available(region, symbol, harvest_only=harvest_only))} {symbol} can be routed from it")
            raise RoutingError(f"Only {plain(self.available(region, symbol, harvest_only=harvest_only))} {symbol} is left to route from {region_label(region)}")
        key = (region, symbol)
        self.allocated[key] = self.allocated.get(key, ZERO) + debit
        if harvest_only:
            self.harvested_allocated[key] = self.harvested_allocated.get(key, ZERO) + debit
        return debit

    def allocate_many(self, debits: list[tuple[str, str, Any, bool]]) -> None:
        previous = dict(self.allocated)
        previous_harvested = dict(self.harvested_allocated)
        try:
            for region, symbol, value, harvest_only in debits:
                self.allocate(region, symbol, value, harvest_only=harvest_only)
        except Exception:
            self.allocated = previous
            self.harvested_allocated = previous_harvested
            raise


def region_reserves(summaries: list[dict[str, Any]], extra: Mapping[tuple[str, str], Any] | None = None) -> dict[tuple[str, str], Decimal]:
    reserves = {key: amount(value) for key, value in (extra or {}).items()}
    for row in summaries:
        uid = row.get("region_uid")
        if not isinstance(uid, str) or row.get("unknown", 0):
            raise RoutingError("A donor region has unknown harvest requirements")
        for symbol in COST_TOKENS:
            need = amount((row.get("cost_now") or {}).get(symbol, 0))
            rate = amount((row.get("cost_per_hour") or {}).get(symbol, 0))
            reserves[(uid, symbol)] = max(reserves.get((uid, symbol), ZERO), need + rate / 30)
    return reserves


def cover_shortfalls(ledger: Ledger, needs: Mapping[tuple[str, str], Any], pools: Mapping[str, Pool], slippage: Any) -> list[dict[str, Any]]:
    """Plan donors first. Only the uncovered remainder is eligible for a DEC buy."""
    result = []
    for (target, symbol), value in sorted(needs.items()):
        required = amount(value)
        key = (target, symbol)
        if key not in ledger.balances:
            raise RoutingError("Target regional balance is unknown")
        short = max(ZERO, required - ledger.balances[key])
        if not short:
            continue
        while short:
            candidates = []
            for donor, token in ledger.balances:
                if donor == target or token != symbol:
                    continue
                quote = input_for_delivery(symbol, short, ledger.available(donor, symbol), pools, slippage)
                if quote:
                    # ranks donors only; buying never competes with a valid donor.
                    candidates.append((max(ZERO, short - quote.minimum), quote.input - quote.minimum, donor, quote))
            if not candidates:
                break
            _remainder, _cost, donor, quote = min(candidates, key=lambda entry: entry[:3])
            ledger.allocate(donor, symbol, quote.input)
            result.append({"kind": "transfer", "source_region": donor, "destination_region": target, **quote.public()})
            short = max(ZERO, short - quote.minimum)
        if short:
            quote = input_for_delivery(symbol, short, ledger.available(INVENTORY, "DEC"), pools, slippage,
                                       source_symbol="DEC")
            if quote is None or quote.minimum < short:
                result.append({"kind": "buy_remainder", "destination_region": target, "symbol": symbol,
                               "needed": text(short), "status": "awaiting_funds",
                               "reason": "Not enough unreserved DEC to cover the remaining harvest cost"})
            else:
                ledger.allocate(INVENTORY, "DEC", quote.input)
                result.append({"kind": "buy_remainder", "source_region": INVENTORY,
                               "destination_region": target, "symbol": symbol, "needed": text(short),
                               "status": "ready", **quote.public()})
    return result


def allocation_quantity(rule: Mapping[str, Any], received: Decimal) -> Decimal:
    mode = rule.get("quantity_mode")
    raw = rule.get("quantity")
    if mode == "percent":
        digits = str(raw)
        if isinstance(raw, bool) or not digits.isascii() or not digits.isdecimal():
            raise RoutingError("Route percent must be a whole number from 1% to 100%")
        value = Decimal(digits)
        if not 1 <= value <= 100:
            raise RoutingError("Route percent must be a whole number from 1% to 100%")
        return floor(received * value / 100)
    if mode == "amount":
        return floor(amount(raw))
    raise RoutingError("Choose an amount or percentage allocation")


def validate_percent_allocation(rule: Mapping[str, Any]) -> Decimal:
    if rule.get("quantity_mode") == "amount":
        return amount(rule.get("quantity"))
    return allocation_quantity(rule, ZERO)


def liquidity_deposit(pool: Pool, resource_amount: Any) -> dict[str, Any]:
    resource = floor(amount(resource_amount, positive=True))
    if resource <= 0:
        raise RoutingError("Deposit is below supported precision")
    with localcontext() as ctx:
        ctx.prec = 50
        dec = rounded(resource * pool.dec / pool.resource)
        shares = floor(min(resource / pool.resource, dec / pool.dec) * pool.shares)
    if dec <= 0 or shares <= 0:
        raise RoutingError("Deposit would not create a spendable pool share")
    return {"symbol": pool.symbol, "pool_id": pool.id, "resource_amount": text(resource),
            "dec_amount": text(dec), "estimated_shares": text(shares), "external": pool.external}


def liquidity_dec_ceiling(deposit: Mapping[str, Any], slippage: Any) -> Decimal:
    tolerance = validate_slippage(slippage)
    return ceiling(amount(deposit.get("dec_amount"), positive=True) * (ONE + tolerance / 100))


def liquidity_funding_split(pool: Pool, resource_amount: Any, slippage: Any) -> dict[str, Any]:
    """Smallest sale whose protected DEC funds the post-swap LP ratio plus its slippage bound."""
    total = floor(amount(resource_amount, positive=True))
    tolerance = validate_slippage(slippage)
    units = int(total / QUANTUM)
    if units < 2:
        raise RoutingError("The reserved resource is too small to split between a swap and liquidity")

    def candidate(swap_units: int) -> tuple[SwapQuote, Decimal, Pool]:
        swap_input = Decimal(swap_units) * QUANTUM
        quote = swap_quote(pool.symbol, "DEC", swap_input, {pool.symbol: pool}, tolerance)
        fee_half = floor(swap_input * (ONE - pool.factor(False)) / 2)
        post_resource = rounded(pool.resource + swap_input - fee_half)
        post_dec = rounded(pool.dec - quote.output)
        if post_resource <= 0 or post_dec <= 0:
            raise RoutingError("The pool cannot fund this liquidity split")
        post_pool = Pool(pool.id, pool.symbol, post_resource, post_dec, pool.shares, pool.external)
        required = rounded((total - swap_input) * post_dec / post_resource)
        return quote, required, post_pool

    def protected_requirement(required: Decimal) -> Decimal:
        return ceiling(required * (ONE + tolerance / 100))

    try:
        largest_quote, largest_required, _largest_pool = candidate(units - 1)
    except RoutingError as exc:
        raise RoutingError("The pool cannot fund this liquidity split") from exc
    if largest_quote.minimum < protected_requirement(largest_required):
        raise RoutingError("The protected swap output cannot fund the liquidity deposit")
    low, high = 1, units - 1
    while low < high:
        middle = (low + high) // 2
        try:
            quote, required, _post_pool = candidate(middle)
            sufficient = quote.minimum >= protected_requirement(required)
        except RoutingError:
            sufficient = False
        if sufficient:
            high = middle
        else:
            low = middle + 1
    quote, required, post_pool = candidate(low)
    deposit = liquidity_deposit(post_pool, total - quote.input)
    if amount(deposit["dec_amount"]) != required or quote.minimum < protected_requirement(required):
        raise RoutingError("The protected swap output cannot fund the liquidity deposit")
    return {"total_resource": text(total), "swap_resource": text(quote.input),
            "deposit_resource": deposit["resource_amount"], "protected_dec": text(quote.minimum),
            "required_dec": text(required), "estimated_dec": text(quote.output),
            "estimated_surplus_dec": text(max(ZERO, quote.minimum - required)),
            "quote": quote.public(), "deposit": deposit}


MAX_RULES = 5000
MAX_AMOUNT_CHARS = 64   # longest amount text accepted from a client
MAX_AMOUNT_SCALE = 30   # most decimal places accepted from a client (the game uses 3)


def validate_rules(rules: Any, ledger: Ledger, pools: Mapping[str, Pool], sources: set[str]) -> None:
    if not isinstance(rules, list) or len(rules) > MAX_RULES:  # a request-size sanity bound, not a plan limit
        raise RoutingError("Invalid routing rules")
    identifiers = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise RoutingError("Invalid routing rule")
        identifier = rule.get("id")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 80 or identifier in identifiers:
            raise RoutingError("Routing rule IDs must be unique")
        identifiers.add(identifier)
        kind, source, symbol = rule.get("kind"), rule.get("source_region"), rule.get("symbol")
        if not isinstance(kind, str) or kind not in {"transfer", "sell", "liquidity"} or not isinstance(source, str) or not isinstance(symbol, str):
            raise RoutingError("Choose a resource, region and routing action")
        if source not in sources | {INVENTORY} or (source, symbol) not in ledger.balances:
            raise RoutingError("Choose a resource available in a selected harvest region or wallet inventory")
        if symbol != "DEC" and symbol not in pools:
            raise RoutingError("The selected resource has no supported pool")
        allocation_quantity(rule, ZERO)
        if kind == "liquidity":
            if symbol not in pools:
                raise RoutingError("Choose the resource side of a liquidity pool")
            dec_source = rule.get("dec_source")
            if dec_source is not None and (not isinstance(dec_source, str) or dec_source not in DEC_SOURCES):
                raise RoutingError("Choose wallet DEC or sell half first")
            continue
        if rule.get("dec_source") is not None:
            raise RoutingError("DEC source is only valid for liquidity routing")
        validate_slippage(rule.get("slippage", "2.5"))
        destination = symbol if kind == "transfer" else rule.get("destination_symbol")
        if not isinstance(destination, str) or destination not in set(pools) | {"DEC"}:
            raise RoutingError("Choose a supported output resource")
        target = INVENTORY if destination in {"DEC", "SPS", "VOUCHER"} else rule.get("destination_region")
        if not isinstance(target, str) or (target, destination) not in ledger.balances:
            raise RoutingError("Choose an owned destination region")
        if source == target and symbol == destination:
            raise RoutingError("Source and destination are identical")


def plan_after_harvest(ledger: Ledger, rules: list[dict[str, Any]], pools: Mapping[str, Pool], slippage: Any) -> list[dict[str, Any]]:
    """Allocate actual receipts. Wallet-LP steps are never blocked here; the run checks the live wallet right before signing them ()."""
    if not isinstance(rules, list) or len(rules) > MAX_RULES:  # a request-size sanity bound, not a plan limit
        raise RoutingError("Invalid routing rules")
    tolerance = text(validate_slippage(slippage))
    prepared: list[dict[str, Any]] = []
    identifiers = set()
    claimed: dict[tuple[str, str], Decimal] = {}
    held_back: dict[str, str] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            raise RoutingError("Invalid routing rule")
        identifier = rule.get("id")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 80 or identifier in identifiers:
            raise RoutingError("Routing rule IDs must be unique")
        identifiers.add(identifier)
        kind, region, symbol = rule.get("kind"), rule.get("source_region"), rule.get("symbol")
        if kind not in {"transfer", "sell", "liquidity"} or not isinstance(region, str) or not isinstance(symbol, str):
            raise RoutingError("Choose a resource, region and routing action")
        key = (region, symbol)
        basis = ledger.harvested.get(key, ZERO)
        quantity = allocation_quantity(rule, basis)
        if basis == ZERO:
            quantity = ZERO
        # a region keeps its next harvest cost back, so "100% of the harvest" can be a little more than is
        # spendable. A percentage route takes what is spendable and says what stayed; an exact amount still errors.
        if rule.get("quantity_mode") == "percent" and quantity > 0 and key in ledger.balances:
            spendable = max(ZERO, ledger.available(region, symbol, harvest_only=True) - claimed.get(key, ZERO))
            if spendable < quantity <= basis - claimed.get(key, ZERO):  # rules claiming more than the harvest still fail
                held_back[identifier] = (f"{region_label(region)} keeps {plain(quantity - spendable)} {symbol} back for its "
                                         f"next harvest cost, so this route moves {plain(spendable)} {symbol}.")
                quantity = spendable
        claimed[key] = claimed.get(key, ZERO) + quantity
        if kind == "liquidity":
            pool = pools.get(symbol)
            if pool is None:
                raise RoutingError("This resource has no supported liquidity pool")
            requested_source = rule.get("dec_source")
            if requested_source is not None and (not isinstance(requested_source, str) or requested_source not in DEC_SOURCES):
                raise RoutingError("Choose wallet DEC or sell half first")
            deposit = liquidity_deposit(pool, quantity) if quantity > 0 else None
            up_to = liquidity_dec_ceiling(deposit, tolerance) if deposit else ZERO
            prepared.append({"rule": rule, "id": identifier, "kind": kind, "region": region,
                             "symbol": symbol, "quantity": quantity, "pool": pool, "deposit": deposit,
                             "up_to": up_to, "dec_source": requested_source or "swap_half",
                             "dec_source_defaulted": requested_source is None})
        else:
            if rule.get("dec_source") is not None:
                raise RoutingError("DEC source is only valid for liquidity routing")
            prepared.append({"rule": rule, "id": identifier, "kind": kind, "region": region,
                             "symbol": symbol, "quantity": quantity})

    steps: list[dict[str, Any]] = []
    # sales are planned (and run) before liquidity, so a wallet-LP step sees the wallet after them.
    prepared.sort(key=lambda entry: entry["kind"] == "liquidity")
    for item in prepared:
        rule, identifier, kind = item["rule"], item["id"], item["kind"]
        region, symbol, quantity = item["region"], item["symbol"], item["quantity"]
        if quantity <= 0:
            empty = {"id": identifier, "kind": kind, "status": "empty", "reason": held_back.get(identifier, "No received resource allocated")}
            if kind == "liquidity":
                empty["rule_id"] = identifier
            steps.append(empty)
            continue
        step = {"id": identifier, "kind": kind, "source_region": region, "symbol": symbol,
                "quantity": text(quantity), "status": "ready"}
        if kind == "liquidity":
            pool, deposit = item["pool"], item["deposit"]
            source, up_to = item["dec_source"], item["up_to"]
            if source == "swap_half":
                ledger.allocate(region, symbol, quantity, harvest_only=True)
                try:
                    split = liquidity_funding_split(pool, quantity, tolerance)
                except RoutingError as exc:
                    step.update(rule_id=identifier, dec_source=source,
                                dec_source_defaulted=item["dec_source_defaulted"],
                                signature_cost=2 if pool.external else 1,
                                status="awaiting_funds", blocked=True, reason=str(exc),
                                deposit=deposit,
                                dec_funding={"source": source, "available": text(ZERO),
                                             "required": deposit["dec_amount"], "up_to": text(up_to),
                                             "shortfall": text(up_to)})
                    steps.append(step)
                    continue
                swap_id = f"{identifier}:dec"
                split_up_to = liquidity_dec_ceiling(split["deposit"], tolerance)
                funding = {"source": source, "available": split["protected_dec"],
                           "required": split["required_dec"], "up_to": text(split_up_to),
                           "shortfall": text(ZERO), "extra_signatures": 2 if pool.external else 1,
                           "split": {key: split[key] for key in
                           ("total_resource", "swap_resource", "deposit_resource", "protected_dec",
                            "required_dec", "estimated_dec", "estimated_surplus_dec")}}
                steps.append({"id": swap_id, "rule_id": identifier, "kind": "liquidity_funding_swap", "source_region": region,
                              "destination_region": INVENTORY, "symbol": symbol,
                              "destination_symbol": "DEC", "quantity": split["swap_resource"],
                              "status": "ready", "quote": split["quote"], "slippage": tolerance,
                              "dec_source": source, "signature_cost": 2 if pool.external else 1,
                              "liquidity_funding": {"role": "first", "liquidity_rule_id": identifier}})
                steps.append({"id": f"{identifier}:liquidity", "rule_id": identifier, "kind": "liquidity",
                              "source_region": region, "symbol": symbol, "quantity": split["deposit_resource"],
                              "status": "ready", "deposit": split["deposit"], "depends_on": swap_id,
                              "dec_source": source, "dec_source_defaulted": item["dec_source_defaulted"],
                              "dec_funding": funding, "signature_cost": 2 if pool.external else 1,
                              "liquidity_funding": {"role": "second", "liquidity_rule_id": identifier}})
                continue

            ledger.allocate(region, symbol, quantity, harvest_only=True)
            available = ledger.available(INVENTORY, "DEC")
            target_dec = up_to
            reservation = target_dec
            reserved = target_dec <= available
            if reserved:
                ledger.allocate(INVENTORY, "DEC", reservation)
            shortfall = max(ZERO, target_dec - available)
            step.update(rule_id=identifier, deposit=deposit, dec_source=source,
                        dec_source_defaulted=item["dec_source_defaulted"],
                        signature_cost=2 if pool.external else 1,
                        dec_funding={"source": source, "available": text(available),
                                     "required": deposit["dec_amount"], "up_to": text(up_to),
                                     "reserved": text(reservation), "shortfall": text(shortfall)})
            if not reserved:
                # (maintainer: "there is no need to calculate ANYTHING before the selling to dec. Just sell to
                # dec. done. After that you have a new dec wallet balance"): never block here. Sales run first; right before
                # this LP step is signed the run reads the live wallet (routing_runs_v2) and requests no signature if short.
                step.update(note="Uses the DEC in your wallet after the sales before it; checked against your live wallet right before signing")
        else:
            destination_symbol = symbol if kind == "transfer" else rule.get("destination_symbol")
            destination_region = rule.get("destination_region")
            if not isinstance(destination_symbol, str):
                raise RoutingError("Choose the sale output resource")
            if destination_symbol in {"DEC", "SPS", "VOUCHER"}:
                destination_region = INVENTORY
            elif not isinstance(destination_region, str) or (destination_region, destination_symbol) not in ledger.balances:
                raise RoutingError("Choose an owned destination region")
            if symbol == destination_symbol and region == destination_region:
                raise RoutingError("Source and destination are identical")
            rule_slippage = text(validate_slippage(rule.get("slippage", tolerance)))
            destination_pool = pools.get(destination_symbol)
            # a resource sold straight into an EXTERNAL pool (today only SPS) cannot use the
            # internal from/to swap contract; the official client sells to DEC first, then swaps that
            # exact DEC to the external token as its own reviewed, separately signed step.
            if kind == "sell" and symbol != "DEC" and destination_symbol != "DEC" and destination_pool is not None and destination_pool.external:
                first_quote = swap_quote(symbol, "DEC", quantity, pools, rule_slippage)
                ledger.allocate(region, symbol, quantity, harvest_only=True)
                step.update(id=f"{identifier}:dec", destination_symbol="DEC", destination_region=INVENTORY,
                           quote=first_quote.public(), slippage=rule_slippage,
                           signature_cost=2 if first_quote.external else 1,
                           two_step={"role": "first", "final_symbol": destination_symbol})
                steps.append(step)
                second_id = f"{identifier}:sps"
                try:
                    second_quote = swap_quote("DEC", destination_symbol, first_quote.output, pools, rule_slippage)
                except RoutingError:
                    steps.append({"id": second_id, "kind": "sell", "status": "empty",
                                 "reason": f"Proceeds too small to swap to {destination_symbol}",
                                 "depends_on": f"{identifier}:dec", "two_step": {"role": "second"}})
                else:
                    second_public = second_quote.public()
                    second_public["estimate"] = True
                    steps.append({"id": second_id, "kind": "sell", "source_region": INVENTORY, "symbol": "DEC",
                                 "destination_symbol": destination_symbol, "destination_region": INVENTORY,
                                 "depends_on": f"{identifier}:dec", "quantity": text(ZERO), "status": "ready",
                                 "quote": second_public, "slippage": rule_slippage,
                                 "signature_cost": 2 if second_quote.external else 1,
                                 "two_step": {"role": "second"}})
                continue
            quote = swap_quote(symbol, destination_symbol, quantity, pools, rule_slippage)
            ledger.allocate(region, symbol, quantity, harvest_only=True)
            step.update(destination_symbol=destination_symbol, destination_region=destination_region,
                        quote=quote.public(), slippage=rule_slippage,
                        signature_cost=2 if quote.external else 1)
        steps.append(step)
    for step in steps:
        message = held_back.get(step.get("rule_id") or step.get("id"))
        if message:
            step["held_back"] = message
    return steps
