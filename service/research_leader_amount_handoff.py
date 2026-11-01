"""First member price breakouts and a causal traded-value handoff.

The parent leader/peer identities are immutable inputs. No rank reconstruction,
account state, outcomes, file I/O, parameter search or input mutation occurs here.
"""
from collections import Counter
from copy import deepcopy
import math

from .research_leader_lag import _Series, _day, _digest, _finite, _positive

VERSION = "leader-amount-handoff-v1"
STRATEGY_ID = "leaderAmountHandoffV1"
PARENT_ID = "leaderLagFollowersV1"
MODES = ("control", "handoff")
BASIS = {
    "source": "unchanged six board-local cohorts and audited parent leader events; no new group or rank calculation",
    "price": "frozen research_leader_lag._Series exact raw positive OHLCV/envelope/status validity",
    "amount": "M3=math.fsum(explicit positive CNY amount on d-2..d); all three price/status bars also valid",
    "formation": "A=parent formationDate; F=parent peerFormationDate=A-10; fixed20 ordered peers",
    "lock": "earliest anchor event owns A..A+5 even if group incomplete; suppressed events cannot extend",
    "initial": "jointly known member M3(A)<=anchor M3(A); equality admitted",
    "firstBreakout": "first close above preceding five exact highs during A+1..A+5; unknown price terminates",
    "handoff": "member M3(T-1)<=anchor M3(T-1) and member M3(T)>anchor M3(T)",
    "selection": "all raw first breakouts, earliest formation/leader/member order, first owner per member, Top5 before amount masks",
    "diagnostics": "M3(T)/(3*mean amount T-20..T-1); diagnostics never affect admission",
    "limitations": "traded-value activity proxy, not turnover, actor identity or causal capital transfer; raw surviving-cohort development history",
}


def _ratio(left, right):
    if not _positive(left) or not _positive(right):
        return None
    result = left / right
    return result if _finite(result) else None


def _amount(source, calendar, index, slots=3):
    dates = calendar[max(0, index - slots + 1):index + 1] if index >= 0 else []
    amounts, reasons = [], []
    if len(dates) != slots:
        reasons.append("insufficientCalendarHistory")
    for at in range(max(0, index - slots + 1), index + 1):
        reason = source.reasons[at]
        bar = source.bars[at]
        amount = bar.get("amount") if bar else None
        amounts.append(amount if _finite(amount) else None)
        if reason is not None:
            reasons.append(calendar[at] + ":" + reason)
        if not _positive(amount):
            reasons.append(calendar[at] + ":nonpositiveOrUnknownAmount")
    total = None
    if not reasons:
        try:
            total = math.fsum(amounts)
        except (OverflowError, ValueError):
            pass
        if not _positive(total):
            total = None
            reasons.append("nonfiniteAmountSum")
    return {"date": calendar[index] if index >= 0 else None, "dates": dates, "amounts": amounts,
            "total": total, "status": "unknown" if reasons else "known", "reasons": reasons}


def _price(source, calendar, index):
    reason = source.window_reason(index, 6)
    bar = source.bars[index]
    high = max(b["high"] for b in source.bars[index - 5:index]) if reason is None else None
    close = bar.get("close") if bar else None
    return {"date": calendar[index], "windowDates6": calendar[max(0, index - 5):index + 1],
            "prior5High": high, "close": close if _finite(close) else None,
            "breakout": close > high if reason is None else None,
            "status": "known" if reason is None else "unknown", "reason": reason}


def _order(row):
    return (row["formationDate"], row["originalLeaderRank"], row["anchorSymbol"],
            row["frozenMemberRank"], row["memberSymbol"])


def _events(events, calendar, start, end, symbols):
    if not isinstance(events, list):
        raise ValueError("Parent leader events must be an ordered list")
    position = {day: i for i, day in enumerate(calendar)}
    result, previous, identities, slots = [], None, set(), set()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Parent leader event must be an object")
        day = _day(event.get("formationDate"))
        if day > end:
            continue  # No later numeric/group fields are inspected.
        if day < start or day not in position:
            raise ValueError("Parent activation outside target calendar")
        anchor, rank = event.get("anchorSymbol"), event.get("originalLeaderRank")
        if anchor not in symbols or type(rank) is not int or not 1 <= rank <= 5:
            raise ValueError("Invalid parent anchor/rank")
        identity = event.get("eventId")
        if identity != f"{PARENT_ID}:{day}:{anchor}" or identity in identities or (day, rank) in slots:
            raise ValueError("Contradictory or duplicate parent identity/rank")
        key = (day, rank, anchor)
        if previous is not None and key <= previous:
            raise ValueError("Parent events must already be ordered")
        previous = key; identities.add(identity); slots.add((day, rank))
        at = position[day]
        if at < 10 or event.get("peerFormationDate") != calendar[at - 10]:
            raise ValueError("Parent F must be exactly A minus10 source slots")
        group = event.get("groupSummary")
        if not isinstance(group, dict) or group.get("anchorSymbol") != anchor or group.get("peerFormationDate") != event["peerFormationDate"]:
            raise ValueError("Parent group identity mismatch")
        members, count, kind = group.get("members"), group.get("selectedCount"), group.get("kind")
        if (not isinstance(members, list) or type(count) is not int or count != len(members)
                or not 0 <= count <= 20 or kind not in ("formed", "incomplete", "unknown")
                or group.get("requiredCount") != 20 or (kind == "formed" and count != 20)):
            raise ValueError("Contradictory parent group metadata")
        seen = set()
        for member_rank, member in enumerate(members, 1):
            symbol = member.get("symbol")
            if symbol not in symbols or symbol == anchor or symbol in seen or member.get("frozenMemberRank") != member_rank:
                raise ValueError("Invalid frozen member identity/order")
            seen.add(symbol)
        if not isinstance(event.get("groupSha256"), str) or len(event["groupSha256"]) != 64:
            raise ValueError("Parent group hash missing")
        result.append(event)
    return result


def build_leader_amount_handoff(dataset, leader_events, *, start, end, progress=None, on_evidence=None):
    """Stream daily transitions separately; return complete compact episode layers."""
    start, end = _day(start), _day(end)
    if start > end or dataset.get("priceBasisId") != "raw-unadjusted":
        raise ValueError("Ordered dates and explicitly raw-unadjusted data required")
    supplied = dataset.get("calendar")
    if (not isinstance(supplied, list) or not supplied or supplied != sorted(set(supplied))
            or any(_day(d) != d for d in supplied)):
        raise ValueError("Unique increasing source calendar required")
    calendar = [d for d in supplied if d <= end]
    targets = [d for d in calendar if start <= d <= end]
    if not targets:
        raise ValueError("No target sessions")
    securities = dataset.get("securities")
    if not isinstance(securities, list) or not securities:
        raise ValueError("Fixed cohort required")
    originals = {}
    for security in securities:
        symbol = security.get("symbol")
        if not isinstance(symbol, str) or not symbol or symbol in originals:
            raise ValueError("Unique nonempty security identities required")
        originals[symbol] = security
    events = _events(leader_events, calendar, start, end, originals)
    needed = {e["anchorSymbol"] for e in events}
    needed.update(m["symbol"] for e in events for m in e["groupSummary"]["members"])
    sources = {s: _Series(originals[s], calendar, end) for s in sorted(needed)}
    by_day = {}
    for event in events:
        by_day.setdefault(event["formationDate"], []).append(event)
    position = {d: i for i, d in enumerate(calendar)}
    amounts = {}
    def amount(symbol, index):
        key = (symbol, index)
        if key not in amounts:
            amounts[key] = _amount(sources[symbol], calendar, index)
        return deepcopy(amounts[key])
    def pair(member, anchor, index):
        return {"date": calendar[index], "member": amount(member, index), "anchor": amount(anchor, index)}
    def diagnostics(row, index):
        out = {}
        for role, symbol in (("member", row["memberSymbol"]), ("anchor", row["anchorSymbol"])):
            prior = _amount(sources[symbol], calendar, index - 1, 20)
            mean = prior["total"] / 20 if prior["total"] is not None else None
            denominator = mean * 3 if mean is not None else None
            out[role] = {"prior20Dates": prior["dates"], "prior20MeanAmount": mean,
                         "ownRelativeAmount": _ratio(row["amountEvidence"]["current"][role]["total"], denominator),
                         "reasons": prior["reasons"]}
        out["pairRatios"] = {name: _ratio(e["member"]["total"], e["anchor"]["total"])
                             for name, e in (("initial", row["initialAmountEvidence"]),
                                             ("previous", row["amountEvidence"]["previous"]),
                                             ("current", row["amountEvidence"]["current"]))}
        return out
    locks, active, daily, formations, episodes, confirmations, opportunities = {}, [], [], [], [], [], []
    schedules = {m: [] for m in MODES}
    for day in targets:
        index = position[day]
        observations, todays_raw, todays_forms = [], [], []
        still_active = []
        for episode in active:
            price = _price(sources[episode["memberSymbol"]], calendar, index)
            offset = index - position[episode["formationDate"]]
            state = "observing"
            if price["status"] == "unknown": state = "priceUnknown"
            elif price["breakout"]: state = "firstBreakout"
            elif offset == 5: state = "expired"
            episode["observedSessions"] += 1
            episode["lastObservationDate"] = day
            observation = {"episodeId": episode["episodeId"], "date": day, "offset": offset,
                           "priceEvidence": price, "state": state}
            if state == "firstBreakout":
                previous = pair(episode["memberSymbol"], episode["anchorSymbol"], index - 1)
                current = pair(episode["memberSymbol"], episode["anchorSymbol"], index)
                reasons = [clock + ":" + role + ":" + reason for clock, value in (("previous", previous), ("current", current))
                           for role in ("member", "anchor") for reason in value[role]["reasons"]]
                known = not reasons
                crossing = (previous["member"]["total"] <= previous["anchor"]["total"]
                            and current["member"]["total"] > current["anchor"]["total"]) if known else None
                raw = {k: deepcopy(episode[k]) for k in ("episodeId", "parentEventId", "formationDate", "peerFormationDate",
                       "anchorSymbol", "memberSymbol", "originalLeaderRank", "frozenMemberRank", "initialAmountEvidence")}
                raw.update({"formationId": episode["episodeId"], "signalId": episode["episodeId"],
                            "symbol": episode["memberSymbol"], "decisionDate": day, "signalDate": day,
                            "priceEvidence": price, "amountEvidence": {"previous": previous, "current": current,
                            "status": "known" if known else "unknown", "reasons": reasons, "crossing": crossing},
                            "ownershipStatus": None, "ownerEpisodeId": None, "originalRank": None, "selectionStatus": None})
                todays_raw.append(raw)
                observation["amountEvidence"] = deepcopy(raw["amountEvidence"])
            if state != "observing":
                episode["terminalStatus"], episode["terminalDate"] = state, day
            else:
                still_active.append(episode)
            observations.append(observation)
        active = still_active
        # Ownership and original Top5 are deliberately established before masks.
        owners = {}
        for raw in sorted(todays_raw, key=_order):
            symbol = raw["memberSymbol"]
            if symbol in owners:
                raw.update(ownershipStatus="duplicateSuppressed", ownerEpisodeId=owners[symbol]["episodeId"],
                           selectionStatus="duplicateSuppressed")
            else:
                owners[symbol] = raw
                rank = len(owners)
                raw.update(ownershipStatus="owner", ownerEpisodeId=raw["episodeId"], originalRank=rank,
                           selectionStatus="selected" if rank <= 5 else "rankExcluded")
                if rank <= 5:
                    known = raw["amountEvidence"]["status"] == "known"
                    selected = deepcopy(raw)
                    selected["diagnostics"] = diagnostics(raw, index)
                    selected["control"] = True if known else None
                    selected["handoff"] = raw["amountEvidence"]["crossing"]
                    selected["signals"] = {}
                    for mode in MODES:
                        signal = {"signalId": raw["signalId"], "strategyId": STRATEGY_ID, "symbol": symbol,
                                  "signalDate": day, "originalRank": rank} if selected[mode] is True else None
                        selected["signals"][mode] = signal
                        if signal is not None: schedules[mode].append(deepcopy(signal))
                    opportunities.append(selected)
            confirmations.append(deepcopy(raw))
        # Existing episodes are advanced before today's new A formations.
        for event in by_day.get(day, []):
            anchor, parent = event["anchorSymbol"], event["eventId"]
            prior_lock = locks.get(anchor)
            locked = prior_lock is not None and index <= prior_lock["throughIndex"]
            group = event["groupSummary"]
            status = "locked" if locked else "formed" if group["kind"] == "formed" else "notFormed"
            formation = {"parentEventId": parent, "formationDate": day, "peerFormationDate": event["peerFormationDate"],
                         "anchorSymbol": anchor, "originalLeaderRank": event["originalLeaderRank"],
                         "groupSha256": event["groupSha256"], "groupKind": group["kind"],
                         "members": deepcopy(group["members"]), "status": status,
                         "lockOwnerEventId": prior_lock["eventId"] if locked else parent,
                         "lockThroughIndex": prior_lock["throughIndex"] if locked else index + 5,
                         "createdMemberEpisodes": 20 if status == "formed" else 0}
            if not locked: locks[anchor] = {"eventId": parent, "throughIndex": index + 5}
            formations.append(formation); todays_forms.append(formation)
            if status != "formed": continue
            for member in group["members"]:
                symbol = member["symbol"]
                initial = pair(symbol, anchor, index)
                known = all(initial[role]["status"] == "known" for role in ("member", "anchor"))
                state = ("observing" if initial["member"]["total"] <= initial["anchor"]["total"] else "initialAbove") if known else "initialUnknown"
                identity = f"{STRATEGY_ID}:{day}:{anchor}:{symbol}"
                episode = {"episodeId": identity, "parentEventId": parent, "formationDate": day,
                           "peerFormationDate": event["peerFormationDate"], "anchorSymbol": anchor, "memberSymbol": symbol,
                           "originalLeaderRank": event["originalLeaderRank"], "frozenMemberRank": member["frozenMemberRank"],
                           "initialAmountEvidence": initial, "initialStatus": state, "observedSessions": 0,
                           "lastObservationDate": None, "terminalStatus": state if state != "observing" else None,
                           "terminalDate": day if state != "observing" else None}
                episodes.append(episode)
                if state == "observing": active.append(episode)
        censored = []
        if day == targets[-1]:
            for episode in active:
                episode["terminalStatus"], episode["terminalDate"] = "censored", day
                censored.append(episode["episodeId"])
            active = []
        evidence = {"date": day, "formations": deepcopy(todays_forms), "memberObservations": observations,
                    "rawConfirmationIds": [r["episodeId"] for r in sorted(todays_raw, key=_order)], "censoredEpisodeIds": censored}
        row = {"date": day, "sourceEvents": len(todays_forms), "formationStatusCounts": dict(sorted(Counter(f["status"] for f in todays_forms).items())),
               "memberObservationCounts": dict(sorted(Counter(o["state"] for o in observations).items())),
               "rawConfirmations": len(todays_raw), "duplicateSuppressed": len(todays_raw) - len(owners),
               "uniqueOwners": len(owners), "selectedTop5": min(5, len(owners)), "rankExcluded": max(0, len(owners) - 5),
               "censored": len(censored), "evidenceSha256": _digest(evidence)}
        daily.append(row)
        if on_evidence is not None: on_evidence(deepcopy(evidence))
        if progress is not None and (len(daily) % 20 == 0 or day == targets[-1]):
            progress({"stage": "handoff", "completedDays": len(daily), "targetDays": len(targets),
                      "formations": len(formations), "memberEpisodes": len(episodes), "opportunities": len(opportunities)})
    initial_counts = Counter(e["initialStatus"] for e in episodes)
    terminal_counts = Counter(e["terminalStatus"] for e in episodes)
    joint_known = sum(o["control"] is True for o in opportunities)
    true_count = sum(o["handoff"] is True for o in opportunities)
    counts = {"sourceSymbols": len(originals), "targetDays": len(targets), "sourceEvents": len(formations),
              "lockedEvents": sum(f["status"] == "locked" for f in formations),
              "unlockedFormed": sum(f["status"] == "formed" for f in formations),
              "unlockedNotFormed": sum(f["status"] == "notFormed" for f in formations),
              "memberEpisodes": len(episodes), "initialUnknown": initial_counts["initialUnknown"],
              "initialAbove": initial_counts["initialAbove"], "observedMembers": initial_counts["observing"],
              **{k: terminal_counts[k] for k in ("priceUnknown", "firstBreakout", "expired", "censored")},
              "duplicateSuppressed": sum(r["ownershipStatus"] == "duplicateSuppressed" for r in confirmations),
              "uniqueOwners": sum(r["ownershipStatus"] == "owner" for r in confirmations),
              "selectedTop5": len(opportunities), "rankExcluded": sum(r["selectionStatus"] == "rankExcluded" for r in confirmations),
              "jointKnown": joint_known, "jointUnknown": len(opportunities) - joint_known,
              "crossingTrue": true_count, "crossingFalse": joint_known - true_count,
              "opportunities": len(opportunities), "signals": {m: len(schedules[m]) for m in MODES},
              "notScheduled": {m: len(opportunities) - len(schedules[m]) for m in MODES}}
    return {"version": VERSION, "strategyId": STRATEGY_ID, "modes": list(MODES), "calendar": calendar,
            "targetDates": targets, "basis": deepcopy(BASIS), "counts": counts, "dailyRows": daily,
            "formations": formations, "memberEpisodes": episodes, "rawConfirmations": confirmations,
            "opportunities": opportunities, "schedules": schedules, "certified": False, "targetAchieved": False}
