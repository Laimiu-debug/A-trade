"""Covered-sample observations from an already bound, audited feature panel.

No raw prices, labels, trades, eligibility filters, new ranks or market-regime
classification are used. Required evidence takes priority over a false gate:
incomplete evidence is unknown, never an observed failure. The caller binds
the source hashes and supplies every fixed symbol on every target session.
"""
from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
from datetime import date
from functools import lru_cache
import math
from statistics import mean


FEATURE_VERSION = "market-state-observations-v1"
SOURCE_FEATURE_VERSION = "daily-leadership-raw-v1"
Q_DEFINITION = "persistent-price-strength-five-gates-v1"
THRESHOLDS = (
    ("meanStrengthPercentile20", ">=", .90),
    ("minimumStrengthPercentile20", ">=", .80),
    ("relativeReturn60Percentile", ">=", .90),
    ("closePathMaxDrawdown20", "<=", .10),
    ("signedPathEfficiency20", ">=", .35),
)
GROUPS = {"security20": 21, "security60": 61, "benchmark20": 21,
          "benchmark60": 61, "path20": 21, "ma20": 39,
          "amountPrior20": 20, "amountToday": 1}
Q_GROUPS = ("security20", "security60", "benchmark20", "benchmark60", "path20")
HISTORY_FIELDS = ("trailing20DailyStrengthPercentiles", "trailing20StrengthCrossSectionCounts",
                  "strengthRankSessionDates20", "knownStrengthRankSessions20",
                  "availableStrengthRankSessions20", "relativeReturn20Percentile",
                  "relativeReturn20CrossSectionCount", "relativeReturn60CrossSectionCount")


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _bounded(value, low=0, high=1):
    return _finite(value) and low <= value <= high


@lru_cache(maxsize=4096)
def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must be YYYY-MM-DD")
    return value


def _ordered_dates(values, day, max_size=None):
    try:
        return (isinstance(values, list) and (max_size is None or len(values) <= max_size)
                and all(_day(d) <= day for d in values)
                and all(a < b for a, b in zip(values, values[1:])))
    except (ValueError, TypeError):
        return False


def _group_ok(group, required, day):
    return (isinstance(group, dict) and group.get("complete") is True
            and all(type(group.get(k)) is int and group[k] == required for k in
                    ("requiredSessions", "availableCalendarSessions", "observedSessions", "validSessions"))
            and all(group.get(k) == [] for k in ("missingDates", "invalidDates", "haltDates", "reasons"))
            and _ordered_dates(group.get("unknownTradeStatusDates"), day, required))


def _feature_reasons(row, field):
    reasons = row.get("featureReasons")
    if not isinstance(reasons, dict):
        return ["featureReasonsUnavailable"]
    return ["featureReasons:" + field] if field in reasons and reasons[field] != [] else []


def _common_reasons(row):
    result = []
    if row.get("featureVersion") != SOURCE_FEATURE_VERSION:
        result.append("sourceFeatureVersionMismatch")
    if row.get("rawPriceBasis") != "raw-unadjusted":
        result.append("rawPriceBasisUnknown")
    return result


def _group_reasons(row, names):
    groups = row.get("completeness")
    groups = groups if isinstance(groups, dict) else {}
    return ["requiredWindowUnavailable:" + k for k in names
            if not _group_ok(groups.get(k), GROUPS[k], row["asOfDate"])]


def _archive_reasons(row, target_dates, day_index, population_size):
    ranks = row.get("trailing20DailyStrengthPercentiles")
    counts = row.get("trailing20StrengthCrossSectionCounts")
    days = row.get("strengthRankSessionDates20")
    reasons = []
    if not isinstance(ranks, list) or len(ranks) != 20 or not all(_bounded(v) for v in ranks):
        reasons.append("archivedStrengthPercentilesUnavailable")
    if (not isinstance(counts, list) or len(counts) != 20
            or not all(type(n) is int and 1 <= n <= population_size for n in counts)):
        reasons.append("archivedStrengthCountsUnavailable")
    if (not _ordered_dates(days, row["asOfDate"]) or len(days) != 20 or days[-1] != row["asOfDate"]
            or [d for d in days if d >= target_dates[0]] != list(target_dates[max(0, day_index-19):day_index+1])):
        reasons.append("archivedStrengthDatesUnavailable")
    for key in ("knownStrengthRankSessions20", "availableStrengthRankSessions20"):
        if type(row.get(key)) is not int or row[key] != 20:
            reasons.append("invalidCount:" + key)
    if (not _bounded(row.get("relativeReturn20Percentile"))
            or type(row.get("relativeReturn20CrossSectionCount")) is not int
            or not 1 <= row["relativeReturn20CrossSectionCount"] <= population_size):
        reasons.append("currentStrengthRankUnavailable")
    elif (isinstance(ranks, list) and len(ranks) == 20 and isinstance(counts, list) and len(counts) == 20
          and (ranks[-1] != row["relativeReturn20Percentile"] or counts[-1] != row["relativeReturn20CrossSectionCount"])):
        reasons.append("currentStrengthRankArchiveMismatch")
    for key in ("return20", "relativeReturn20"):
        if not _finite(row.get(key)):
            reasons.append("nonfiniteFeature:" + key)
        reasons.extend(_feature_reasons(row, key))
    return reasons


def evaluate_q(row, *, target_dates, day_index, population_size):
    """Evaluate five fixed predicates, preserving independently known failures.

    Prior-to-target rank dates come from the caller's audited panel. Within the
    target span their suffix must exactly match the registered exchange dates.
    Unknown historical identity or trade flags do not certify or reject Q.
    """
    if (not isinstance(row, dict) or type(day_index) is not int or not 0 <= day_index < len(target_dates)
            or row.get("asOfDate") != target_dates[day_index] or type(population_size) is not int or population_size < 1):
        raise ValueError("Q requires an aligned row and fixed population")
    common = _common_reasons(row)
    history_reasons = _archive_reasons(row, target_dates, day_index, population_size)
    predicates = {}
    for field, operator, threshold in THRESHOLDS:
        value = row.get(field)
        valid_value = _bounded(value, -1 if field == "signedPathEfficiency20" else 0)
        reasons = common + _feature_reasons(row, field)
        if not valid_value:
            reasons.append("invalidBoundedFeature:" + field)
        if field in ("meanStrengthPercentile20", "minimumStrengthPercentile20"):
            reasons += history_reasons + _group_reasons(row, ("security20", "benchmark20"))
            history = row.get("trailing20DailyStrengthPercentiles")
            if isinstance(history, list) and len(history) == 20 and all(_bounded(v) for v in history):
                expected = mean(history) if field == "meanStrengthPercentile20" else min(history)
                if valid_value and value != expected:
                    reasons.append("archivedStrengthStatisticMismatch:" + field)
        elif field == "relativeReturn60Percentile":
            reasons += _group_reasons(row, ("security60", "benchmark60"))
            n = row.get("relativeReturn60CrossSectionCount")
            if type(n) is not int or not 1 <= n <= population_size:
                reasons.append("currentRelative60RankCountUnavailable")
            for key in ("return60", "relativeReturn60"):
                if not _finite(row.get(key)):
                    reasons.append("nonfiniteFeature:" + key)
                reasons += _feature_reasons(row, key)
        else:
            reasons += _group_reasons(row, ("path20",))
        reasons = sorted(set(reasons))
        predicates[field] = {"value": value if valid_value else None, "operator": operator,
            "threshold": threshold, "passed": None if reasons else
            (value >= threshold if operator == ">=" else value <= threshold), "unknownReasons": reasons}
    unknown = sorted({reason for p in predicates.values() for reason in p["unknownReasons"]})
    failed = [k for k, p in predicates.items() if p["passed"] is False]
    return {"qState": "unknown" if unknown else "false" if failed else "true",
            "criterionValues": {k: p["value"] for k, p in predicates.items()},
            "predicates": predicates, "failedPredicates": failed, "unknownReasons": unknown}


def _price(row):
    reasons = _common_reasons(row) + _group_reasons(row, ("security20",)) + _feature_reasons(row, "return20")
    if not _finite(row.get("return20")):
        reasons.append("nonfiniteFeature:return20")
    return {"evaluable": not reasons, "return20": row.get("return20") if not reasons else None,
            "reasons": sorted(set(reasons))}


def _amount(row):
    fields = ("amountToday", "prior20MeanAmount", "amountRelativeToOwnPrior20")
    reasons = _common_reasons(row) + _group_reasons(row, ("amountToday", "amountPrior20"))
    if type(row.get("knownAmountSessions20")) is not int or row["knownAmountSessions20"] != 20:
        reasons.append("knownAmountSessions20Incomplete")
    for field in fields:
        if not _finite(row.get(field)) or row[field] <= 0:
            reasons.append("nonpositiveOrNonfiniteFeature:" + field)
        reasons += _feature_reasons(row, field)
    if all(_finite(row.get(field)) and row[field] > 0 for field in fields):
        if row["amountRelativeToOwnPrior20"] != row["amountToday"] / row["prior20MeanAmount"]:
            reasons.append("amountRatioMismatch")
    return {"evaluable": not reasons, "ratio": row.get("amountRelativeToOwnPrior20") if not reasons else None,
            "reasons": sorted(set(reasons))}


def _median(values):
    if not values:
        return None
    values = sorted(values)
    middle = len(values) // 2
    # statistics.mean avoids the overflow of (a+b)/2 for two large finite values.
    return values[middle] if len(values) % 2 else mean(values[middle-1:middle+1])


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _reason_counts(records):
    return dict(sorted(Counter(r for record in records for r in set(record["reasons"])).items()))


def _quality(rows, day):
    groups = {}
    for name, required in GROUPS.items():
        item = {"requiredSessions": required, "completeCount": 0, "missingCount": 0, "invalidCount": 0,
                "haltCount": 0, "unknownTradeStatusCount": 0, "unavailableEvidenceCount": 0}
        reasons = Counter()
        for row in rows:
            source = row.get("completeness")
            group = source.get(name) if isinstance(source, dict) else None
            if not isinstance(group, dict):
                item["unavailableEvidenceCount"] += 1
                reasons["groupEvidenceUnavailable"] += 1
                continue
            item["completeCount"] += _group_ok(group, required, day)
            for field, output in (("missingDates", "missingCount"), ("invalidDates", "invalidCount"),
                                  ("haltDates", "haltCount"), ("unknownTradeStatusDates", "unknownTradeStatusCount")):
                if _ordered_dates(group.get(field), day, required):
                    item[output] += bool(group[field])
                else:
                    reasons["invalidEvidence:" + field] += 1
            if isinstance(group.get("reasons"), list) and all(isinstance(r, str) for r in group["reasons"]):
                reasons.update(set(group["reasons"]))
            else:
                reasons["groupReasonsUnavailable"] += 1
        item["reasonCounts"] = dict(sorted(reasons.items()))
        groups[name] = item
    return {"groups": groups, "countsMayOverlap": True,
            "historicalIdentityAndTradeStatusCertified": False}


def _transition(previous, current):
    if current == "true":
        return "continued" if previous == "true" else "knownNewChallenger" if previous == "false" else "priorStateUnknownEntrant"
    if previous == "true":
        return "observedFailure" if current == "false" else "unknown"
    return "initial:" + current if previous is None else previous + "To" + current.capitalize()


class MarketStateTracker:
    """Ordered fixed-sample stream; retains only previous Q and five endpoints.

    Each update is transactional for malformed row/date/population input. Returned
    values and input dictionaries cannot mutate the retained comparison state.
    """
    def __init__(self, symbols, target_dates):
        if (not isinstance(symbols, (list, tuple)) or not symbols
                or any(not isinstance(s, str) or not s for s in symbols)
                or list(symbols) != sorted(set(symbols))):
            raise ValueError("Symbols must be nonempty, unique and sorted")
        if (not isinstance(target_dates, (list, tuple)) or not target_dates
                or any(_day(d) != d for d in target_dates)
                or any(a >= b for a, b in zip(target_dates, target_dates[1:]))):
            raise ValueError("Target dates must be unique and strictly ordered")
        self.symbols, self.target_dates = tuple(symbols), tuple(target_dates)
        self._index = 0
        self._previous = None
        self._prices = deque(maxlen=5)

    def update(self, day, rows):
        if self._index >= len(self.target_dates) or day != self.target_dates[self._index]:
            raise ValueError("Update exactly the next registered target session")
        if (not isinstance(rows, list) or len(rows) != len(self.symbols)
                or any(not isinstance(row, dict) or row.get("symbol") != symbol or row.get("asOfDate") != day
                       for row, symbol in zip(rows, self.symbols))):
            raise ValueError("Every target session requires all symbols in their fixed order")
        size = len(self.symbols)
        current_q = [evaluate_q(row, target_dates=self.target_dates, day_index=self._index, population_size=size) for row in rows]
        prices, amounts = [_price(row) for row in rows], [_amount(row) for row in rows]
        earlier = self._prices[0] if len(self._prices) == 5 else None
        roles = []
        for i, (row, q) in enumerate(zip(rows, current_q)):
            previous = self._previous[i] if self._previous is not None else None
            prior_state = previous["qState"] if previous is not None else None
            groups = row.get("completeness")
            groups = groups if isinstance(groups, dict) else {}
            # Full archived ranks/date arrays remain in the hash-bound input
            # row, addressable by (date,symbol); do not duplicate a large panel.
            history_evidence = {key: deepcopy(row.get(key)) for key in
                ("rawPriceBasis", "knownStrengthRankSessions20", "availableStrengthRankSessions20",
                 "relativeReturn20CrossSectionCount", "relativeReturn60CrossSectionCount", "knownAmountSessions20")}
            history_evidence.update(sourceFeatureVersion=row.get("featureVersion"),
                windowComplete={key: _group_ok(groups.get(key), required, day) for key, required in GROUPS.items()},
                windowValidSessions={key: groups[key].get("validSessions") if isinstance(groups.get(key), dict) else None for key in GROUPS},
                fullEvidenceReference={"date": day, "symbol": row["symbol"], "sampleManifestHash": row.get("sampleManifestHash")})
            pair = None if earlier is None else bool(prices[i]["evaluable"] and earlier["prices"][i]["evaluable"])
            roles.append({"date": day, "symbol": row["symbol"], "featureVersion": FEATURE_VERSION,
                "qDefinition": Q_DEFINITION, **q, "priorQState": prior_state,
                "previousRecordAvailable": previous is not None,
                "priorCriterionValues": deepcopy(previous["criterionValues"]) if previous is not None else None,
                "transition": _transition(prior_state, q["qState"]),
                "price20": deepcopy(prices[i]), "amount": deepcopy(amounts[i]),
                "matched5": {"available": earlier is not None, "earlierDate": earlier["date"] if earlier else None,
                    "paired": pair, "earlier": deepcopy(earlier["prices"][i]) if earlier else None},
                "evidence": history_evidence, "baseEligibility": deepcopy(row.get("baseEligibility")),
                "featureReasons": deepcopy(row.get("featureReasons")),
                "asOfEvidence": deepcopy(row.get("asOfEvidence")), "sourceHashes": deepcopy(row.get("sourceHashes")),
                "sampleManifestHash": row.get("sampleManifestHash"), "calendarHash": row.get("calendarHash"),
                "certified": False, "targetAchieved": False, "actionEvidence": "unverified"})
        price_values = [p["return20"] for p in prices if p["evaluable"]]
        amount_values = [a["ratio"] for a in amounts if a["evaluable"]]
        positive = sum(v > 0 for v in price_values)
        distribution = Counter(str(row["knownAmountSessions20"]) if type(row.get("knownAmountSessions20")) is int
                               and 0 <= row["knownAmountSessions20"] <= 20 else "unknownOrInvalid" for row in rows)
        daily = {"date": day, "featureVersion": FEATURE_VERSION, "fixedPopulation": size, "qDefinition": Q_DEFINITION,
            "qCounts": {state: sum(q["qState"] == state for q in current_q) for state in ("true", "false", "unknown")},
            "confirmedStrongEmpty": not any(q["qState"] == "true" for q in current_q),
            "qUnknownCount": sum(q["qState"] == "unknown" for q in current_q),
            "price20": {"evaluableCount": len(price_values), "coverage": len(price_values)/size,
                "unavailableCount": size-len(price_values), "medianAbsoluteReturn20": _median(price_values),
                "positiveCount": positive, "zeroCount": sum(v == 0 for v in price_values),
                "negativeCount": sum(v < 0 for v in price_values),
                "positiveReturn20Breadth": _ratio(positive, len(price_values)), "reasonCounts": _reason_counts(prices)},
            "amount": {"evaluableCount": len(amount_values), "coverage": len(amount_values)/size,
                "unavailableCount": size-len(amount_values), "medianRelativeToOwnPrior20": _median(amount_values),
                "knownAmountSessions20Distribution": dict(sorted(distribution.items())), "reasonCounts": _reason_counts(amounts)},
            "matched5": self._matched(prices, earlier, size), "yesterdayCohort": self._cohort(roles),
            "quality": _quality(rows, day), "newStrategyTrials": 0, "marketRegime": None,
            "forwardReturnsCalculated": False, "strategyReturnsCalculated": False,
            "backwardPriceReturnsFromPanel": True, "certified": False, "targetAchieved": False}
        # Commit only after the complete output has been built. Keep independent,
        # compact records so caller edits to returned roles cannot rewrite history.
        self._previous = [{"qState": q["qState"], "criterionValues": deepcopy(q["criterionValues"])} for q in current_q]
        self._prices.append({"date": day, "prices": deepcopy(prices)})
        self._index += 1
        return {"daily": daily, "roles": roles}

    def _matched(self, prices, earlier, size):
        keys = ("earlierEvaluableCount", "pairedCount", "pairedCoverageToday", "pairedCoverageEarlier", "pairedCoverageFixed",
                "positiveBreadthToday", "positiveBreadthEarlier", "medianReturnToday", "medianReturnEarlier",
                "matchedBreadthChange5", "matchedMedianReturnChange5", "unpairedTodayCount", "unpairedEarlierCount")
        today_n = sum(p["evaluable"] for p in prices)
        result = {"available": earlier is not None, "earlierDate": earlier["date"] if earlier else None,
                  "todayEvaluableCount": today_n, **dict.fromkeys(keys), "reason": "earlierTargetSessionUnavailable" if earlier is None else None}
        if earlier is None:
            return result
        old = earlier["prices"]
        pairs = [(p["return20"], e["return20"]) for p, e in zip(prices, old) if p["evaluable"] and e["evaluable"]]
        old_n, pair_n = sum(p["evaluable"] for p in old), len(pairs)
        result.update(earlierEvaluableCount=old_n, pairedCount=pair_n,
            pairedCoverageToday=_ratio(pair_n, today_n), pairedCoverageEarlier=_ratio(pair_n, old_n),
            pairedCoverageFixed=pair_n/size, unpairedTodayCount=today_n-pair_n, unpairedEarlierCount=old_n-pair_n)
        if not pairs:
            result["reason"] = "emptyMatchedPopulation"
            return result
        today, prior = [p[0] for p in pairs], [p[1] for p in pairs]
        bt, bp = sum(v > 0 for v in today)/pair_n, sum(v > 0 for v in prior)/pair_n
        mt, mp = _median(today), _median(prior)
        change = mt-mp
        result.update(positiveBreadthToday=bt, positiveBreadthEarlier=bp, medianReturnToday=mt, medianReturnEarlier=mp,
                      matchedBreadthChange5=100*(bt-bp), matchedMedianReturnChange5=change if _finite(change) else None)
        if not _finite(change):
            result["reason"] = "nonfiniteMedianDifference"
        return result

    def _cohort(self, roles):
        keys = ("eligibleCount", "continuedCount", "observedFailureCount", "unknownCount", "observedCount",
                "conditionalContinuationRate", "observedCoverage", "continuationLowerBound", "continuationUpperBound")
        previous = self._previous is not None
        result = {"previousCohortAvailable": previous,
            "previousDate": self.target_dates[self._index-1] if previous else None,
            "members": None, **dict.fromkeys(keys),
            "reason": None if previous else "previousCohortUnavailable",
            "boundsMeaning": "observation-missingness bounds; not confidence intervals or net30 probabilities",
            "currentTrueRoles": {key: sum(r["transition"] == key for r in roles) for key in
                                 ("continued", "knownNewChallenger", "priorStateUnknownEntrant")}}
        if not previous:
            return result
        members = [r for r in roles if r["priorQState"] == "true"]
        count = len(members)
        continued = sum(r["qState"] == "true" for r in members)
        failed = sum(r["qState"] == "false" for r in members)
        unknown = sum(r["qState"] == "unknown" for r in members)
        observed = continued + failed
        assert continued+failed+unknown == count
        result.update(members=[r["symbol"] for r in members], eligibleCount=count,
            continuedCount=continued, observedFailureCount=failed, unknownCount=unknown, observedCount=observed,
            conditionalContinuationRate=_ratio(continued, observed), observedCoverage=_ratio(observed, count),
            continuationLowerBound=_ratio(continued, count), continuationUpperBound=_ratio(continued+unknown, count))
        return result
