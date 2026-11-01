"""Pure, descriptive decomposition of two saved position episodes.

No account is executed and no counterfactual return is constructed. A common
episode's PnL difference is split symmetrically into return-profile and committed-
capital terms. Both retain execution, fee, maturity and account-path dependence.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math


VERSION = "research-exit-mechanism-v1"
GROUPS = ("bothSameEntry", "bothDifferentEntry", "controlOnly", "candidateOnly", "neither")
POSITION_STATES = ("closed", "openAtCutoff", "notEntered")
COMPONENTS = ("returnProfileComponent", "committedCapitalComponent",
              "candidateOnlyComponent", "controlOnlyComponent")
_FIELDS = {"entered", "entryDate", "entryPrice", "exitDate", "positionState",
           "investedAmount", "closedPnlAmount", "openRealizedPnlAmount",
           "openUnrealizedPnlAmount", "holdingSessions"}
_MONEY = ("closedPnlAmount", "openRealizedPnlAmount", "openUnrealizedPnlAmount")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _number(value, name):
    _require(_finite(value), f"{name} must be a finite number, not bool")
    return float(value)


def _sum(values):
    try:
        result = math.fsum(values)
    except (OverflowError, ValueError):
        raise ValueError("Non-finite arithmetic result") from None
    return _number(result, "Arithmetic result")


def _day(value):
    _require(isinstance(value, str), "Date must be canonical YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("Date must be canonical YYYY-MM-DD") from None
    _require(parsed.isoformat() == value, "Date must be canonical YYYY-MM-DD")
    return value


def _episode(row):
    _require(isinstance(row, dict) and _FIELDS <= row.keys(), "Incomplete normalized episode")
    _require(type(row["entered"]) is bool, "entered must be bool")
    state = row["positionState"]
    _require(state in POSITION_STATES, "Unknown positionState")
    capital = _number(row["investedAmount"], "investedAmount")
    money = {key: _number(row[key], key) for key in _MONEY}
    if not row["entered"]:
        _require(state == "notEntered", "Unentered episode has a position")
        _require(capital == 0 and all(value == 0 for value in money.values()),
                 "Unentered episode has money")
        _require(all(row[key] is None for key in ("entryDate", "entryPrice", "exitDate", "holdingSessions")),
                 "Unentered episode has execution or holding data")
        return capital, 0.0, None
    _require(state != "notEntered" and capital > 0, "Entered episode needs positive investedAmount")
    entry = _day(row["entryDate"])
    _require(_number(row["entryPrice"], "entryPrice") > 0, "entryPrice must be positive")
    age = row["holdingSessions"]
    _require(age is None or type(age) is int and age > 0, "holdingSessions must be positive int or null")
    if state == "closed":
        _require(_day(row["exitDate"]) >= entry, "Exit precedes entry")
        _require(money["openRealizedPnlAmount"] == money["openUnrealizedPnlAmount"] == 0,
                 "Closed episode cannot contain open PnL")
        pnl = money["closedPnlAmount"]
    else:
        _require(row["exitDate"] is None and money["closedPnlAmount"] == 0,
                 "Open episode cannot have a closed date or closed PnL")
        pnl = _sum((money["openRealizedPnlAmount"], money["openUnrealizedPnlAmount"]))
    rate = _number(pnl / capital, "Episode return fraction")
    return capital, pnl, rate


def pair_episode(control, candidate):
    """Pair normalized episodes; investedAmount is buy outlay including fees.

    Open PnL includes realized partial-sale PnL and the remaining mark-to-market
    PnL, with no hypothetical liquidation fee. Missing capital is an error rather
    than a zero-return episode. Caller owns identity and original-fill linkage.
    """
    ic, pc, rc = _episode(control)
    iv, pv, rv = _episode(candidate)
    both = control["entered"] and candidate["entered"]
    same_entry = both and control["entryDate"] == candidate["entryDate"] and control["entryPrice"] == candidate["entryPrice"]
    group = ("bothSameEntry" if same_entry else "bothDifferentEntry" if both else
             "controlOnly" if control["entered"] else "candidateOnly" if candidate["entered"] else "neither")
    parts = dict.fromkeys(COMPONENTS, 0.0)
    if both:
        # Half-before-add avoids overflow of the sum when its mean is finite.
        mean_capital = _sum((ic / 2, iv / 2))
        mean_rate = _sum((rc / 2, rv / 2))
        parts["returnProfileComponent"] = _number(mean_capital * _sum((rv, -rc)), "Return-profile component")
        parts["committedCapitalComponent"] = _number(mean_rate * _sum((iv, -ic)), "Capital component")
    elif control["entered"]:
        parts["controlOnlyComponent"] = -pc
    elif candidate["entered"]:
        parts["candidateOnlyComponent"] = pv
    closed_pair = same_entry and control["positionState"] == candidate["positionState"] == "closed"
    timing = None
    if closed_pair:
        timing = "later" if candidate["exitDate"] > control["exitDate"] else "earlier" if candidate["exitDate"] < control["exitDate"] else "same"
    age_known = both and control["holdingSessions"] is not None and candidate["holdingSessions"] is not None
    return {"control": deepcopy(control), "candidate": deepcopy(candidate),
            "group": group, "maturity": control["positionState"] + "__" + candidate["positionState"],
            "controlPnl": pc, "candidatePnl": pv, "deltaPnl": _sum((pv, -pc)), **parts,
            "controlReturnPct": _number(rc * 100, "Control return percent") if rc is not None else None,
            "candidateReturnPct": _number(rv * 100, "Candidate return percent") if rv is not None else None,
            "holdingDeltaSessions": candidate["holdingSessions"] - control["holdingSessions"] if age_known else None,
            "closedPairTiming": timing,
            "baselineOutcome": ("win" if pc > 0 else "loss" if pc < 0 else "zero") if closed_pair else None}


def aggregate(rows):
    """Aggregate paired rows, preserving empty cells and open/closed maturity.

    Extra identity fields on rows and normalized episodes are permitted. Derived
    fields must still match pair_episode; no rounded or altered row is accepted.
    Mean/median return differences include both common-entry groups, never a
    synthetic zero return for an unentered side. PnL contributions are additive;
    these distributions are not subgroup account returns.
    """
    selected = []
    for row in rows:
        _require(isinstance(row, dict) and "control" in row and "candidate" in row,
                 "Aggregate needs paired rows")
        checked = pair_episode(row["control"], row["candidate"])
        _require(all(key in row and row[key] == value for key, value in checked.items()),
                 "Paired row derived fields differ")
        selected.append(checked)
    groups = {group: sum(row["group"] == group for row in selected) for group in GROUPS}
    maturity = {a + "__" + b: sum(row["maturity"] == a + "__" + b for row in selected)
                for a in POSITION_STATES for b in POSITION_STATES}
    differences = sorted(_sum((row["candidateReturnPct"], -row["controlReturnPct"]))
                         for row in selected if row["control"]["entered"] and row["candidate"]["entered"])
    n = len(differences)
    median = (differences[n // 2] if n % 2 else _sum((differences[n // 2 - 1] / 2, differences[n // 2] / 2))) if n else None
    result = {"count": len(selected), "groupCounts": groups, "maturityCounts": maturity,
              "commonEnteredCount": n,
              **{key: _sum(row[key] for row in selected) for key in (*COMPONENTS, "controlPnl", "candidatePnl", "deltaPnl")},
              "meanReturnDeltaPct": _sum(value / n for value in differences) if n else None,
              "medianReturnDeltaPct": median}
    for side in ("control", "candidate"):
        result[side + "EnteredCount"] = sum(row[side]["entered"] for row in selected)
        result[side + "ClosedCount"] = sum(row[side]["positionState"] == "closed" for row in selected)
        result[side + "OpenCount"] = sum(row[side]["positionState"] == "openAtCutoff" for row in selected)
        for key in _MONEY:
            result[side + key[0].upper() + key[1:]] = _sum(row[side][key] for row in selected)
    return result
