"""Prior-only outside-group matching on immutable first-breakout opportunities.

No parent clock/group reconstruction, account state, file I/O or outcome input.
All candidate comparisons can be streamed independently of the compact result.
"""
from collections import Counter
from copy import deepcopy
import math

from .research_leader_lag import _Series, _day, _digest, _finite, _positive
from .research_leader_amount_handoff import _amount, _ratio

VERSION = "leader-matched-anchor-v1"
STRATEGY_ID = "leaderMatchedAnchorV1"
PARENT_ID = "leaderAmountHandoffV1"
MODES = ("control", "realCross", "pseudoCross")
COMMON_STATES = ("matchUnavailable", "initialUnknown", "initialAbove", "decisionUnknown", "commonKnown")
OVERLAP_STATES = ("both", "realOnly", "pseudoOnly", "neither")
BASIS = {
    "population": "every immutable batch43 original member Top5; no parent owner, rank, lock or first-breakout recomputation",
    "matching": "one per formed parent(A,anchor), including no eventual opportunity; same cohort excluding anchor and all20 peers",
    "scale": "math.fsum exact positive amount A-20..A-1 divided by20; frozen raw OHLCV/status validity; A excluded",
    "distance": "abs(log(candidateMean20)-log(anchorMean20)); exact distance then symbol; no caliper or fallback",
    "common": "matchUnavailable > initialUnknown > initialAbove > decisionUnknown > commonKnown; independent flags retained",
    "initial": "member M3(A)<=both real and pseudo; original parent membership remains a separate inherited condition",
    "crossing": "member M3(T-1)<=anchor and member M3(T)>anchor, within commonKnown only; original T and ranks unchanged",
    "evidence": "M3 uses exact3 positive amount and valid OHLCV/status slots; recomputed inherited member/real evidence must equal parent",
    "interpretation": "outside this frozen group, not proven unrelated; scale matching does not balance theme, strength, news or investor identity",
    "scope": "raw surviving-cohort development history; all36 new accounts, no old control population or account reuse",
}


def _count(values, states):
    counts = Counter(values)
    return {state: counts[state] for state in states}


def _scale(source, calendar, at):
    evidence = _amount(source, calendar, at - 1, 20)
    mean = evidence["total"] / 20 if evidence["total"] is not None else None
    reasons = evidence["reasons"][:]
    if not reasons and not _positive(mean):
        reasons.append("nonfiniteOrUnderflowMeanAmount20")
        mean = None
    return {"status": "unknown" if reasons else "known", "meanAmount20": mean,
            "sumAmount20": evidence["total"], "reasons": reasons}


def _unavailable(calendar, at):
    dates = calendar[max(0, at - 2):at + 1]
    return {"date": calendar[at], "dates": dates, "amounts": [None] * len(dates),
            "total": None, "status": "unknown", "reasons": ["pseudoAnchorUnavailable"]}


def _parent_formations(rows, positions, start, end, symbols):
    if not isinstance(rows, list): raise ValueError("Ordered parent formations required")
    output, previous, identities = [], None, set()
    for row in rows:
        if not isinstance(row, dict): raise ValueError("Parent formation must be an object")
        day = _day(row.get("formationDate"))
        if day > end: continue
        rank, anchor = row.get("originalLeaderRank"), row.get("anchorSymbol")
        if day < start or day not in positions or anchor not in symbols or type(rank) is not int or not 1 <= rank <= 5:
            raise ValueError("Parent formation calendar/anchor/rank differs")
        identity = row.get("parentEventId")
        key = (day, rank, anchor)
        if identity != f"leaderLagFollowersV1:{day}:{anchor}" or identity in identities or (previous is not None and key <= previous):
            raise ValueError("Duplicate or misordered parent formation")
        previous = key; identities.add(identity)
        at = positions[day]
        if at < 10 or row.get("peerFormationDate") != list(positions)[at - 10]:
            raise ValueError("Parent peer formation clock differs")
        status, members = row.get("status"), row.get("members")
        if status not in ("formed", "locked", "notFormed") or not isinstance(members, list):
            raise ValueError("Parent formation disposition differs")
        if status == "formed" and (row.get("groupKind") != "formed" or len(members) != 20):
            raise ValueError("Formed parent requires exactly20 peers")
        seen = set()
        for rank, member in enumerate(members, 1):
            symbol = member.get("symbol")
            if symbol not in symbols or symbol == anchor or symbol in seen or member.get("frozenMemberRank") != rank:
                raise ValueError("Parent peer identities/order differ")
            seen.add(symbol)
        output.append(row)
    return output


def build_leader_matched_anchor(dataset, parent_formations, parent_opportunities, *, start, end,
                                progress=None, on_match_evidence=None):
    start, end = _day(start), _day(end)
    supplied = dataset.get("calendar")
    if (start > end or dataset.get("priceBasisId") != "raw-unadjusted" or not isinstance(supplied, list)
            or not supplied or supplied != sorted(set(supplied)) or any(_day(d) != d for d in supplied)):
        raise ValueError("Ordered raw source calendar required")
    calendar = [d for d in supplied if d <= end]
    targets = [d for d in calendar if start <= d <= end]
    if not targets: raise ValueError("No target sessions")
    position = {d: i for i, d in enumerate(calendar)}
    securities = dataset.get("securities")
    if not isinstance(securities, list) or not securities: raise ValueError("Fixed source cohort required")
    originals = {}
    for security in securities:
        symbol = security.get("symbol")
        if not isinstance(symbol, str) or not symbol or symbol in originals: raise ValueError("Unique source identities required")
        originals[symbol] = security
    symbols = sorted(originals)
    formations = _parent_formations(parent_formations, position, start, end, originals)
    sources = {s: _Series(originals[s], calendar, end) for s in symbols}
    matches, match_map = [], {}
    cached_day, scales = None, {}
    for n, parent in enumerate(formations):
        day, anchor = parent["formationDate"], parent["anchorSymbol"]
        at = position[day]
        members = [m["symbol"] for m in parent["members"]]
        match = {"parentEventId": parent["parentEventId"], "formationDate": day,
                 "peerFormationDate": parent["peerFormationDate"], "anchorSymbol": anchor,
                 "originalLeaderRank": parent["originalLeaderRank"], "parentStatus": parent["status"],
                 "groupSha256": parent["groupSha256"], "memberSymbols": members,
                 "windowDates20": calendar[max(0, at - 20):at], "matchStatus": "inheritedOther",
                 "pseudoSymbol": None, "anchorScale": None, "pseudoScale": None, "distance": None,
                 "candidateCount": 0, "eligibleCandidateCount": 0, "unknownCandidateCount": 0,
                 "excludedCandidateCount": 0, "candidateEvidenceSha256": None}
        if parent["status"] == "formed":
            if cached_day != day:
                cached_day, scales = day, {s: _scale(sources[s], calendar, at) for s in symbols}
            anchor_scale = scales[anchor]
            match["anchorScale"] = deepcopy(anchor_scale)
            candidates, eligible = [], []
            excluded = set(members) | {anchor}
            for symbol in symbols:
                scale = scales[symbol]
                if symbol in excluded:
                    status, reason, mean, distance = "excluded", "excludedAnchor" if symbol == anchor else "excludedPeer", None, None
                elif scale["status"] == "unknown":
                    status, reason, mean, distance = "unknown", "candidateScaleUnknown", None, None
                else:
                    status, reason, mean = "eligible", None, scale["meanAmount20"]
                    distance = abs(math.log(mean) - math.log(anchor_scale["meanAmount20"])) if anchor_scale["status"] == "known" else None
                    if distance is not None: eligible.append((distance, symbol))
                candidates.append({"symbol": symbol, "status": status, "reason": reason,
                                   "meanAmount20": mean, "distance": distance})
            if anchor_scale["status"] == "unknown": match["matchStatus"] = "anchorScaleUnknown"
            elif not eligible: match["matchStatus"] = "noEligibleCandidate"
            else:
                distance, selected = min(eligible)
                match.update(matchStatus="matched", pseudoSymbol=selected, distance=distance, pseudoScale=deepcopy(scales[selected]))
            counts = Counter(r["status"] for r in candidates)
            match.update(candidateCount=len(candidates), eligibleCandidateCount=counts["eligible"],
                         unknownCandidateCount=counts["unknown"], excludedCandidateCount=counts["excluded"],
                         candidateEvidenceSha256=_digest(candidates))
            evidence = {"parentEventId": parent["parentEventId"], "formationDate": day, "anchorSymbol": anchor,
                        "windowDates20": match["windowDates20"], "anchorScale": deepcopy(anchor_scale),
                        "candidates": candidates, "candidatesSha256": match["candidateEvidenceSha256"],
                        "selectedSymbol": match["pseudoSymbol"], "status": match["matchStatus"]}
            if on_match_evidence is not None: on_match_evidence(deepcopy(evidence))
        matches.append(match); match_map[parent["parentEventId"]] = match
        if progress is not None and ((n + 1) % 20 == 0 or n + 1 == len(formations)):
            progress({"stage": "matching", "completedFormations": n + 1, "parentFormations": len(formations)})
    if not isinstance(parent_opportunities, list): raise ValueError("Ordered parent opportunities required")
    opportunities, schedules, previous, identities, day_ranks, day_symbols = [], {m: [] for m in MODES}, None, set(), set(), set()
    for parent in parent_opportunities:
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
            raise ValueError("Duplicate/misordered parent original Top5")
        previous = key; identities.add(parent_id); day_ranks.add((day, rank)); day_symbols.add((day, member))
        match = match_map.get(parent.get("parentEventId"))
        if (not match or match["parentStatus"] != "formed" or match["formationDate"] != formation
                or match["anchorSymbol"] != anchor or member not in match["memberSymbols"]
                or match["peerFormationDate"] != parent.get("peerFormationDate")
                or match["originalLeaderRank"] != parent.get("originalLeaderRank")
                or match["memberSymbols"].index(member) + 1 != parent.get("frozenMemberRank")
                or parent.get("signalDate") != day or not 1 <= position[day] - position[formation] <= 5):
            raise ValueError("Parent opportunity does not belong to frozen formed episode")
        at, initial_at, pseudo = position[day], position[formation], match["pseudoSymbol"]
        values = {}
        for clock, index in (("initial", initial_at), ("previous", at - 1), ("current", at)):
            values[clock] = {"date": calendar[index], "member": _amount(sources[member], calendar, index),
                             "real": _amount(sources[anchor], calendar, index),
                             "pseudo": _amount(sources[pseudo], calendar, index) if pseudo is not None else _unavailable(calendar, index)}
        inherited = {"initial": parent["initialAmountEvidence"],
                     "previous": parent["amountEvidence"]["previous"], "current": parent["amountEvidence"]["current"]}
        for clock in inherited:
            saved = inherited[clock]
            if (saved["date"] != values[clock]["date"] or saved["member"] != values[clock]["member"]
                    or saved["anchor"] != values[clock]["real"]):
                raise ValueError("Recomputed member/real M3 differs from inherited evidence")
        initial_unknown = [role + ":" + reason for role in ("member", "real", "pseudo") for reason in values["initial"][role]["reasons"]]
        above = [role for role in ("real", "pseudo") if values["initial"]["member"]["status"] == "known"
                 and values["initial"][role]["status"] == "known"
                 and values["initial"]["member"]["total"] > values["initial"][role]["total"]]
        decision_unknown = [clock + ":" + role + ":" + reason for clock in ("previous", "current")
                            for role in ("member", "real", "pseudo") for reason in values[clock][role]["reasons"]]
        unavailable = match["matchStatus"] != "matched"
        status = ("matchUnavailable" if unavailable else "initialUnknown" if initial_unknown else "initialAbove" if above
                  else "decisionUnknown" if decision_unknown else "commonKnown")
        crossing = {}
        for role in ("real", "pseudo"):
            known = all(values[clock][r]["status"] == "known" for clock in ("previous", "current") for r in ("member", role))
            crossing[role] = (values["previous"]["member"]["total"] <= values["previous"][role]["total"]
                              and values["current"]["member"]["total"] > values["current"][role]["total"]) if known else None
        overlap = ("both" if crossing["real"] and crossing["pseudo"] else "realOnly" if crossing["real"]
                   else "pseudoOnly" if crossing["pseudo"] else "neither") if status == "commonKnown" else None
        identity = f"{STRATEGY_ID}:{formation}:{anchor}:{member}"
        out = {"signalId": identity, "formationId": identity, "parentOpportunityId": parent_id,
               "parentEventId": parent["parentEventId"], "formationDate": formation,
               "peerFormationDate": parent["peerFormationDate"], "decisionDate": day, "signalDate": day,
               "symbol": member, "memberSymbol": member, "anchorSymbol": anchor, "pseudoSymbol": pseudo,
               "originalLeaderRank": parent["originalLeaderRank"], "frozenMemberRank": parent["frozenMemberRank"],
               "originalRank": rank, "priceEvidence": deepcopy(parent["priceEvidence"]),
               "parentInitialAmountEvidenceSha256": _digest(parent["initialAmountEvidence"]),
               "parentAmountEvidenceSha256": _digest(parent["amountEvidence"]), "matchStatus": match["matchStatus"],
               "amountEvidence": values, "commonStatus": status,
               "flags": {"matchUnavailable": unavailable, "initialUnknownReasons": initial_unknown,
                         "initialAboveAnchors": above, "decisionUnknownReasons": decision_unknown},
               "crossing": crossing, "overlap": overlap,
               "diagnostics": {"prior20ScaleRatio": _ratio(match["pseudoScale"]["meanAmount20"], match["anchorScale"]["meanAmount20"])
                               if pseudo is not None else None, "matchDistance": match["distance"],
                               "initialPseudoRealM3Ratio": _ratio(values["initial"]["pseudo"]["total"], values["initial"]["real"]["total"]),
                               "initialMemberRealM3Ratio": _ratio(values["initial"]["member"]["total"], values["initial"]["real"]["total"]),
                               "initialMemberPseudoM3Ratio": _ratio(values["initial"]["member"]["total"], values["initial"]["pseudo"]["total"])}}
        gates = {"control": True, "realCross": crossing["real"], "pseudoCross": crossing["pseudo"]} if status == "commonKnown" else {
            mode: False if status == "initialAbove" else None for mode in MODES}
        out.update(gates)
        out["signals"] = {}
        for mode in MODES:
            signal = {"signalId": identity, "strategyId": STRATEGY_ID, "symbol": member,
                      "signalDate": day, "originalRank": rank} if gates[mode] is True else None
            out["signals"][mode] = signal
            if signal is not None: schedules[mode].append(deepcopy(signal))
        opportunities.append(out)
    daily = []
    match_days, opportunity_days = {}, {}
    for row in matches: match_days.setdefault(row["formationDate"], []).append(row)
    for row in opportunities: opportunity_days.setdefault(row["decisionDate"], []).append(row)
    match_states = ("matched", "anchorScaleUnknown", "noEligibleCandidate", "inheritedOther")
    for day in targets:
        ms, os = match_days.get(day, []), opportunity_days.get(day, [])
        daily.append({"date": day, "parentFormations": len(ms), "matchStatusCounts": _count((r["matchStatus"] for r in ms), match_states),
                      "originalTop5": len(os), "commonStatusCounts": _count((r["commonStatus"] for r in os), COMMON_STATES),
                      "overlapCounts": _count((r["overlap"] for r in os), OVERLAP_STATES),
                      "signals": {mode: sum(r["signals"][mode] is not None for r in os) for mode in MODES}})
    match_counts = _count((r["matchStatus"] for r in matches), match_states)
    distinct = len({r["pseudoSymbol"] for r in matches if r["pseudoSymbol"] is not None})
    counts = {"sourceSymbols": len(symbols), "targetDays": len(targets), "parentFormations": len(matches),
              "formed": len(matches) - match_counts["inheritedOther"], **match_counts,
              "originalTop5": len(opportunities), "opportunities": len(opportunities),
              "commonStatusCounts": _count((r["commonStatus"] for r in opportunities), COMMON_STATES),
              "overlapCounts": _count((r["overlap"] for r in opportunities), OVERLAP_STATES),
              "distinctPseudoAnchors": distinct, "reusedPseudoAssignments": match_counts["matched"] - distinct,
              "signals": {mode: len(schedules[mode]) for mode in MODES},
              "notScheduled": {mode: len(opportunities) - len(schedules[mode]) for mode in MODES}}
    return {"version": VERSION, "strategyId": STRATEGY_ID, "modes": list(MODES), "calendar": calendar,
            "targetDates": targets, "basis": deepcopy(BASIS), "counts": counts, "dailyRows": daily,
            "matches": matches, "opportunities": opportunities, "schedules": schedules,
            "certified": False, "targetAchieved": False}
