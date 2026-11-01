"""First anchor decline and member level retention on immutable parent Top5.

No new parent formation, ownership, price-breakout search, file I/O or account.
"""
from collections import Counter
from copy import deepcopy

from .research_leader_lag import _Series, _day, _digest, _finite
from .research_leader_amount_handoff import _price

VERSION = "leader-pullback-retention-v1"
STRATEGY_ID = "leaderPullbackRetentionV1"
PARENT_ID = "leaderAmountHandoffV1"
MODES = ("control", "retained")
COMMON_STATES = ("firstDownUnknown", "noDownThroughT", "sameDayDown", "memberPathUnknown", "commonKnown")
BASIS = {
    "population": "all immutable batch43 original member Top5, including T-amount unknown; inherited initial known member M3(A)<=anchor M3(A)",
    "firstDown": "scan anchor adjacent pairs A+1..T; stop at first unknown or strict decline; proven P is not invalidated by later anchor bars",
    "retention": "for P<T, member exact valid P-1..T-1; every close P..T-1>=fixed close(P-1); T excluded and no recovery repair",
    "quality": "frozen raw OHLCV/envelope/status semantics; no new amount, preclose, Q, relative-rank or date gate",
    "selection": "parent owner, rank and first T unchanged; shared completeness before retention; no replacement",
    "interpretation": "conditional price-path contrast, not causal leadership transfer, material drawdown or verified main-force identity",
    "scope": "six frozen surviving-cohort development sources, raw prices and historical-status limits; no2026 or holdout claim",
}


def _close(source, at):
    bar = source.bars[at]
    value = bar.get("close") if bar is not None else None
    return value if _finite(value) else None


def first_down(source, calendar, formation_index, decision_index):
    """Inspect each exact pair only until the first decisive observation."""
    a, t = formation_index, decision_index
    if not 0 <= a < t < len(calendar): raise ValueError("First-down indices must satisfy A<T")
    result = {"status": "noDownThroughT", "formationDate": calendar[a], "decisionDate": calendar[t],
              "firstDownDate": None, "unknownDate": None, "windowDates": [calendar[a]],
              "evaluatedPairs": [], "reasons": []}
    for at in range(a + 1, t + 1):
        previous_reason, reason = source.reasons[at - 1], source.reasons[at]
        previous_close, close = _close(source, at - 1), _close(source, at)
        known = previous_reason is None and reason is None
        down = close < previous_close if known else None
        result["windowDates"].append(calendar[at])
        result["evaluatedPairs"].append({"previousDate": calendar[at - 1], "date": calendar[at],
            "previousClose": previous_close, "close": close, "previousReason": previous_reason,
            "reason": reason, "down": down})
        if not known:
            result.update(status="firstDownUnknown", unknownDate=calendar[at], reasons=[
                calendar[i] + ":" + source.reasons[i] for i in (at - 1, at) if source.reasons[i] is not None])
            break
        if down:
            result.update(status="sameDayDown" if at == t else "priorDown", firstDownDate=calendar[at])
            break
    return result


def retention_path(source, calendar, down_index, decision_index):
    """Preserve unknown and independently known breaches without skipping slots."""
    result = {"status": "notApplicable", "referenceDate": None, "referenceClose": None,
              "referenceReason": None, "windowDates": [], "observations": [], "unknownReasons": [],
              "breachDates": [], "firstBreachDate": None, "retained": None}
    if down_index is None: return result
    p, t = down_index, decision_index
    if not 0 < p < t < len(calendar): raise ValueError("Retention requires 0<P<T")
    reference_reason, reference = source.reasons[p - 1], _close(source, p - 1)
    result.update(referenceDate=calendar[p - 1], referenceClose=reference, referenceReason=reference_reason,
                  windowDates=calendar[p - 1:t])
    if reference_reason is not None: result["unknownReasons"].append(calendar[p - 1] + ":" + reference_reason)
    for at in range(p, t):
        reason, close = source.reasons[at], _close(source, at)
        known = reference_reason is None and reason is None
        keep = close >= reference if known else None
        result["observations"].append({"date": calendar[at], "close": close, "reason": reason,
                                       "retained": keep, "breached": not keep if known else None})
        if reason is not None: result["unknownReasons"].append(calendar[at] + ":" + reason)
        if known and not keep: result["breachDates"].append(calendar[at])
    result["firstBreachDate"] = next(iter(result["breachDates"]), None)
    result["status"] = "unknown" if result["unknownReasons"] else "known"
    result["retained"] = not result["breachDates"] if result["status"] == "known" else None
    return result


def _formations(rows, calendar, position, start, end, symbols):
    # Same frozen parent identity contract as44, without its matching mechanism.
    if not isinstance(rows, list): raise ValueError("Ordered parent formations required")
    output, previous, identities = [], None, set()
    for row in rows:
        if not isinstance(row, dict): raise ValueError("Parent formation must be an object")
        day = _day(row.get("formationDate"))
        if day > end: continue
        rank, anchor = row.get("originalLeaderRank"), row.get("anchorSymbol")
        if day < start or day not in position or anchor not in symbols or type(rank) is not int or not 1 <= rank <= 5:
            raise ValueError("Parent formation calendar/anchor/rank differs")
        identity, key = row.get("parentEventId"), (day, rank, anchor)
        if identity != f"leaderLagFollowersV1:{day}:{anchor}" or identity in identities or (previous is not None and key <= previous):
            raise ValueError("Duplicate or misordered parent formation")
        previous = key; identities.add(identity); at = position[day]
        if at < 10 or row.get("peerFormationDate") != calendar[at - 10]: raise ValueError("Parent F differs")
        status, members = row.get("status"), row.get("members")
        if status not in ("formed", "locked", "notFormed") or not isinstance(members, list):
            raise ValueError("Parent disposition differs")
        if status == "formed" and (row.get("groupKind") != "formed" or len(members) != 20):
            raise ValueError("Formed parent requires20 frozen peers")
        seen = set()
        for i, member in enumerate(members, 1):
            symbol = member.get("symbol")
            if symbol not in symbols or symbol == anchor or symbol in seen or member.get("frozenMemberRank") != i:
                raise ValueError("Parent member identity/order differs")
            seen.add(symbol)
        output.append(deepcopy(row))
    return output


def _counts(rows):
    counter = Counter(row["commonStatus"] for row in rows)
    return {state: counter[state] for state in COMMON_STATES}


def build_leader_pullback_retention(dataset, parent_formations, parent_opportunities, *, start, end, progress=None):
    start, end = _day(start), _day(end)
    supplied = dataset.get("calendar")
    if (start > end or dataset.get("priceBasisId") != "raw-unadjusted" or not isinstance(supplied, list)
            or not supplied or supplied != sorted(set(supplied)) or any(_day(d) != d for d in supplied)):
        raise ValueError("Ordered raw source calendar required")
    calendar = [d for d in supplied if d <= end]; position = {d: i for i, d in enumerate(calendar)}
    targets = [d for d in calendar if start <= d <= end]
    if not targets: raise ValueError("No target sessions")
    securities = dataset.get("securities")
    if not isinstance(securities, list) or not securities: raise ValueError("Fixed source cohort required")
    originals = {}
    for security in securities:
        symbol = security.get("symbol")
        if not isinstance(symbol, str) or not symbol or symbol in originals: raise ValueError("Unique source identities required")
        originals[symbol] = security
    formations = _formations(parent_formations, calendar, position, start, end, originals)
    formation_map = {row["parentEventId"]: row for row in formations}
    sources = {}
    def source(symbol):
        if symbol not in sources: sources[symbol] = _Series(originals[symbol], calendar, end)
        return sources[symbol]
    if not isinstance(parent_opportunities, list): raise ValueError("Ordered parent opportunities required")
    opportunities, schedules = [], {mode: [] for mode in MODES}
    previous, identities, day_ranks, day_symbols = None, set(), set(), set()
    for n, parent in enumerate(parent_opportunities):
        if not isinstance(parent, dict): raise ValueError("Parent opportunity must be an object")
        day = _day(parent.get("decisionDate"))
        if day > end: continue
        if day not in position or day < start: raise ValueError("Parent decision outside target")
        member, anchor, formation = parent.get("memberSymbol"), parent.get("anchorSymbol"), parent.get("formationDate")
        rank, parent_id = parent.get("originalRank"), parent.get("signalId")
        if type(rank) is not int or not 1 <= rank <= 5 or member not in originals or anchor not in originals:
            raise ValueError("Parent opportunity identities/rank differ")
        key = (day, rank, member, parent_id)
        if (parent_id != f"{PARENT_ID}:{formation}:{anchor}:{member}" or parent_id in identities
                or (day, rank) in day_ranks or (day, member) in day_symbols or (previous is not None and key <= previous)):
            raise ValueError("Duplicate/misordered parent Top5")
        previous = key; identities.add(parent_id); day_ranks.add((day, rank)); day_symbols.add((day, member))
        form = formation_map.get(parent.get("parentEventId"))
        members = [r["symbol"] for r in form["members"]] if form else []
        if (not form or form["status"] != "formed" or form["formationDate"] != formation or form["anchorSymbol"] != anchor
                or member not in members or form["peerFormationDate"] != parent.get("peerFormationDate")
                or form["originalLeaderRank"] != parent.get("originalLeaderRank")
                or members.index(member) + 1 != parent.get("frozenMemberRank") or parent.get("signalDate") != day
                or not 1 <= position[day] - position[formation] <= 5):
            raise ValueError("Parent opportunity does not belong to frozen formed episode")
        t, a = position[day], position[formation]
        price = _price(source(member), calendar, t)
        if price != parent["priceEvidence"] or price["status"] != "known" or price["breakout"] is not True:
            raise ValueError("Parent first-breakout raw evidence differs")
        down = first_down(source(anchor), calendar, a, t)
        p = position[down["firstDownDate"]] if down["firstDownDate"] is not None else None
        path = retention_path(source(member), calendar, p if down["status"] == "priorDown" else None, t)
        status = down["status"] if down["status"] != "priorDown" else "memberPathUnknown" if path["status"] == "unknown" else "commonKnown"
        gates = {"control": True, "retained": path["retained"]} if status == "commonKnown" else {
            mode: None if status in ("firstDownUnknown", "memberPathUnknown") else False for mode in MODES}
        identity = f"{STRATEGY_ID}:{formation}:{anchor}:{member}"
        row = {"signalId": identity, "formationId": identity, "parentOpportunityId": parent_id,
               "parentEventId": parent["parentEventId"], "formationDate": formation, "peerFormationDate": parent["peerFormationDate"],
               "decisionDate": day, "signalDate": day, "symbol": member, "memberSymbol": member, "anchorSymbol": anchor,
               "originalLeaderRank": parent["originalLeaderRank"], "frozenMemberRank": parent["frozenMemberRank"],
               "originalRank": rank, "priceEvidence": deepcopy(price),
               "parentInitialAmountEvidenceSha256": _digest(parent["initialAmountEvidence"]),
               "parentAmountEvidenceSha256": _digest(parent["amountEvidence"]), "parentAmountStatus": parent["amountEvidence"]["status"],
               "firstDown": down, "retention": path, "commonStatus": status,
               "flags": {"firstDownUnknownReasons": down["reasons"][:], "memberUnknownReasons": path["unknownReasons"][:],
                         "memberBreachDates": path["breachDates"][:]},
               "diagnostics": {"formationToDownSlots": p - a if p is not None else None,
                               "downToDecisionSlots": t - p if p is not None else None,
                               "retentionObservationCount": len(path["observations"])}, **gates, "signals": {}}
        for mode in MODES:
            signal = {"signalId": identity, "strategyId": STRATEGY_ID, "symbol": member,
                      "signalDate": day, "originalRank": rank} if gates[mode] is True else None
            row["signals"][mode] = signal
            if signal is not None: schedules[mode].append(deepcopy(signal))
        opportunities.append(row)
        if progress is not None and ((n + 1) % 100 == 0 or n + 1 == len(parent_opportunities)):
            progress({"completedOpportunities": n + 1, "parentOpportunities": len(parent_opportunities)})
    daily, forms_by_day, opps_by_day = [], Counter(f["formationDate"] for f in formations), {}
    for row in opportunities: opps_by_day.setdefault(row["decisionDate"], []).append(row)
    for day in targets:
        rows = opps_by_day.get(day, [])
        daily.append({"date": day, "parentFormations": forms_by_day[day], "originalTop5": len(rows),
                      "commonStatusCounts": _counts(rows), "retainedKnown": sum(r["retained"] is True for r in rows),
                      "lostKnown": sum(r["control"] is True and r["retained"] is False for r in rows),
                      "signals": {m: sum(r["signals"][m] is not None for r in rows) for m in MODES}})
    formed = sum(f["status"] == "formed" for f in formations)
    counts = {"sourceSymbols": len(originals), "targetDays": len(targets), "parentFormations": len(formations),
              "formed": formed, "inheritedOther": len(formations) - formed, "originalTop5": len(opportunities),
              "opportunities": len(opportunities), "commonStatusCounts": _counts(opportunities),
              "retainedKnown": len(schedules["retained"]), "lostKnown": len(schedules["control"]) - len(schedules["retained"]),
              "signals": {m: len(schedules[m]) for m in MODES}, "notScheduled": {m: len(opportunities) - len(schedules[m]) for m in MODES}}
    return {"version": VERSION, "strategyId": STRATEGY_ID, "modes": list(MODES), "calendar": calendar,
            "targetDates": targets, "basis": deepcopy(BASIS), "counts": counts, "dailyRows": daily,
            "formations": formations, "opportunities": opportunities, "schedules": schedules,
            "certified": False, "targetAchieved": False}
