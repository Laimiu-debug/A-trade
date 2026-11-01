"""Independent direct-window composition audit; production builder not imported."""
from bisect import bisect_right
import math
import numpy as np

POLICIES = ("completeSystem", "withoutMarketAdmission", "withoutSectorAdmission", "withoutCatchupEntry", "sectorLiquidRepresentative")


def ranks(values):
    values = np.asarray(values, float)
    answer = np.full(len(values), np.nan)
    mask = np.isfinite(values)
    known = values[mask]
    if len(known):
        answer[mask] = ((known[:, None] > known[None, :]).sum(axis=1) + .5 * (known[:, None] == known[None, :]).sum(axis=1)) / len(known)
    return answer


def num(value):
    return float(value) if np.isfinite(value) else None


def same(a, b, label="root"):
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            raise ValueError(label + " keys differ")
        for key in a:
            same(a[key], b[key], label + "." + str(key))
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            raise ValueError(label + " length differs")
        for i, (x, y) in enumerate(zip(a, b)):
            same(x, y, label + ":" + str(i))
    elif type(a) in (int, float) and type(b) in (int, float):
        if not math.isclose(a, b, rel_tol=1e-11, abs_tol=1e-12):
            raise ValueError(f"{label} numeric differs:{a}/{b}")
    elif a != b:
        raise ValueError(f"{label} differs:{a}/{b}")


class DirectWindowReference:
    def __init__(self, matrix, joins, indices):
        self.m, self.joins, self.indices = matrix, sorted(joins, key=lambda r: r["availableFrom"] or "9999"), indices
        self.calendar, self.symbols = matrix["calendar"].tolist(), matrix["symbols"].tolist()
        self.availability = [j["availableFrom"] or "9999" for j in self.joins]
        self.cache, self.industry_cache, self.macro_cache = {}, {}, {}

    def stock(self, i):
        if i in self.cache:
            return self.cache[i]
        m, n = self.m, len(self.symbols)
        valid = m["priceValid"]
        def average(field, length, prior=0, price=True):
            end = i + 1 - prior
            if end < length:
                return np.full(n, np.nan)
            raw = m[field][:, end-length:end]
            okay = np.isfinite(raw).all(axis=1)
            if price:
                okay &= valid[:, end-length:end].all(axis=1)
            if field == "amount":
                okay &= (raw > 0).all(axis=1)
            return np.where(okay, raw.sum(axis=1) / length, np.nan)
        def change(length):
            if i < length:
                return np.full(n, np.nan)
            okay = valid[:, i-length:i+1].all(axis=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                return np.where(okay, m["close"][:, i] / m["close"][:, i-length] - 1, np.nan)
        def extreme(field, length, maximum):
            if i < length:
                return np.full(n, np.nan)
            raw = m[field][:, i-length:i]
            okay = valid[:, i-length:i].all(axis=1) & np.isfinite(raw).all(axis=1)
            value = raw.max(axis=1) if maximum else raw.min(axis=1)
            return np.where(okay, value, np.nan)
        ntr = np.full(n, np.nan)
        if i >= 20:
            previous = m["close"][:, i-20:i]
            high, low = m["high"][:, i-19:i+1], m["low"][:, i-19:i+1]
            with np.errstate(divide="ignore", invalid="ignore"):
                terms = np.maximum.reduce([high-low, abs(high-previous), abs(low-previous)]) / previous
            okay = valid[:, i-20:i+1].all(axis=1) & np.isfinite(terms).all(axis=1)
            ntr = np.where(okay, terms.sum(axis=1) / 20, np.nan)
        value = {"close": np.where(valid[:, i], m["close"][:, i], np.nan), "ma20": average("close", 20), "ma60": average("close", 60),
            "r20": change(20), "r60": change(60), "a5": average("amount", 5), "priorA20": average("amount", 20, 1), "ntr": ntr,
            "amount": np.where(valid[:, i] & np.isfinite(m["amount"][:, i]) & (m["amount"][:, i] > 0), m["amount"][:, i], np.nan),
            "high5": extreme("high", 5, True), "high20": extreme("high", 20, True), "low10": extreme("low", 10, False), "low20": extreme("low", 20, False)}
        self.cache[i] = value
        return value

    def index(self, symbol, key, i, length=1, prior=0):
        raw = np.asarray(self.indices[symbol][key], float)
        end = i + 1 - prior
        part = raw[max(0, end-length):end]
        return float(part.sum() / length) if len(part) == length and np.isfinite(part).all() and (part > 0).all() else np.nan

    def benchmark_return(self, i, width):
        if i < width or not np.isfinite(self.index("sh000300", "close", i, width + 1)):
            return np.nan
        return self.indices["sh000300"]["close"][i] / self.indices["sh000300"]["close"][i-width] - 1

    def turnover(self, i):
        if i < 20:
            return np.nan, np.nan
        values = np.asarray(self.indices["sh000001"]["amount"]) + np.asarray(self.indices["sz399106"]["amount"])
        first = np.asarray(self.indices["sh000001"]["amount"])[i-20:i+1]
        second = np.asarray(self.indices["sz399106"]["amount"])[i-20:i+1]
        if not (np.isfinite(first).all() and np.isfinite(second).all() and (first > 0).all() and (second > 0).all()):
            return float(values[i]) if np.isfinite(values[i]) else np.nan, np.nan
        return float(values[i]), float(values[i-4:i+1].sum()/5 / (values[i-20:i].sum()/20))

    def macro(self, i):
        if i in self.macro_cache:
            return self.macro_cache[i]
        f = self.stock(i)
        observed = np.isfinite(f["ma60"])
        count = int(observed.sum())
        breadth = float((f["close"][observed] > f["ma60"][observed]).mean()) if count else None
        total, ratio = self.turnover(i)
        liquidity = None if not (np.isfinite(total) and np.isfinite(ratio)) else bool(total >= 1e12 and ratio >= .8)
        triplets = [[self.index(s, "close", i, k) for k in (1, 20, 60)] for s in ("sh000001", "sz399106")]
        known = np.isfinite(triplets).all()
        trend = None if not known else all(c > b and a > b for c, a, b in triplets)
        gates = liquidity, trend, None if count / len(self.symbols) < .95 else breadth > .5
        admission = None if any(v is None for v in gates) else all(gates)
        crisis = 0
        for j in range(i, -1, -1):
            pairs = [[self.index(s, "close", j), self.index(s, "close", j, 60)] for s in ("sh000001", "sz399106")]
            if not np.isfinite(pairs).all() or not all(c < ma for c, ma in pairs):
                break
            crisis += 1
        value = {"admission": admission, "liquidity": liquidity, "trend": trend, "breadth60": breadth,
            "breadthObserved": count, "breadthPopulation": len(self.symbols), "turnover": num(total), "turnover5ToPrior20": num(ratio), "crisisCount": crisis, "riskExit": crisis >= 3}
        self.macro_cache[i] = value
        return value

    def sectors(self, i):
        if i in self.industry_cache:
            return self.industry_cache[i]
        k = bisect_right(self.availability, self.calendar[i]) - 1
        if k < 0:
            result = None, [], {}
            self.industry_cache[i] = result
            return result
        join, f = self.joins[k], self.stock(i)
        _, market_ratio = self.turnover(i)
        values, peers = [], {}
        for group in join["sectors"]:
            rows = np.asarray(group["cohortRowIndices"], dtype=int)
            if not len(rows):
                continue
            masks = [np.isfinite(f[key][rows]) for key in ("r20", "r60", "ma60")]
            masks.append(np.isfinite(f["a5"][rows]) & np.isfinite(f["priorA20"][rows]))
            counts = [int(mask.sum()) for mask in masks]
            ret20 = float(f["r20"][rows[masks[0]]].mean()) if counts[0] else np.nan
            ret60 = float(f["r60"][rows[masks[1]]].mean()) if counts[1] else np.nan
            breadth = float((f["close"][rows[masks[2]]] > f["ma60"][rows[masks[2]]]).mean()) if counts[2] else np.nan
            activity = float(f["a5"][rows[masks[3]]].sum()/f["priorA20"][rows[masks[3]]].sum()/market_ratio) if counts[3] and np.isfinite(market_ratio) else np.nan
            relative20, relative60 = ret20-self.benchmark_return(i, 20), ret60-self.benchmark_return(i, 60)
            good = len(rows) >= 5 and min(counts) / len(rows) >= .95 and np.isfinite([relative20, relative60, breadth, activity]).all()
            raw = bool(relative20 > 0 and relative60 > 0 and breadth > .5 and activity >= 1) if good else None
            rank20, rank60 = ranks(f["r20"][rows]), ranks(f["r60"][rows])
            leaders = [int(row) for j, row in enumerate(rows) if rank60[j] >= .8 and f["r60"][row] > max(0., ret60) and f["close"][row] > f["ma60"][row]]
            value = {"sectorCode": group["sectorCode"], "sectorName": group["sectorName"], "cohortMembers": len(rows),
                "observedCounts20_60_breadth_amount": counts, "return20": num(ret20), "return60": num(ret60),
                "relative20": num(relative20), "relative60": num(relative60), "breadth60": num(breadth), "participation": num(activity),
                "featureValid": bool(good), "rawStrong": raw, "leaders": leaders, "score": None}
            values.append(value)
            peers[group["sectorCode"]] = rows, rank20, rank60
        valid = [s for s in values if s["featureValid"]]
        for sector in valid:
            numerator = 0
            for key in ("relative20", "relative60", "participation"):
                rank_values = [s[key] for s in valid]
                numerator += 2 * sum(v < sector[key] for v in rank_values) + sum(v == sector[key] for v in rank_values)
            sector["score"] = numerator / (6 * len(valid))
        self.industry_cache[i] = join["snapshotId"], values, peers
        return self.industry_cache[i]

    def sector_count(self, i, code, desired):
        identity = self.sectors(i)[0]
        count = 0
        for j in range(i, -1, -1):
            snapshot, sectors, _ = self.sectors(j)
            item = next((s for s in sectors if s["sectorCode"] == code), None)
            if snapshot != identity or item is None or item["rawStrong"] is not desired:
                break
            count += 1
        return count

    def audit(self, production):
        checked, signal_lists, metadata = 0, {p: [] for p in POLICIES}, {}
        for output in production["daily"]:
            i, day = output["index"], output["date"]
            if self.calendar[i] != day:
                raise ValueError("Decision date/index mismatch")
            same(output["market"], self.macro(i), day + ":market")
            identity, raw_sectors, peer_map = self.sectors(i)
            sectors = []
            for raw in raw_sectors:
                yes, no = self.sector_count(i, raw["sectorCode"], True), self.sector_count(i, raw["sectorCode"], False)
                sectors.append({**raw, "confirmCount": yes, "failCount": no, "confirmed": yes >= 3, "riskExit": no >= 3})
            same(output["sectors"], sectors, day + ":industry")
            ordered = sorted([s for s in sectors if s["featureValid"]], key=lambda s: (-s["score"], s["sectorCode"]))
            selected = [s["sectorCode"] for s in ordered if s["confirmed"]][:3]
            same(output["selectedSectors"], selected, day + ":selected")
            if output["snapshotId"] != identity:
                raise ValueError("Publication-time snapshot differs")
            now = self.stock(i)
            previous = self.stock(i-1) if i else None
            for policy in POLICIES:
                if policy != "withoutMarketAdmission" and self.macro(i)["admission"] is not True:
                    continue
                chosen = []
                for sector in ordered:
                    code = sector["sectorCode"]
                    if policy != "withoutSectorAdmission" and code not in selected:
                        continue
                    rows, r20, r60 = peer_map[code]
                    old_identity, old_sectors, _ = self.sectors(i-5) if i >= 5 else (None, [], {})
                    old = next((s["leaders"] for s in old_sectors if s["sectorCode"] == code), []) if old_identity == identity else []
                    healthy = [r for r in old if np.isfinite(now["ma20"][r]) and now["close"][r] > now["ma20"][r]]
                    candidates = []
                    for j, row in enumerate(rows):
                        row = int(row)
                        close, ntr, amount, low10 = (now[key][row] for key in ("close", "ntr", "priorA20", "low10"))
                        if not np.isfinite([close, ntr, amount, low10, now["amount"][row]]).all() or amount <= 0:
                            continue
                        floor = max(low10, close*.95)
                        if not 0 < floor < close:
                            continue
                        pattern = None
                        if policy == "sectorLiquidRepresentative":
                            pattern = policy
                        elif now["r60"][row] > 0 and close > now["ma60"][row] and close <= now["ma20"][row] + 2*ntr*close:
                            leader = row in sector["leaders"]
                            if leader and previous is not None and close > now["high20"][row] and previous["close"][row] <= previous["high20"][row]:
                                pattern = "leaderBreakout"
                            elif leader and self.m["low"][row, i] < now["low20"][row] < close:
                                pattern = "leaderReclaim"
                            elif policy != "withoutCatchupEntry" and now["r20"][row] <= sector["return20"] and .2 <= r20[j] < .8 and previous is not None and close > now["high5"][row] and previous["close"][row] <= previous["high5"][row] and now["amount"][row] > amount and any(r != row for r in healthy):
                                pattern = "catchup"
                        if pattern is None:
                            continue
                        key = (-amount, self.symbols[row]) if policy == "sectorLiquidRepresentative" else ({"leaderBreakout": 0, "leaderReclaim": 1, "catchup": 2}[pattern], -r60[j], -amount, self.symbols[row])
                        candidates.append((key, row, {"pattern": pattern, "signalClose": float(close), "failurePrice": float(floor), "meanNtr20": float(ntr),
                            "sectorCode": code, "snapshotId": identity, "sectorScore": sector["score"], "stockReturn60Midrank": num(r60[j]), "priorAmount20": float(amount), "priorLeaderRows": healthy}))
                    if candidates:
                        chosen.append(min(candidates, key=lambda c: c[0]))
                for rank, (_, row, info) in enumerate(chosen[:5], 1):
                    identifier = f"system-v1:{policy}:{day}:{self.symbols[row]}"
                    record = {"signalId": identifier, "strategyId": "marketSectorStockV1", "symbol": self.symbols[row], "signalDate": day, "originalRank": rank}
                    signal_lists[policy].append(record)
                    metadata[identifier] = {**info, **{k: v for k, v in record.items() if k != "strategyId"}}
            checked += 1
        same(production["schedules"], signal_lists, "allPolicies:schedules")
        same(production["metadata"], metadata, "allPolicies:metadata")
        return {"passed": True, "independentDirectWindowDays": checked, "allMarketIndustryRoleAndScheduleChecks": True,
            "stockDayFeatureWindowsCached": len(self.cache), "newMarketAccounts": 0, "secondExecutionEngine": False}
