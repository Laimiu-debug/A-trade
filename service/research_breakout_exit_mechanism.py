"""Describe saved breakout activation timing; never execute or filter accounts.

The caller reconstructs episodes and complete held-close evidence from the
original fill order and observations. A final sell's signalId is not an entry
join key. The frozen episode arithmetic remains the only PnL decomposition.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date

from .research_exit_mechanism import aggregate as _aggregate, pair_episode


VERSION = "research-breakout-exit-mechanism-v1"
ACTIVATION_CLASSES = (
    "neverActivatedByCutoff", "activatedBeforeControlFinalExit",
    "activatedOnControlFinalExitDay", "activatedAfterControlFinalExit",
    "controlStillOpenAtCutoff", "notComparableEntry", "activationEvidenceUnknown",
)
EVIDENCE_STATUSES = ("complete", "incomplete", "missing", "notEntered")
CANDIDATE_EXIT_GROUPS = (
    "stopLoss", "trailingStop", "breakoutVolatilityTrail", "otherClosedExit",
    "openAtCutoff", "notEntered",
)
BASIS = {
    "timing": "Candidate activation is at held close; control final exit is at open. Same-day activation is later, not advance confirmation.",
    "partialSales": "Before-control-final-exit compares only the final sell, and does not imply absence of earlier partial sales.",
    "completeness": "Complete means full held-close observation coverage and a consistent activation state chain, not that every anchor or volatility window is known.",
    "money": "Saved episode PnL and symmetric components are descriptive, path-dependent attribution, not causal effects or filtered-strategy returns.",
}
_EVIDENCE_FIELDS = {"complete", "activationDate", "heldCloseCount", "activatedCloseCount",
                    "firstHeldCloseDate", "lastHeldCloseDate"}
_EXTRA_FIELDS = ("cutoffDate", "activationEvidence", "activationDate", "activationClass",
                 "evidenceStatus", "candidateEpisodeExitReason", "candidateExitGroup")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _day(value):
    _require(isinstance(value, str), "Date must be canonical YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("Date must be canonical YYYY-MM-DD") from None
    _require(parsed.isoformat() == value, "Date must be canonical YYYY-MM-DD")
    return value


def _evidence_status(candidate, evidence, cutoff):
    if evidence is None:
        return ("missing" if candidate["entered"] else "notEntered"), None
    _require(isinstance(evidence, dict) and _EVIDENCE_FIELDS <= evidence.keys(),
             "Incomplete activation evidence schema")
    _require(type(evidence["complete"]) is bool, "Evidence complete must be bool")
    count, activated = evidence["heldCloseCount"], evidence["activatedCloseCount"]
    _require(type(count) is int and count >= 0, "heldCloseCount must be a nonnegative int")
    _require(type(activated) is int and activated in (0, 1) and activated <= count,
             "activatedCloseCount must be zero or one and no greater than heldCloseCount")
    first, last, activation = (evidence[key] for key in
                               ("firstHeldCloseDate", "lastHeldCloseDate", "activationDate"))
    _require((activated == 0) == (activation is None), "Activation date and activated count disagree")
    if not count:
        _require(first is None and last is None, "Empty held-close evidence cannot have boundary dates")
    else:
        first, last = _day(first), _day(last)
        _require(first <= last <= cutoff, "Held-close evidence is unordered or after cutoff")
        _require((count == 1 and first == last) or (count > 1 and first < last),
                 "Held-close count and boundary dates disagree")
    if not candidate["entered"]:
        _require(count == 0 and activation is None, "Unentered candidate cannot have held-close evidence")
        return "notEntered", None
    if count:
        _require(first >= candidate["entryDate"], "Held-close evidence precedes candidate entry")
        if candidate["positionState"] == "closed":
            _require(last < candidate["exitDate"], "Final opening exit precedes that day's close evidence")
    if evidence["complete"]:
        _require(count > 0 and first == candidate["entryDate"],
                 "Complete entered evidence must begin at entry close and contain observations")
    if activation is not None:
        activation = _day(activation)
        _require(first <= activation <= last, "Activation must be within the candidate's observed held closes")
    return "complete" if evidence["complete"] else "incomplete", activation


def _exit_group(candidate, reason):
    if candidate["positionState"] != "closed":
        _require(reason is None, "Open or unentered candidate cannot have a final-exit reason")
        return candidate["positionState"]
    _require(isinstance(reason, str) and bool(reason) and reason == reason.strip(),
             "Closed candidate requires the actual final sell's nonempty reason")
    return reason if reason in CANDIDATE_EXIT_GROUPS[:3] else "otherClosedExit"


def pair_activation_episode(control, candidate, *, cutoff_date, activation_evidence,
                            candidate_episode_exit_reason):
    """Pair saved episodes and classify activation only for identical entries.

    `activation_evidence=None` means missing, never "not activated". Incomplete
    observations may retain a known activation date but cannot certify a timing
    class. The caller certifies complete coverage and state transitions; this
    pure boundary validates their minimum counts/dates. A closed candidate's
    final sell reason must come from its actual sell-fill episode ownership.
    """
    pair = pair_episode(control, candidate)
    cutoff = _day(cutoff_date)
    for side in (control, candidate):
        if side["entered"]:
            _require(side["entryDate"] <= cutoff, "Episode entry is after cutoff")
            if side["positionState"] == "closed":
                _require(side["exitDate"] <= cutoff, "Episode final exit is after cutoff")
    status, activation = _evidence_status(candidate, activation_evidence, cutoff)
    exit_group = _exit_group(candidate, candidate_episode_exit_reason)
    if pair["group"] != "bothSameEntry":
        classification = "notComparableEntry"
    elif status != "complete":
        classification = "activationEvidenceUnknown"
    elif activation is None:
        classification = "neverActivatedByCutoff"
    elif control["positionState"] == "openAtCutoff":
        classification = "controlStillOpenAtCutoff"
    elif activation < control["exitDate"]:
        classification = "activatedBeforeControlFinalExit"
    elif activation == control["exitDate"]:
        classification = "activatedOnControlFinalExitDay"
    else:
        classification = "activatedAfterControlFinalExit"
    return {**pair, "cutoffDate": cutoff, "activationEvidence": deepcopy(activation_evidence),
            "activationDate": activation, "activationClass": classification, "evidenceStatus": status,
            "candidateEpisodeExitReason": candidate_episode_exit_reason, "candidateExitGroup": exit_group}


def _summarize(rows):
    return {**_aggregate(rows),
            "activationClassCounts": {key: sum(row["activationClass"] == key for row in rows)
                                      for key in ACTIVATION_CLASSES},
            "evidenceStatusCounts": {key: sum(row["evidenceStatus"] == key for row in rows)
                                     for key in EVIDENCE_STATUSES},
            "activationEvidenceUnknownCount": sum(row["evidenceStatus"] in ("missing", "incomplete") for row in rows),
            "candidateExitGroupCounts": {key: sum(row["candidateExitGroup"] == key for row in rows)
                                         for key in CANDIDATE_EXIT_GROUPS}}


def aggregate_activation(rows):
    """Preserve all paired money/maturity and all seven activation cells.

    Non-comparable entries remain in totals, including their evidence-unknown
    counts. No trade or opportunity is removed because activation was unknown.
    Extra caller-owned identity fields are permitted, but derived fields must
    exactly match the pure pairing result.
    """
    selected = []
    for row in rows:
        _require(isinstance(row, dict) and all(key in row for key in
                 ("control", "candidate", *_EXTRA_FIELDS)), "Aggregate needs activation-paired rows")
        checked = pair_activation_episode(row["control"], row["candidate"],
            cutoff_date=row["cutoffDate"], activation_evidence=row["activationEvidence"],
            candidate_episode_exit_reason=row["candidateEpisodeExitReason"])
        _require(all(key in row and row[key] == value for key, value in checked.items()),
                 "Activation-paired row derived fields differ")
        selected.append(checked)
    return {**_summarize(selected), "activationRows": [
        {"activationClass": key, **_summarize([row for row in selected if row["activationClass"] == key])}
        for key in ACTIVATION_CLASSES]}
