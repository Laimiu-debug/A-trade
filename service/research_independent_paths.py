"""One frozen opportunity per isolated ledger; never a whole-period portfolio.

The original period, calendar, bars and historical checks are retained. Entry,
fills, fees and both exit definitions belong to the existing account classes.
Only the transaction-loop wrapper and compact evidence projection are new.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .portfolio_engine import PortfolioEngine, _digest
from .research_fixed_signal_account import FixedSignalAccount
from .research_volatility_trail import VolatilityTrailAccount

VERSION = "research-independent-paths-v1"
EXIT_POLICIES = ("trail10", "volatilityTrail")
STATUSES = ("closed", "open", "unfilled", "unresolved")
INITIAL_CAPITAL = 500_000.
ENTRY_WEIGHT = .2
NOMINAL_ENTRY_BUDGET = 100_000.


def single_security_dataset(dataset, symbol):
    """Copy the entire source envelope and exactly one untrimmed security."""
    if not isinstance(dataset, dict) or not isinstance(dataset.get("securities"), list):
        raise ValueError("A source dataset with securities is required")
    matches = [row for row in dataset["securities"] if isinstance(row, dict) and row.get("symbol") == symbol]
    if len(matches) != 1:
        raise ValueError("Signal symbol must identify exactly one source security")
    # Do not deep-copy the other securities, truncate warmup/calendar dates or
    # reinterpret global membership, price-basis or provenance metadata.
    projected = {key: deepcopy(value) for key, value in dataset.items() if key != "securities"}
    projected["securities"] = [deepcopy(matches[0])]
    return projected


def _pending_target_empty(target):
    if target is None:
        return True
    # The frozen engine creates a target envelope even on an empty-cash day.
    # Preserve that envelope; only its entirely empty order maps are harmless.
    return isinstance(target, dict) and all(target.get(key) == {} for key in
        ("weights", "quantities", "budgets", "priorityBySymbol", "signalDates"))


def _unprocessed_entitlements(state):
    processed = set(state["processedActions"])
    return {key: deepcopy(value) for key, value in state["actionEntitlements"].items()
            if value.get("quantity", 0) > 0 and key not in processed}


def _unresolved_reasons(engine):
    state = engine.state
    reasons = []
    if engine.research_state["opportunities"][0]["status"] in ("notObserved", "planned"):
        reasons.append("signalNotTerminal")
    for key in ("positions", "forcedExits", "cashReceivables", "actionPayables", "preExRights"):
        if state[key]:
            reasons.append(key)
    if state["reservedActionCash"] != 0:
        reasons.append("reservedActionCash")
    if _unprocessed_entitlements(state):
        reasons.append("unprocessedEntitlements")
    if state["unaccountedActionExposure"]:
        reasons.append("unaccountedActionExposure")
    if not _pending_target_empty(state["pendingTarget"]):
        reasons.append("pendingTargetOrders")
    return reasons


def _checked_config(config, exit_policy):
    if exit_policy not in EXIT_POLICIES:
        raise ValueError("Unsupported independent path exit policy")
    if not isinstance(config, dict):
        raise ValueError("Original explicit account config is required")
    expected = {"initialCapital": INITIAL_CAPITAL, "maxStockWeight": ENTRY_WEIGHT,
                "stopLossPct": .05, "trailingStopPct": .1 if exit_policy == "trail10" else 0,
                "takeProfitPct": 0, "maxHoldingDays": 0}
    if any(type(config.get(key)) not in (int, float) or config[key] != value for key, value in expected.items()):
        raise ValueError("Keep original stop5/exit config and fixed 500000 capital /20percent allocation")
    return deepcopy(config)


def _compact(engine, signal, source_count, exit_policy, stop_reason, first_resolved_date):
    # result() performs the inherited identity/state guards, fee reconciliation
    # and standard valuation projection. Its full-period performance metrics
    # are deliberately not exposed as independent-opportunity performance.
    result = engine.result()
    state = engine.state
    trades = result["trades"]
    buys = [row for row in trades if row["side"] == "buy"]
    if len(buys) > 1 or any(row["symbol"] != signal["symbol"] for row in trades):
        raise ValueError("Independent path must contain at most one initial buy")
    entered = bool(buys)
    unresolved = _unresolved_reasons(engine)
    position_closed = entered and not state["positions"] and bool(result["closedTrades"])
    fully_closed = position_closed and not unresolved
    if fully_closed:
        status = "closed"
    elif state["positions"]:
        status = "open"
    elif entered or unresolved:
        status = "unresolved"
    else:
        status = "unfilled"
    entry_date = buys[0]["date"] if buys else None
    final_exit_date = max((row["exitDate"] for row in result["closedTrades"]), default=None) if position_closed else None
    # Investment is actual committed entry cash plus declared subscription
    # cash. The current experiment has no declared corporate actions; retaining
    # these explicit cash legs avoids silently treating a later receipt as an
    # earlier sell. Its runner must enforce its own source/availability scope.
    invested = math.fsum(row["amount"] + row["fee"] for row in buys)
    invested += math.fsum(row.get("amount", 0.) for row in state["actionLedger"]
                          if row["stage"] in ("subscriptionPaid", "cashPaid"))
    cash_delta = state["cash"] - INITIAL_CAPITAL
    trade_cash = math.fsum(row["amount"] - row["fee"] if row["side"] == "sell"
                          else -row["amount"] - row["fee"] for row in trades)
    action_cash = math.fsum(row.get("amount", 0.) * (1 if row["stage"] == "cashReceived" else -1)
                           for row in state["actionLedger"]
                           if row["stage"] in ("cashReceived", "cashPaid", "subscriptionPaid"))
    if not math.isclose(cash_delta, trade_cash + action_cash, rel_tol=1e-10, abs_tol=1e-6):
        raise ValueError("Independent cash ledger does not reconcile")
    if fully_closed and (not invested > 0 or not first_resolved_date or not entry_date < final_exit_date <= first_resolved_date):
        raise ValueError("Closed independent label has inconsistent maturity")
    net_pnl = cash_delta if fully_closed else None
    net_return = net_pnl / invested if fully_closed else None
    if net_return is not None and not math.isfinite(net_return):
        raise ValueError("Finite decimal closed return required")
    available = first_resolved_date if fully_closed else None
    label = {"formationId": signal["signalId"], "formationDate": signal["signalDate"],
             "entryDate": entry_date, "exitDate": final_exit_date if fully_closed else None,
             "netReturn": net_return} if entered else None
    processed = state["cursor"]
    terminal = {key: deepcopy(state[key]) for key in (
        "cash", "pendingTarget", "forcedExits", "cashReceivables", "actionPayables", "reservedActionCash",
        "preExRights", "actionEntitlements", "processedActions", "actionLedger", "totalFees", "totalSlippage",
        "lastPrices", "lastPriceDates", "quarantined", "unaccountedActionExposure")}
    terminal.update(positions=result["positions"], marketValue=state["curve"][-1]["marketValue"],
                    equity=state["curve"][-1]["equity"], lastCurve=deepcopy(state["curve"][-1]),
                    unprocessedEntitlements=_unprocessed_entitlements(state), unresolvedReasons=unresolved,
                    emptyPendingTargetEnvelope=state["pendingTarget"] is not None and _pending_target_empty(state["pendingTarget"]),
                    tradeCashNet=trade_cash, actionCashNet=action_cash, cashChange=cash_delta)
    return {"version": VERSION, "exitPolicy": exit_policy, "signal": deepcopy(signal), "status": status, "label": label,
        "sourceProjection": {"sourceSecurityCount": source_count, "securityCount": 1,
            "symbol": signal["symbol"], "datasetDigest": engine.data_digest,
            "sourceFingerprint": engine.fingerprint, "calendarDigest": _digest(engine.dataset["calendar"]),
            "securityDigest": _digest(engine.dataset["securities"][0]),
            "calendarSessions": len(engine.dataset["calendar"]),
            "retainedMetadataFields": sorted(key for key in engine.dataset if key != "securities"),
            "meaning": "All source metadata and full calendar retained; exactly one complete security copied; no historical shortcut"},
        "run": {"requestedStartDate": engine.config["startDate"], "requestedEndDate": engine.config["endDate"],
            "firstProcessedDate": engine.dates[0], "processedThrough": engine.dates[processed - 1],
            "processedSessions": processed, "totalPeriodSessions": len(engine.dates),
            "stoppedEarly": processed < len(engine.dates), "stopReason": stop_reason,
            "initialCapital": INITIAL_CAPITAL, "entryWeight": ENTRY_WEIGHT, "nominalEntryBudget": NOMINAL_ENTRY_BUDGET,
            "notWholePeriodInvestmentAccount": True},
        "outcome": {"entered": entered, "positionClosed": position_closed, "fullyClosed": fully_closed,
            "labelEligible": fully_closed, "entryDate": entry_date, "finalExitDate": final_exit_date,
            "labelAvailableDate": available, "investedAmount": invested if entered else None,
            "netPnlAmount": net_pnl, "netReturn": net_return},
        "evidence": {"identity": deepcopy(engine.identity), "opportunity": deepcopy(engine.research_state["opportunities"][0]),
            "trades": trades, "closedTrades": result["closedTrades"], "decisions": result["decisions"],
            "feeLedger": result["research"]["feeLedger"], "volatilityTrail": result["research"].get("volatilityTrail"),
            "currentPositionTrails": deepcopy(engine._trail_positions) if exit_policy == "volatilityTrail" else None,
            "terminal": terminal, "quality": result["quality"], "warnings": result["warnings"]},
        "certified": False, "targetAchieved": False}


def run_independent_path(dataset, signal, config, *, exit_policy, fee_policy, stop_when_resolved=True):
    """Run one original signal, returning audited-input-ready compact evidence.

    ``closed`` labels are decimal net cash P&L / actual invested cash; neither
    an unfilled opportunity nor a partially sold/open holding is a zero label.
    ``stop_when_resolved=False`` is useful for full-period synthetic equivalence
    checks. Even a shortened path keeps the original config end unchanged.
    """
    if type(stop_when_resolved) is not bool:
        raise ValueError("stop_when_resolved must be a boolean")
    if not isinstance(signal, dict):
        raise ValueError("One complete original signal object is required")
    settings = _checked_config(config, exit_policy)
    projected = single_security_dataset(dataset, signal.get("symbol"))
    account_class = FixedSignalAccount if exit_policy == "trail10" else VolatilityTrailAccount
    engine = account_class(projected, [deepcopy(signal)], settings,
                           entry_weight=ENTRY_WEIGHT, fee_policy=fee_policy)
    engine._identity_stable()
    engine._validate_research_state()
    first_resolved_date = None
    stop_reason = "periodEnd"
    while engine.state["cursor"] < len(engine.dates):
        # Direct inherited transaction step preserves subclass dispatch and
        # rollback. Avoid rehashing the full source at every calendar session.
        PortfolioEngine.step(engine, 1)
        if not _unresolved_reasons(engine):
            if first_resolved_date is None:
                first_resolved_date = engine.dates[engine.state["cursor"] - 1]
            if stop_when_resolved and engine.state["cursor"] < len(engine.dates):
                stop_reason = "signalTerminalAndLedgerSettled"
                break
    engine._identity_stable()
    engine._validate_research_state()
    return _compact(engine, signal, len(dataset["securities"]), exit_policy, stop_reason, first_resolved_date)
