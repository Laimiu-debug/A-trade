"""Causal, shared daily relay and trend observations from local stock bars.

These are close-price proxies, never an exchange limit-up list or fund flows.
The same accumulator feeds the market overview and the analysis ladder.
"""
from __future__ import annotations

from statistics import mean, median


def rounded(value):
    return round(value, 6) if value is not None else None


def limit_flags(previous, row, board, name):
    """Require the nominal cent-rounded limit price, not merely a large rise.

    Historical ST/listing status, adjusted reference prices and rule changes
    are not available. A price outside the nominal daily band is unobservable
    for promotion/break calculations rather than evidence of a broken board.
    """
    if previous is None or row is None or previous[2] <= 0 or row[2] <= 0:
        return False, False, False
    day, st = row[0], "ST" in name.upper()
    if board == "bse":
        percent = 30
    elif board == "star" or board == "chinext" and day >= "2020-08-24":
        percent = 20
    else:
        percent = 5 if st and (board != "main" or day < "2026-07-06") else 10
    cents = int(round(previous[1] * 100))
    upper = (cents * (100 + percent) + 50) // 100 / 100
    lower = (cents * (100 - percent) + 50) // 100 / 100
    # Half a tick absorbs floating arithmetic but cannot include an adjacent tick.
    epsilon = .004999
    observable = lower - epsilon <= row[1] <= upper + epsilon
    return abs(row[1] - upper) <= epsilon, abs(row[1] - lower) <= epsilon, observable


def promotion_bucket():
    return {"eligible": 0, "observed": 0, "promoted": 0, "broken": 0, "missing": 0, "rate": None}


class Leadership:
    def __init__(self, dates):
        self.dates = dates
        self.rows = [{"maxHeight": 0, "multiBoardCount": 0, "limitUpCount": 0, "observedCount": 0,
                      "promotion": {key: promotion_bucket() for key in ("firstToSecond", "secondToThird", "high", "all")},
                      "premium": {"eligible": 0, "observed": 0, "missing": 0, "mean": None, "median": None},
                      "brokenCount": 0, "leaderBreakCount": 0, "leaders": [],
                      "trend": {"eligible": 0, "count": 0, "aboveMA20Ratio": None, "aboveMA60Ratio": None,
                                "amountShare": None, "persistentPositiveCount": 0},
                      "highElasticity": {"count": 0, "amountShare": None},
                      "risk": {"limitDownCount": 0, "declineRatio": None},
                      "_premium": [], "_above20": 0, "_above60": 0, "_amount": 0,
                      "_trendAmount": 0, "_elasticAmount": 0, "_changes": 0, "_declines": 0}
                     for _ in dates]
        self.current, self.previous = [], []

    def add(self, security, aligned, board):
        if security["kind"] != "stock":
            return
        base = {key: security[key] for key in ("symbol", "code", "name", "market", "kind")}
        base["board"] = board
        height, start, start_close, complete = 0, None, None, False
        valid_run, positive_run, sum20, sum60 = 0, 0, 0., 0.
        previous_height = 0
        for i, row in enumerate(aligned):
            stat = self.rows[i]
            previous = aligned[i - 1] if i else None
            up, down, observable = limit_flags(previous, row, board, security["name"])
            before = height
            # Before resetting current state, follow the yesterday cohort, including
            # missing/zero-volume bars. Missing observations never enter the rate.
            if before:
                stat["premium"]["eligible"] += 1
                if row is not None and row[2] > 0 and previous is not None and previous[2] > 0:
                    stat["premium"]["observed"] += 1
                    stat["_premium"].append((row[1] / previous[1] - 1) * 100)
                else:
                    stat["premium"]["missing"] += 1
                keys = ["firstToSecond" if before == 1 else "secondToThird" if before == 2 else "high"]
                if before >= 2:
                    keys.append("all")
                for key in keys:
                    bucket = stat["promotion"][key]
                    bucket["eligible"] += 1
                    bucket["observed"] += int(observable)
                    bucket["missing"] += int(not observable)
                    bucket["promoted"] += int(observable and up)
                    bucket["broken"] += int(observable and not up)
                stat["brokenCount"] += int(before >= 2 and observable and not up)
            if row is None or row[2] <= 0:
                height, start, start_close, complete = 0, None, None, False
                valid_run, positive_run, sum20, sum60 = 0, 0, 0., 0.
            else:
                stat["observedCount"] += 1
                if previous is not None and previous[2] > 0:
                    stat["_changes"] += 1
                    stat["_declines"] += int(row[1] < previous[1])
                stat["risk"]["limitDownCount"] += int(down)
                if up:
                    if before == 0:
                        start, start_close = self.dates[i], previous[1]
                        prior = aligned[i - 2] if i > 1 else None
                        # Exact starting date cannot be proved at the window edge,
                        # after a missing day, or after a prior unobservable band.
                        prior_up, _, prior_observable = limit_flags(prior, previous, board, security["name"])
                        complete = prior_observable and not prior_up
                    height = before + 1
                    stat["limitUpCount"] += 1
                    stat["multiBoardCount"] += int(height >= 2)
                    stat["maxHeight"] = max(stat["maxHeight"], height)
                    if height >= 2:
                        leader = {**base, "height": height, "amount": row[3]}
                        if not stat["leaders"] or height > stat["leaders"][0]["height"]:
                            stat["leaders"] = [leader]
                        elif height == stat["leaders"][0]["height"]:
                            stat["leaders"].append(leader)
                else:
                    height, start, start_close, complete = 0, None, None, False
                valid_run += 1
                sum20 += row[1]
                sum60 += row[1]
                if valid_run > 20:
                    sum20 -= aligned[i - 20][1]
                if valid_run > 60:
                    sum60 -= aligned[i - 60][1]
                ret20 = (row[1] / aligned[i - 20][1] - 1) * 100 if valid_run >= 21 else None
                positive_run = positive_run + 1 if ret20 is not None and ret20 > 0 else 0
                if valid_run >= 60:
                    trend = stat["trend"]
                    trend["eligible"] += 1
                    above20, above60 = row[1] > sum20 / 20 + 1e-8, row[1] > sum60 / 60 + 1e-8
                    persistent = positive_run >= 5
                    strong = above20 and above60 and persistent
                    trend["count"] += int(strong)
                    trend["persistentPositiveCount"] += int(persistent)
                    stat["_above20"] += int(above20)
                    stat["_above60"] += int(above60)
                    stat["_amount"] += row[3]
                    stat["_trendAmount"] += row[3] if strong else 0
                    if (row[1] / aligned[i - 10][1] - 1) * 100 > 30:
                        stat["highElasticity"]["count"] += 1
                        stat["_elasticAmount"] += row[3]
            if i == len(aligned) - 2:
                previous_height = height
            if i == len(aligned) - 1:
                if up:
                    self.current.append({**base, "boardHeight": height, "previousHeight": before,
                                         "boardStartDate": start, "boardRunReturn": rounded((row[1] / start_close - 1) * 100) if start_close else None,
                                         "sequenceComplete": complete, "status": "promoted" if before else "new"})
                if previous_height >= 2:
                    self.previous.append({**base, "previousHeight": previous_height,
                                          "currentHeight": height if observable else None,
                                          "status": "promoted" if observable and up else "broken" if observable else "missing",
                                          "date": self.dates[i], "previousDate": self.dates[i - 1],
                                          "close": row[1] if row else None, "amount": row[3] if row else None,
                                          "changePercent": rounded((row[1] / previous[1] - 1) * 100) if row and row[2] > 0 and previous else None,
                                          "missingReason": None if observable else "日线缺失" if row is None else "当日无成交" if row[2] <= 0 else "涨跌幅规则待核验"})

    def finish(self):
        events, timeline = [], []
        for i, (date, stat) in enumerate(zip(self.dates, self.rows)):
            for bucket in stat["promotion"].values():
                bucket["rate"] = rounded(bucket["promoted"] / bucket["observed"]) if bucket["observed"] else None
            values = stat.pop("_premium")
            stat["premium"].update({"mean": rounded(mean(values)) if values else None, "median": rounded(median(values)) if values else None})
            trend = stat["trend"]
            trend["aboveMA20Ratio"] = rounded(stat["_above20"] / trend["eligible"]) if trend["eligible"] else None
            trend["aboveMA60Ratio"] = rounded(stat["_above60"] / trend["eligible"]) if trend["eligible"] else None
            trend["amountShare"] = rounded(stat["_trendAmount"] / stat["_amount"]) if stat["_amount"] else None
            stat["highElasticity"]["amountShare"] = rounded(stat["_elasticAmount"] / stat["_amount"]) if stat["_amount"] else None
            stat["risk"]["declineRatio"] = rounded(stat["_declines"] / stat["_changes"]) if stat["_changes"] else None
            stat["leaders"].sort(key=lambda item: (-item["amount"], item["symbol"]))
            # Higher-board cohort uses yesterday >=3; if its maximum was two,
            # two-board names form yesterday's high-board cohort instead.
            previous = self.rows[i - 1] if i else None
            if previous:
                cohort = stat["promotion"]["high"] if previous["maxHeight"] >= 3 else stat["promotion"]["secondToThird"]
                stat["highBoardPromotion"] = dict(cohort)
                stat["leaderBreakCount"] = cohort["broken"]
            else:
                stat["highBoardPromotion"] = promotion_bucket()
            metrics = {key: value for key, value in stat.items() if not key.startswith("_")}
            event = None
            if previous and {item["symbol"] for item in previous["leaders"]} != {item["symbol"] for item in stat["leaders"]}:
                event = {"date": date, "previousDate": self.dates[i - 1],
                         "previousLeaders": previous["leaders"], "leaders": stat["leaders"],
                         "previousMaxHeight": previous["maxHeight"], "maxHeight": stat["maxHeight"],
                         "reason": "height_reset" if stat["maxHeight"] < previous["maxHeight"] else "leader_appeared" if not previous["leaders"] else "leader_change",
                         "incomplete": bool(stat["highBoardPromotion"]["missing"]),
                         "coverageNote": "昨日高标存在未观测样本，名单变化不能视为确定切换" if stat["highBoardPromotion"]["missing"] else None}
                events.append(event)
            timeline.append({"date": date, **{key: metrics[key] for key in ("maxHeight", "limitUpCount", "multiBoardCount", "observedCount")},
                             "metrics": metrics, "rotationEvent": event})
        yesterday_max = self.rows[-2]["maxHeight"] if len(self.rows) > 1 else 0
        yesterday = [item for item in self.previous if item["previousHeight"] >= (3 if yesterday_max >= 3 else 2)]
        yesterday.sort(key=lambda item: (-item["previousHeight"], -(item["amount"] or 0), item["symbol"]))
        return {"timeline": timeline, "current": self.current, "yesterdayLeaders": yesterday,
                "previousCandidates": self.previous, "rotationEvents": events}
