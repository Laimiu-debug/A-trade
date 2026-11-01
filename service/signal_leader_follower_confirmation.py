"""Frozen-group member first confirmation; causal, position-free state machine.

Source opportunity date A is the activation date. Its earlier group-formation
date F is only provenance. Parent calculations are inherited, not recomputed.
The member shadow runs independently of anchor cancellation, then ownership and
global Top5 precede the anchor mask. No price, trade or future outcome is read.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
import math

VERSION = "raw-leader-follower-confirmation-v1"
CANDIDATE_ID = "rawLeaderFollowerConfirmation"
MEMBER_CONTROL_ID = "control_rawLeaderFollowerConfirmationMember"
ANCHOR_CONTROL_ID = "control_rawLeaderFollowerConfirmationAnchor"
GROUPS = (CANDIDATE_ID, MEMBER_CONTROL_ID, ANCHOR_CONTROL_ID)
PARENT_VERSION = "raw-persistent-price-leader-v1"
PARENT_ID = "rawPersistentPriceLeaderBreakoutDiagnostic"
PEER_VERSION = "raw-leader-peer-expansion-v1"
Q_FIELDS = ("meanStrengthPercentile20", "minimumStrengthPercentile20", "relativeReturn60Percentile",
            "closePathMaxDrawdown20", "signedPathEfficiency20")
GROUP_SIZE, OBSERVATION_SESSIONS, FORMATION_LAG, TOP_N = 20, 5, 10, 5


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Expected canonical ISO date")
    return value


def _symbol(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty symbol")
    return value


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _q(kind, passed):
    if kind not in ("signaled", "notTriggered") or any(type(passed[k]) is not bool for k in Q_FIELDS):
        return "unknown"
    return "true" if all(passed.values()) else "false"


def project_parent(outcome):
    """Project registered eligibleParentQ, keeping ineligible distinct in evidence.

Exact boolean predicate values and the inherited exclusive kind determine Q.
This deliberately differs from the eligibility-independent market-panel Q.
Malformed source identity is fatal; incomplete predicate evidence is unknown.
"""
    if not isinstance(outcome, dict):
        raise ValueError("Parent outcome must be an object")
    if outcome.get("version") != PARENT_VERSION or outcome.get("candidateId") != PARENT_ID:
        raise ValueError("Parent version/candidate identity differs")
    symbol, day = _symbol(outcome.get("symbol")), _day(outcome.get("date"))
    predicates = outcome.get("predicates")
    predicates = predicates if isinstance(predicates, dict) else {}
    passed = {}
    for key in Q_FIELDS:
        item = predicates.get(key)
        value = item.get("passed") if isinstance(item, dict) else None
        passed[key] = value if type(value) is bool else None
    kind, reason = outcome.get("kind"), outcome.get("reason")
    if kind is not None and not isinstance(kind, str):
        raise ValueError("Parent kind must be a string or missing")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("Parent reason must be a string or missing")
    return {"version": VERSION, "parentVersion": PARENT_VERSION, "parentCandidateId": PARENT_ID,
        "symbol": symbol, "date": day, "kind": kind, "reason": reason,
        "eligibleParentQ": _q(kind, passed), "breakout": kind == "signaled", "fivePassed": passed,
        "outcomeSha256": _digest(outcome)}


def _validate_parent(row, symbol, day):
    if (not isinstance(row, dict) or row.get("version") != VERSION or row.get("parentVersion") != PARENT_VERSION
        or row.get("parentCandidateId") != PARENT_ID or row.get("symbol") != symbol or row.get("date") != day
        or not _sha(row.get("outcomeSha256"))):
        raise ValueError("Compact parent identity differs")
    passed = row.get("fivePassed")
    if (not isinstance(passed, dict) or set(passed) != set(Q_FIELDS)
        or any(value is not None and type(value) is not bool for value in passed.values())
        or row.get("eligibleParentQ") != _q(row.get("kind"), passed)
        or type(row.get("breakout")) is not bool or row["breakout"] != (row.get("kind") == "signaled")):
        raise ValueError("Compact parent projection differs")


def _raw_key(row):
    return (row["formationDate"], row["formationParentRank"], row["anchorSymbol"],
            row["frozenMemberRank"], row["memberSymbol"])


def _select(day, raw):
    ordered = sorted(deepcopy(raw), key=_raw_key)
    by_member = {}
    for item in ordered:
        by_member.setdefault(item["memberSymbol"], []).append(item)
    owned, ownership = [], []
    for member, items in by_member.items():
        owner = items[0]
        owned.append(owner)
        ownership.append({"memberSymbol": member, "ownerRawConfirmationId": owner["rawConfirmationId"],
            "ownerEpisodeId": owner["episodeId"], "allRawConfirmationIds": [r["rawConfirmationId"] for r in items],
            "discardedRawConfirmationIds": [r["rawConfirmationId"] for r in items[1:]]})
    ranked = [{**row, "rank": rank, "selectedTop5": rank <= TOP_N} for rank, row in enumerate(owned, 1)]
    slots, anchors = [], {}
    for row in ranked[:TOP_N]:
        admitted = row["anchorGate"] == "alive"
        first = anchors.get(row["anchorSymbol"])
        if not admitted:
            disposition = "anchorCancelled" if row["anchorGate"] == "cancelled" else "anchorUnknown"
        elif first is not None:
            disposition = "mappedAnchorCollision"
        else:
            disposition = "admitted"
            anchors[row["anchorSymbol"]] = row["rank"]
        slot = {key: deepcopy(row[key]) for key in ("date", "memberSymbol", "anchorSymbol", "episodeId", "rawConfirmationId",
            "formationDate", "formationIndex", "formationParentRank", "peerFormationDate", "frozenMemberRank", "groupSha256",
            "anchorGate", "anchorGateTerminal", "rank")}
        slot.update(candidateAdmitted=admitted, memberBaselineAdmitted=True,
            anchorControlAdmitted=disposition == "admitted", anchorControlDisposition=disposition,
            anchorControlRetainedRank=anchors.get(row["anchorSymbol"]) if admitted else None)
        slots.append(slot)
    signals = []
    for sid in GROUPS:
        field = {CANDIDATE_ID: "candidateAdmitted", MEMBER_CONTROL_ID: "memberBaselineAdmitted",
                 ANCHOR_CONTROL_ID: "anchorControlAdmitted"}[sid]
        for slot in slots:
            if slot[field]:
                signals.append({**deepcopy(slot), "version": VERSION, "strategyId": sid,
                    "symbol": slot["anchorSymbol"] if sid == ANCHOR_CONTROL_ID else slot["memberSymbol"],
                    "signalDate": day, "selectedTop5": True, "eligible": True,
                    "certified": False, "targetAchieved": False, "actionEvidence": "unverified"})
    return ordered, {"date": day, "rawConfirmationCount": len(ordered), "uniqueMemberCount": len(owned),
        "fullRanking": ranked, "ownership": ownership, "slots": slots, "signals": signals,
        "policyCounts": {sid: sum(row["strategyId"] == sid for row in signals) for sid in GROUPS},
        "activeExclusionApplied": False, "noBackfill": True, "certified": False, "targetAchieved": False}


class FollowerConfirmationStudy:
    """Consume the complete ordered target calendar once; never inspect positions.

parent_by_symbol holds project_parent outputs for every fixed symbol each day.
source_formations holds that day's original peer-group log rows, including
    locked-out opportunities. formation_calendar supplies source warmup dates for
    F=A-10; episode offsets still start at target A. No expansion status is read.
"""

    def __init__(self, calendar, symbols, *, formation_calendar=None):
        days, names = list(calendar), list(symbols)
        if not days or days != sorted(set(days)) or any(_day(d) != d for d in days):
            raise ValueError("Calendar must be nonempty, ordered and unique")
        if not names or names != sorted(set(names)) or any(_symbol(s) != s for s in names):
            raise ValueError("Symbols must be nonempty, sorted and unique")
        full = days[:] if formation_calendar is None else list(formation_calendar)
        if not full or full != sorted(set(full)) or any(_day(d) != d for d in full) or not set(days).issubset(full):
            raise ValueError("Formation calendar must contain every target date in ordered unique ISO form")
        positions = {day: index for index, day in enumerate(full)}
        if full[positions[days[0]]:positions[days[-1]]+1] != days:
            raise ValueError("Target dates must form one continuous source-calendar interval")
        self._calendar, self._symbols = tuple(days), tuple(names)
        self._formation_calendar, self._formation_positions = tuple(full), positions
        self._symbol_set = set(names)
        self._index, self._finished = -1, False
        self._episodes, self._locks = [], {}

    @property
    def episodes(self):
        return deepcopy(self._episodes)

    def _validate_formations(self, day, index, rows, parents):
        if not isinstance(rows, (list, tuple)) or len(rows) > TOP_N:
            raise ValueError("Provide all ordered original Top5 formation rows")
        seen = set()
        source_index = self._formation_positions[day]
        for rank, row in enumerate(rows, 1):
            if (not isinstance(row, dict) or row.get("date") != day or type(row.get("rank")) is not int or row["rank"] != rank
                or row.get("symbol") not in self._symbol_set or row["symbol"] in seen
                or source_index < FORMATION_LAG
                or row.get("formationDate") != self._formation_calendar[source_index-FORMATION_LAG]):
                raise ValueError("Source opportunity identity/date/F/rank differs")
            seen.add(row["symbol"])
            parent = parents[row["symbol"]]
            if parent["eligibleParentQ"] != "true" or parent["kind"] != "signaled":
                raise ValueError("Source A anchor is not a confirmed original parent signal")
            group = row.get("group")
            if (not isinstance(group, dict) or group.get("version") != PEER_VERSION or group.get("anchor") != row["symbol"]
                or group.get("formationDate") != row["formationDate"] or row.get("groupSha256") != _digest(group)
                or group.get("kind") not in ("formed", "unevaluable")):
                raise ValueError("Source frozen group identity/hash differs")
            members = group.get("members")
            if (not isinstance(members, list) or any(not isinstance(s, str) or s not in self._symbol_set for s in members)
                or len(members) != len(set(members)) or row["symbol"] in members or len(members) > GROUP_SIZE
                or type(group.get("selectedCount")) is not int or group["selectedCount"] != len(members)
                or type(group.get("requiredCount")) is not int or group["requiredCount"] != GROUP_SIZE):
                raise ValueError("Source frozen member identity/count differs")
            if group["kind"] == "formed":
                correlations = group.get("correlations")
                if (len(members) != GROUP_SIZE or not isinstance(correlations, dict) or set(correlations) != set(members)
                    or any(type(v) not in (int, float) or not math.isfinite(v) or not .3 <= v <= 1 for v in correlations.values())):
                    raise ValueError("Formed source group must contain twenty valid frozen members")

    def _advance(self, episode, day, index, parents):
        offset = index-episode["formationIndex"]
        if not 1 <= offset <= OBSERVATION_SESSIONS:
            return [], []
        transitions, raw = [], []
        anchor = parents[episode["anchorSymbol"]]
        before = episode["anchorGate"]
        if before == "alive" and anchor["eligibleParentQ"] != "true":
            episode["anchorGate"] = "cancelled" if anchor["eligibleParentQ"] == "false" else "unknown"
            episode["anchorGateTerminal"] = {"date": day, "index": index, "eligibleParentQ": anchor["eligibleParentQ"],
                "reason": anchor["reason"], "parentKind": anchor["kind"], "parentOutcomeSha256": anchor["outcomeSha256"]}
        transitions.append({"type": "anchor", "date": day, "index": index, "offset": offset, "episodeId": episode["episodeId"],
            "anchorSymbol": episode["anchorSymbol"], "fromState": before, "toState": episode["anchorGate"],
            "reason": "continuousTrue" if episode["anchorGate"] == "alive" else "firstAnchorTerminal" if before == "alive" else "terminalRetained",
            "parent": deepcopy(anchor), "anchorGateTerminal": deepcopy(episode["anchorGateTerminal"])})
        for member in episode["members"]:
            parent, previous = parents[member["symbol"]], member["state"]
            reason = "terminalRetained"
            if previous == "observing":
                q = parent["eligibleParentQ"]
                if q == "unknown":
                    member["state"], reason = "memberUnknown", "firstMemberUnknown"
                elif q == "true":
                    member["state"] = "confirmed" if parent["breakout"] else "confirmedWithoutBreakout"
                    reason = "firstTrueWithBreakout" if parent["breakout"] else "firstTrueWithoutBreakout"
                elif offset == OBSERVATION_SESSIONS:
                    member["state"], reason = "expired", "observationWindowExpired"
                else:
                    reason = "stillFalse"
                if member["state"] != "observing":
                    member.update(terminalDate=day, terminalIndex=index, terminalReason=reason,
                                  terminalParent=deepcopy(parent))
                if member["state"] == "confirmed":
                    raw.append({"version": VERSION, "date": day, "episodeId": episode["episodeId"],
                        "rawConfirmationId": episode["episodeId"]+":"+member["symbol"]+":"+day,
                        "anchorSymbol": episode["anchorSymbol"], "memberSymbol": member["symbol"],
                        "formationDate": episode["formationDate"], "formationIndex": episode["formationIndex"],
                        "formationParentRank": episode["formationParentRank"], "peerFormationDate": episode["peerFormationDate"],
                        "frozenMemberRank": member["frozenMemberRank"], "groupSha256": episode["groupSha256"],
                        "anchorGate": episode["anchorGate"], "anchorGateTerminal": deepcopy(episode["anchorGateTerminal"]),
                        "memberInitialParent": deepcopy(member["initialParent"]), "memberParent": deepcopy(parent),
                        "anchorParent": deepcopy(anchor), "offset": offset, "certified": False, "targetAchieved": False})
            transitions.append({"type": "member", "date": day, "index": index, "offset": offset,
                "episodeId": episode["episodeId"], "anchorSymbol": episode["anchorSymbol"], "memberSymbol": member["symbol"],
                "frozenMemberRank": member["frozenMemberRank"], "fromState": previous, "toState": member["state"],
                "reason": reason, "parent": deepcopy(parent), "anchorGate": episode["anchorGate"]})
        return transitions, raw

    def _form(self, row, index, parents):
        day, anchor = row["date"], row["symbol"]
        prior = self._locks.get(anchor)
        identity = {"date": day, "index": index, "anchorSymbol": anchor, "sourceParentRank": row["rank"],
                    "peerFormationDate": row["formationDate"], "groupSha256": row["groupSha256"]}
        if prior is not None and index <= prior["lockThroughIndex"]:
            return {**identity, "kind": "formationSuppressedByLock", "episodeId": None,
                "lockedByEpisodeId": prior["episodeId"], "lockThroughIndex": prior["lockThroughIndex"]}
        group = row["group"]
        episode = {"episodeId": day+":"+anchor, "formationDate": day, "formationIndex": index,
            "formationParentRank": row["rank"], "anchorSymbol": anchor, "peerFormationDate": row["formationDate"],
            "groupSha256": row["groupSha256"], "formationKind": "activated" if group["kind"] == "formed" else "unevaluableFormation",
            "sourceGroupKind": group["kind"], "sourceGroupReason": group.get("reason"),
            "lockThroughIndex": index+OBSERVATION_SESSIONS, "anchorGate": "alive", "anchorGateTerminal": None,
            "anchorInitialParent": deepcopy(parents[anchor]), "members": []}
        for rank, symbol in enumerate(group["members"], 1):
            parent = parents[symbol]
            state = ("formationUnevaluable" if group["kind"] != "formed" else "observing" if parent["eligibleParentQ"] == "false"
                     else "notInitiallyWeak" if parent["eligibleParentQ"] == "true" else "initialMemberUnknown")
            episode["members"].append({"symbol": symbol, "frozenMemberRank": rank, "initialParent": deepcopy(parent),
                "state": state, "terminalDate": day if state != "observing" else None,
                "terminalIndex": index if state != "observing" else None,
                "terminalReason": state if state != "observing" else None, "terminalParent": deepcopy(parent) if state != "observing" else None})
        self._episodes.append(episode)
        self._locks[anchor] = episode
        return {**identity, "kind": episode["formationKind"], "episodeId": episode["episodeId"],
                "lockThroughIndex": episode["lockThroughIndex"], "episode": deepcopy(episode)}

    def step(self, day, index, parent_by_symbol, source_formations):
        if (self._finished or type(index) is not int or index != self._index+1 or not 0 <= index < len(self._calendar)
            or _day(day) != self._calendar[index]):
            raise ValueError("Consume each target-calendar session exactly once in order")
        if not isinstance(parent_by_symbol, dict) or set(parent_by_symbol) != self._symbol_set:
            raise ValueError("Full fixed-symbol parent population required")
        for symbol in self._symbols:
            _validate_parent(parent_by_symbol[symbol], symbol, day)
        # Validate every source before advancing any state, including locked opportunities.
        self._validate_formations(day, index, source_formations, parent_by_symbol)
        transitions, raw, formations = [], [], []
        for episode in self._episodes:
            daily, confirmed = self._advance(episode, day, index, parent_by_symbol)
            transitions.extend(daily); raw.extend(confirmed)
        for row in source_formations:
            formations.append(self._form(row, index, parent_by_symbol))
        ordered, selection = _select(day, raw)
        self._index = index
        counts = {"activatedEpisodes": len(self._episodes),
            "lockedAnchors": sum(index <= e["lockThroughIndex"] for e in self._locks.values()),
            "anchorGateCounts": dict(Counter(e["anchorGate"] for e in self._episodes)),
            "memberStateCounts": dict(Counter(m["state"] for e in self._episodes for m in e["members"]))}
        return deepcopy({"version": VERSION, "date": day, "index": index, "formations": formations,
            "transitions": transitions, "rawConfirmations": ordered, "selection": selection, "signals": selection["signals"],
            "episodeCounts": counts, "certified": False, "targetAchieved": False})

    def finish(self, cutoff):
        if self._finished or self._index < 0 or _day(cutoff) != self._calendar[self._index]:
            raise ValueError("Finish exactly once at the last consumed session")
        transitions = []
        for episode in self._episodes:
            offset = self._index-episode["formationIndex"]
            for member in episode["members"]:
                if member["state"] == "observing":
                    if offset >= OBSERVATION_SESSIONS:
                        raise ValueError("Unexpired member outside its fixed clock")
                    member.update(state="censoredAtStudyCutoff", terminalDate=cutoff, terminalIndex=self._index,
                                  terminalReason="studyEndedBeforeFifthObservation", terminalParent=None)
                    transitions.append({"type": "member", "date": cutoff, "index": self._index, "offset": offset,
                        "episodeId": episode["episodeId"], "anchorSymbol": episode["anchorSymbol"], "memberSymbol": member["symbol"],
                        "frozenMemberRank": member["frozenMemberRank"], "fromState": "observing", "toState": "censoredAtStudyCutoff",
                        "reason": "studyEndedBeforeFifthObservation", "parent": None, "anchorGate": episode["anchorGate"]})
        self._finished = True
        return deepcopy({"version": VERSION, "cutoff": cutoff, "lastIndex": self._index,
            "transitions": transitions, "episodes": self._episodes, "certified": False, "targetAchieved": False})
