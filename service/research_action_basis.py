"""Isolated distribution arithmetic for research; not an account adapter.

Raw bars and the old portfolio engines are never changed.  Source coverage,
actual broker allocation/credit and an investor-specific tax instruction remain
external requirements.  Floor/ceil allocation and a tax reserve are explicitly
named scenarios, not assertions about a particular shareholder's settlement.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP
import re
import math

VERSION = "research-action-basis-v1"
D = Decimal
CENT = D("0.01")


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Canonical date required")
    return value


def _decimal(value, *, positive=False):
    # Strings retain the published decimal precision; binary floats are not a
    # permitted way to declare source terms or ledger amounts.
    if type(value) not in (str, int, Decimal):
        raise ValueError("Explicit decimal string/integer required")
    try:
        result = D(value)
    except Exception as exc:
        raise ValueError("Invalid decimal") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError("Nonnegative finite decimal required")
    return result


def _quantity(value, *, positive=False):
    if type(value) is not int or value < int(positive):
        raise ValueError("Whole-share quantity required")
    return value


def _bar_decimal(value):
    # Raw TDX readers expose native floats. Convert their published-decimal
    # representation explicitly, rather than importing binary float residue.
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Finite raw market price required")
        value = str(value)
    return _decimal(value, positive=True)


def normalize_distribution(raw):
    """Only dated, separately evidenced actual and price-reference terms.

    Publication dates alone establish availability at that day's CLOSE.  They
    do not establish opening availability or the timestamp of a broker credit.
    This restricted schema deliberately excludes rights issues and settlements.
    """
    keys = {"id", "symbol", "publicDate", "recordDate", "exDate", "shareListingDate",
            "cashPayDate", "cashGrossPerShare", "additionalSharesPerShare",
            "referenceCashPerShare", "referenceAdditionalSharesPerShare",
            "sourceSha256", "sourceUrl"}
    if not isinstance(raw, dict) or set(raw) != keys:
        raise ValueError("Exact distribution schema required; unknown is not zero")
    row = deepcopy(raw)
    if (not isinstance(row["id"], str) or not row["id"] or
            not isinstance(row["symbol"], str) or not re.fullmatch(r"(?:sh|sz)\d{6}", row["symbol"]) or
            not isinstance(row["sourceSha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sourceSha256"]) or
            not isinstance(row["sourceUrl"], str) or not row["sourceUrl"].startswith("https://")):
        raise ValueError("Dated source identity required")
    for key in ("publicDate", "recordDate", "exDate"):
        _day(row[key])
    if not row["publicDate"] <= row["recordDate"] < row["exDate"]:
        raise ValueError("Publication <= registration < ex-date required")
    for key in ("cashGrossPerShare", "additionalSharesPerShare", "referenceCashPerShare",
                "referenceAdditionalSharesPerShare"):
        row[key] = str(_decimal(row[key]))
    if not (D(row["cashGrossPerShare"]) or D(row["additionalSharesPerShare"])):
        raise ValueError("Empty economic event")
    for key, amount in (("cashPayDate", "cashGrossPerShare"),
                        ("shareListingDate", "additionalSharesPerShare")):
        if D(row[amount]):
            if _day(row[key]) < row["exDate"]:
                raise ValueError("Settlement cannot precede ex-date")
        elif row[key] is not None:
            raise ValueError("No unsupported settlement date for zero entitlement")
    return row


def reference_change(event, previous_close):
    event = normalize_distribution(event)
    previous = _decimal(previous_close, positive=True)
    reference = ((previous - D(event["referenceCashPerShare"])) /
                 (1 + D(event["referenceAdditionalSharesPerShare"]))).quantize(CENT, rounding=ROUND_HALF_UP)
    if reference <= 0:
        raise ValueError("Nonpositive ex-reference")
    return {"previousClose": str(previous), "exReference": str(reference),
            "factor": str(reference / previous), "eventId": event["id"]}


def causal_price_window(raw_bars, events, *, as_of, symbol, price_basis):
    """Rebase past OHLC into the as-of close's units, using effective events.

    This is a price-continuity feature series, not total return.  Cash, shares,
    volume and amount are not encoded as portfolio wealth or adjusted turnover.
    Future ex-events do not affect earlier features, even if already announced.
    Event-list completeness is NOT inferred from price continuity.
    """
    _day(as_of)
    if price_basis != "raw-unadjusted":
        raise ValueError("Explicit raw-unadjusted source basis required")
    if not isinstance(symbol, str) or not re.fullmatch(r"(?:sh|sz)\d{6}", symbol):
        raise ValueError("Explicit single-symbol identity required")
    bars = [deepcopy(b) for b in raw_bars if _day(b["date"]) <= as_of]
    dates = [b["date"] for b in bars]
    if dates != sorted(set(dates)):
        raise ValueError("Unique ordered raw bars required")
    for bar in bars:
        if bar.get("symbol", symbol) != symbol:
            raise ValueError("Single-symbol window required")
        if bar.get("priceBasisId", price_basis) != price_basis:
            raise ValueError("Conflicting per-bar price basis")
        prices = {key: _bar_decimal(bar[key]) for key in ("open", "high", "low", "close")}
        if not prices["low"] <= min(prices["open"], prices["close"]) <= max(prices["open"], prices["close"]) <= prices["high"]:
            raise ValueError("Invalid raw OHLC envelope")
    active, identities, effective_dates = [], set(), set()
    for raw in events:
        if _day(raw["exDate"]) > as_of:
            continue
        row = normalize_distribution(raw)
        if row["symbol"] != symbol:
            raise ValueError("Single-symbol window required")
        if row["id"] in identities or row["exDate"] in effective_dates:
            raise ValueError("Duplicate/ambiguous effective event")
        identities.add(row["id"])
        effective_dates.add(row["exDate"])
        prior = [b for b in bars if b["date"] < row["exDate"]]
        if not prior:
            # An event older than the supplied window has no units to convert.
            continue
        if prior[-1]["date"] != row["recordDate"]:
            raise ValueError("Missing record close; ex-reference cannot be derived")
        change = reference_change(row, _bar_decimal(prior[-1]["close"]))
        active.append((row, change))
    active.sort(key=lambda pair: (pair[0]["exDate"], pair[0]["id"]))
    result = []
    for raw in bars:
        factor = D(1)
        applied = []
        for event, change in active:
            if raw["date"] < event["exDate"]:
                factor *= D(change["factor"])
                applied.append(event["id"])
        converted = deepcopy(raw)
        converted.update({key: str(_bar_decimal(raw[key]) * factor) for key in ("open", "high", "low", "close")})
        converted.update(priceBasis=VERSION, priceBasisId=VERSION, sourcePriceBasisId=price_basis,
                         asOfDate=as_of, appliedEventIds=applied, priceFactor=str(factor))
        result.append(converted)
    return {"asOfDate": as_of, "bars": result,
            "referenceChanges": [{**change, "exDate": event["exDate"]} for event, change in active],
            "basisMeaning": "causalPriceContinuity;notTotalReturn;rawAmountAndVolumeUnchanged",
            "coverageCertified": False}


def distribution_entitlements(event, record_lots, *, record_as_of, fraction_policy, tax_reserve_rate):
    """Freeze original episode/lot ownership; no current-symbol attribution.

    A floor/ceil scenario is an account-level allocation sensitivity.  The
    deterministic largest-remainder split between this account's lots is a
    research convention and does not reproduce CSDC's cross-account tail draw.
    """
    event = normalize_distribution(event)
    if _day(record_as_of) != event["recordDate"] or fraction_policy not in ("floor", "ceil", "exact"):
        raise ValueError("Record-close snapshot and explicit fraction scenario required")
    rate = _decimal(tax_reserve_rate)
    if rate > 1:
        raise ValueError("Tax reserve rate exceeds one")
    rows, identities = [], set()
    for lot in deepcopy(record_lots):
        if set(lot) != {"lotId", "episodeId", "acquiredDate", "quantity", "cost"}:
            raise ValueError("Exact eligible-lot schema required")
        if (not isinstance(lot["lotId"], str) or not lot["lotId"] or lot["lotId"] in identities or
                not isinstance(lot["episodeId"], str) or not lot["episodeId"] or
                _day(lot["acquiredDate"]) > record_as_of):
            raise ValueError("Unique record-date eligible lots required")
        identities.add(lot["lotId"])
        quantity = _quantity(lot["quantity"], positive=True)
        cost = _decimal(lot["cost"])
        theoretical = quantity * D(event["additionalSharesPerShare"])
        rows.append({**lot, "cost": str(cost), "theoreticalGrant": str(theoretical),
                     "allocatedGrant": int(theoretical.to_integral_value(rounding=ROUND_FLOOR))})
    total = sum((D(row["theoreticalGrant"]) for row in rows), D(0))
    if fraction_policy == "exact" and total != total.to_integral_value():
        raise ValueError("Fractional shareholder allocation unresolved")
    rounding = ROUND_CEILING if fraction_policy == "ceil" else ROUND_FLOOR
    allocated = int(total.to_integral_value(rounding=rounding))
    remaining = allocated - sum(row["allocatedGrant"] for row in rows)
    priority = sorted(rows, key=lambda row: (-(D(row["theoreticalGrant"]) - row["allocatedGrant"]), row["lotId"]))
    for row in priority[:remaining]:
        row["allocatedGrant"] += 1
    for row in rows:
        gross = row["quantity"] * D(event["cashGrossPerShare"])
        row.update(cashGross=str(gross), taxReserve=str(gross * rate),
                   shareListingDate=event["shareListingDate"], cashPayDate=event["cashPayDate"])
    return {"version": VERSION, "event": event, "recordAsOf": record_as_of,
            "fractionPolicy": fraction_policy, "taxReserveRate": str(rate),
            "shareAllocationMeaning": "explicitSensitivityScenario;notBrokerAllocation",
            "taxMeaning": "explicitReserveScenario;notInvestorTaxAssessment",
            "theoreticalGrant": str(total), "allocatedGrant": allocated,
            "unresolvedFraction": str(total - total.to_integral_value(rounding=ROUND_FLOOR)), "lots": rows}


def grant_lots(entitlement, current_lots, *, day):
    """Return an atomic ex-date share/cost delta; original owners only.

    Missing/mismatching owners are an explicit integration error.  In
    particular a fresh position with the same symbol cannot inherit old rights.
    The caller must retain separate entitlement custody if ownership changed.
    """
    event = normalize_distribution(entitlement["event"])
    if _day(day) != event["exDate"]:
        raise ValueError("Grant must be applied exactly on ex-date")
    result = deepcopy(current_lots)
    lookup = {row["lotId"]: row for row in result}
    if len(lookup) != len(result):
        raise ValueError("Duplicate current lot")
    if any(lot.get("eventId") == event["id"] for lot in result):
        raise ValueError("Share grant already applied")
    grants = []
    for record in entitlement["lots"]:
        lot = lookup.get(record["lotId"])
        if (lot is None or lot["episodeId"] != record["episodeId"] or
                lot["quantity"] != record["quantity"] or D(lot["cost"]) != D(record["cost"])):
            raise ValueError("Record ownership changed; separate entitlement custody required")
        added = record["allocatedGrant"]
        if added:
            old_cost = D(lot["cost"])
            lot["cost"] = str(old_cost * lot["quantity"] / (lot["quantity"] + added))
            grants.append({"lotId": event["id"] + ":" + lot["lotId"], "episodeId": lot["episodeId"],
                "acquiredDate": lot["acquiredDate"], "quantity": added,
                "cost": str(old_cost - D(lot["cost"])),
                "unlockDate": max(event["shareListingDate"], lot.get("unlockDate", lot["acquiredDate"])),
                "eventId": event["id"]})
    return result + grants


def rebase_risk_and_mark(position, change, *, ex_date):
    """Convert units once even without an ex-day bar; keep real market move.

    The last mark is converted only if its date precedes the ex-date.  An
    observed ex-day close is already in new units.  A ratchet stored as a ratio
    to riskEntryPrice is invariant, rather than being scaled a second time.
    """
    _day(ex_date)
    result = deepcopy(position)
    applied = list(result.get("appliedReferenceEvents", []))
    if change["eventId"] in applied:
        raise ValueError("Reference event already applied")
    factor = _decimal(change["factor"], positive=True)
    for key in ("riskEntryPrice", "closePeak"):
        result[key] = str(_decimal(result[key], positive=True) * factor)
    if _day(result["lastPriceDate"]) < ex_date:
        result["lastPrice"] = str(_decimal(result["lastPrice"], positive=True) * factor)
        result["markMeaning"] = "actionConvertedStaleMark;notObservedExDayPrice"
    applied.append(change["eventId"])
    result["appliedReferenceEvents"] = applied
    return result


def consume_sellable_lots(lots, quantity, *, day, price, fee):
    """Cost-only FIFO reducer after an external execution/quantity decision.

    No liquidity/limit/lot-rule inference: those remain in the execution engine.
    Locked grants cannot be sold; failed reductions leave inputs unchanged.
    """
    _day(day)
    quantity = _quantity(quantity, positive=True)
    price, fee = _decimal(price, positive=True), _decimal(fee)
    result, remaining, cost = sorted(deepcopy(lots), key=lambda row: (row["acquiredDate"], row["lotId"])), quantity, D(0)
    if len({row["lotId"] for row in result}) != len(result):
        raise ValueError("Unique FIFO lots required")
    for lot in result:
        _decimal(lot["cost"])
        if _day(lot["acquiredDate"]) >= day or _day(lot.get("unlockDate", lot["acquiredDate"])) > day:
            continue
        take = min(remaining, _quantity(lot["quantity"]))
        if take:
            consumed = D(lot["cost"]) if take == lot["quantity"] else D(lot["cost"]) * take / lot["quantity"]
            lot["quantity"] -= take
            lot["cost"] = str(D(lot["cost"]) - consumed)
            cost += consumed
            remaining -= take
        if not remaining:
            break
    if remaining:
        raise ValueError("Requested shares are locked or unavailable")
    return {"lots": [row for row in result if row["quantity"]], "quantity": quantity,
            "costRemoved": str(cost), "netCash": str(quantity * price - fee),
            "realizedPnl": str(quantity * price - fee - cost)}


class DistributionCashBook:
    """Exact claims with original episode attribution and atomic transitions.

    Date-only payment becomes spendable at the first processed OPEN strictly
    after pay-date.  This is a conservative research credit convention, not a
    statement that any broker uses that timing.  Reserve and actual settled tax
    are distinct.  A caller must supply an explicit tax settlement instruction.
    """
    def __init__(self):
        self.claims = {}
        self.events = set()
        self.last_open = None
        self.last_event_day = None
        self.movements = []

    def checkpoint(self):
        return deepcopy({"claims": self.claims, "events": sorted(self.events),
                         "lastOpen": self.last_open, "lastEventDay": self.last_event_day, "movements": self.movements})

    def restore(self, state):
        # Checkpoints are internal transactional snapshots, not an untrusted
        # persistence format for resuming market accounts.
        self.claims = deepcopy(state["claims"])
        self.events = set(state["events"])
        self.last_open = state["lastOpen"]
        self.last_event_day = state["lastEventDay"]
        self.movements = deepcopy(state["movements"])

    def accrue(self, entitlement, *, day):
        event = normalize_distribution(entitlement["event"])
        if _day(day) != event["exDate"] or event["id"] in self.events:
            raise ValueError("One ex-date accrual per event required")
        if any(clock is not None and day < clock for clock in (self.last_open, self.last_event_day)):
            raise ValueError("Cannot backdate an accrual")
        claims = deepcopy(self.claims)
        for lot in entitlement["lots"]:
            gross, reserve = _decimal(lot["cashGross"]), _decimal(lot["taxReserve"])
            if reserve > gross:
                raise ValueError("Reserve exceeds claim")
            if not gross:
                continue
            ident = event["id"] + ":" + lot["lotId"]
            if ident in claims:
                raise ValueError("Duplicate claim")
            claims[ident] = {"claimId": ident, "eventId": event["id"], "episodeId": lot["episodeId"],
                "exDate": day, "cashPayDate": event["cashPayDate"], "gross": str(gross),
                "reserve": str(reserve), "creditedAtOpen": None, "settledTax": None, "taxSettledDate": None}
        self.claims = claims
        self.events.add(event["id"])
        self.last_event_day = day

    def advance_open(self, day):
        _day(day)
        if ((self.last_open is not None and day <= self.last_open) or
                (self.last_event_day is not None and day < self.last_event_day)):
            raise ValueError("Strictly increasing opening clock required")
        claims, movement = deepcopy(self.claims), {"day": day, "grossCredit": D(0), "reservedCash": D(0),
                                                  "spendableCashDelta": D(0), "claimIds": []}
        for claim in claims.values():
            if claim["creditedAtOpen"] is None and claim["cashPayDate"] < day:
                claim["creditedAtOpen"] = day
                gross, reserve = D(claim["gross"]), D(claim["reserve"])
                movement["grossCredit"] += gross
                movement["reservedCash"] += reserve
                movement["spendableCashDelta"] += gross - reserve
                movement["claimIds"].append(claim["claimId"])
        movement = {key: str(value) if isinstance(value, D) else value for key, value in movement.items()}
        self.claims, self.last_open = claims, day
        self.movements.append(movement)
        return deepcopy(movement)

    def settle_tax(self, claim_id, amount, *, day):
        _day(day)
        claim, tax = self.claims[claim_id], _decimal(amount)
        if (claim["creditedAtOpen"] is None or day < claim["creditedAtOpen"] or
                self.last_open is None or day != self.last_open or claim["settledTax"] is not None or tax > D(claim["gross"])):
            raise ValueError("One explicit tax instruction on current processed opening required")
        if self.last_event_day is not None and day < self.last_event_day:
            raise ValueError("Cannot backdate tax settlement")
        movement = {"day": day, "claimId": claim_id, "episodeId": claim["episodeId"],
                    "settledTax": str(tax), "spendableCashDelta": str(D(claim["reserve"]) - tax)}
        claim.update(settledTax=str(tax), taxSettledDate=day)
        self.movements.append(movement)
        return deepcopy(movement)

    def summary(self):
        unpaid, liability, reserved, income = D(0), D(0), D(0), {}
        for claim in self.claims.values():
            gross, reserve = D(claim["gross"]), D(claim["reserve"])
            tax = D(claim["settledTax"]) if claim["settledTax"] is not None else reserve
            income[claim["episodeId"]] = income.get(claim["episodeId"], D(0)) + gross - tax
            if claim["creditedAtOpen"] is None:
                unpaid += gross
            if claim["settledTax"] is None:
                liability += reserve
                if claim["creditedAtOpen"] is not None:
                    reserved += reserve
        return {"grossReceivable": str(unpaid), "taxReserveLiability": str(liability),
                "reservedPaidCash": str(reserved), "netAssetAdjustment": str(unpaid + reserved - liability),
                "incomeByOriginalEpisode": {key: str(value) for key, value in income.items()},
                "investorTaxCertified": False, "brokerCreditCertified": False}
