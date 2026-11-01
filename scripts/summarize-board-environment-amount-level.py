"""Posthoc amount-level description from three already audited evidence streams.

No raw quotes, accounts, signals, or strategy rules are loaded or changed. The
complete-period basket is a descriptive sensitivity check, not an eligible
universe that could have been known before the observation period.
"""
from argparse import ArgumentParser
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/board-environment-context-v1"
BOARDS = ("main", "chinext", "star")
PERIOD = "2024-2025"
START, END, SPLIT = "2024-01-02", "2025-12-30", "2024-09-24"
EXPECTED_DAYS = 484
AUDIT_SHA = "f862afb27062bfe9ed0c50af09953133f9dc1854ad9d349bb05e2a47e5107188"
VERSION = "board-environment-amount-level-posthoc-v1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ref(path):
    return {"path": Path(path).relative_to(ROOT).as_posix(), "sha256": sha(path)}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iso_day(value):
    require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value,
            "Invalid ISO observation date")
    return value


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def close(left, right, label):
    require(math.isfinite(left) and math.isfinite(right)
            and math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-6), label)


def total(values):
    values = list(values)
    if not values:
        return None
    result = math.fsum(values)
    require(positive(result), "Nonfinite or nonpositive amount total")
    return result


def checked_median(values):
    values = [value for value in values if value is not None]
    if not values:
        return None
    # Independent sorted-middle check against the statistics implementation.
    ordered = sorted(values)
    at = len(ordered) // 2
    expected = ordered[at] if len(ordered) % 2 else mean(ordered[at - 1:at + 1])
    actual = median(values)
    close(actual, expected, "Median disagreement")
    return actual


def distribution(values):
    values = [value for value in values if value is not None]
    return {"knownDays": len(values), "mean": mean(values) if values else None,
            "median": checked_median(values), "min": min(values) if values else None,
            "max": max(values) if values else None}


def ratio(numerator, denominator):
    if numerator is None or denominator is None:
        return None
    require(positive(numerator) and positive(denominator), "Invalid median ratio input")
    result = numerator / denominator
    require(positive(result), "Nonfinite or underflow median ratio")
    close(result * denominator, numerator, "Median ratio reconstruction")
    return result


class HashingReader(io.RawIOBase):
    """Hash compressed input bytes in the same sequential pass as gzip parsing."""
    def __init__(self, stream):
        super().__init__()
        self.stream, self.hasher, self.byte_count = stream, hashlib.sha256(), 0

    def readable(self):
        return True

    def readinto(self, target):
        data = self.stream.read(len(target))
        self.hasher.update(data)
        self.byte_count += len(data)
        target[:len(data)] = data
        return len(data)


def evidence_records(path, expected_sha):
    before = path.stat()
    with path.open("rb") as raw:
        hashed = HashingReader(raw)
        with gzip.GzipFile(fileobj=hashed, mode="rb") as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8") as stream:
                for line in stream:
                    require(bool(line.strip()), "Blank evidence record")
                    yield json.loads(line)
        require(hashed.hasher.hexdigest() == expected_sha, "Evidence file hash mismatch")
        require(hashed.byte_count == before.st_size, "Evidence compressed-byte count mismatch")
    after = path.stat()
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
            "Evidence changed during its single-pass read")


def summarize_board(board, records, *, population, start=START, end=END,
                    split=SPLIT, expected_days=EXPECTED_DAYS):
    """Consume the evidence iterator once, retaining only current valid amounts."""
    require(type(population) is int and population > 0, "Invalid fixed population")
    require(iso_day(start) < iso_day(split) <= iso_day(end), "Invalid observation partition")
    by_symbol, identities, daily, excluded = {}, None, [], []
    previous_a = previous_t = None
    full_count = 0
    for record in records:
        full_count += 1
        a, t = iso_day(record["asOfDate"]), iso_day(record["formationDate"])
        require(a < t and (previous_a is None or previous_a < a)
                and (previous_t is None or previous_t < t), "Duplicate or unordered evidence dates")
        previous_a, previous_t = a, t
        require(type(record["fixedPopulation"]) is int and record["fixedPopulation"] == population,
                "Fixed population count differs from audited source")
        rows = record["rows"]
        require(isinstance(rows, list) and len(rows) == population, "Incomplete daily population")
        require(digest(rows) == record["evidenceDigest"], "Daily evidence digest mismatch")
        symbols = [row["symbol"] for row in rows]
        require(all(isinstance(symbol, str) and symbol for symbol in symbols)
                and len(set(symbols)) == population, "Invalid or duplicate symbol")
        symbols = sorted(symbols)
        if identities is None:
            identities = symbols
            by_symbol = {symbol: {} for symbol in identities}
        require(symbols == identities, "Fixed sample identities changed")
        for row in rows:
            require("amountToday" in row and (row["amountToday"] is None or positive(row["amountToday"])),
                    "amountToday must be null or a finite positive number")
        if not start <= a <= end:
            excluded.append(a)
            continue
        values = []
        for row in rows:
            value = row["amountToday"]
            if value is not None:
                by_symbol[row["symbol"]][a] = value
                values.append(value)
        daily.append({"period": PERIOD, "board": board, "asOfDate": a, "formationDate": t,
            "epoch": "before20240924" if a < split else "from20240924",
            "fixedPopulation": population, "observedCount": len(values),
            "unknownCount": population - len(values), "observedCoverage": len(values) / population,
            "observedTotalCny": total(values)})
    require(len(daily) == expected_days and daily[0]["asOfDate"] == start
            and daily[-1]["asOfDate"] == end, "Observation range or day count mismatch")
    dates = [row["asOfDate"] for row in daily]
    basket = [symbol for symbol in identities if len(by_symbol[symbol]) == expected_days]
    basket_count = len(basket)
    for row in daily:
        balanced = total(by_symbol[symbol][row["asOfDate"]] for symbol in basket)
        share = balanced / row["observedTotalCny"] if balanced is not None else None
        require(share is None or 0 < share <= 1, "Balanced amount exceeds observed amount")
        row.update({"balancedMemberCount": basket_count, "balancedStockShare": basket_count / population,
                    "balancedTotalCny": balanced, "balancedShareOfObservedTotal": share})

    epoch_rows = []
    for epoch in ("before20240924", "from20240924"):
        part = [row for row in daily if row["epoch"] == epoch]
        require(bool(part), "An amount-date epoch is empty")
        observed_sum = total(row["observedTotalCny"] for row in part if row["observedTotalCny"] is not None)
        balanced_sum = total(row["balancedTotalCny"] for row in part if row["balancedTotalCny"] is not None)
        epoch_rows.append({"period": PERIOD, "board": board, "epoch": epoch,
            "asOfDateFrom": part[0]["asOfDate"], "asOfDateThrough": part[-1]["asOfDate"],
            "dayCount": len(part), "fixedPopulation": population,
            "observedKnownDays": sum(row["observedTotalCny"] is not None for row in part),
            "observedMissingDays": sum(row["observedTotalCny"] is None for row in part),
            "observedSymbolDays": sum(row["observedCount"] for row in part),
            "unknownSymbolDays": sum(row["unknownCount"] for row in part),
            "medianObservedTotalCny": checked_median(row["observedTotalCny"] for row in part),
            "observedTotalSumCny": observed_sum,
            "observedCoverage": distribution(row["observedCoverage"] for row in part),
            "balancedMemberCount": basket_count, "balancedStockShare": basket_count / population,
            "balancedKnownDays": len(part) if basket_count else 0,
            "medianBalancedTotalCny": checked_median(row["balancedTotalCny"] for row in part),
            "balancedTotalSumCny": balanced_sum,
            "balancedShareOfObservedTotal": distribution(row["balancedShareOfObservedTotal"] for row in part),
            "balancedShareOfObservedSum": balanced_sum / observed_sum if balanced_sum is not None else None})
    pre, post = epoch_rows
    comparison = {"period": PERIOD, "board": board, "splitAsOfDate": split,
        "beforeDayCount": pre["dayCount"], "afterDayCount": post["dayCount"],
        "beforeMedianObservedTotalCny": pre["medianObservedTotalCny"],
        "afterMedianObservedTotalCny": post["medianObservedTotalCny"],
        "observedMedianPostPreRatio": ratio(post["medianObservedTotalCny"], pre["medianObservedTotalCny"]),
        "beforeMedianBalancedTotalCny": pre["medianBalancedTotalCny"],
        "afterMedianBalancedTotalCny": post["medianBalancedTotalCny"],
        "balancedMedianPostPreRatio": ratio(post["medianBalancedTotalCny"], pre["medianBalancedTotalCny"]),
        "balancedMemberCount": basket_count, "balancedStockShare": basket_count / population,
        "beforeObservedCoverage": pre["observedCoverage"], "afterObservedCoverage": post["observedCoverage"],
        "beforeBalancedShareOfObservedTotal": pre["balancedShareOfObservedTotal"],
        "afterBalancedShareOfObservedTotal": post["balancedShareOfObservedTotal"]}

    # Cross-check the original per-symbol maps, not merely the derived daily table.
    observed_day = math.fsum(row["observedTotalCny"] or 0 for row in daily)
    observed_symbol = math.fsum(math.fsum(values.values()) for values in by_symbol.values())
    balanced_day = math.fsum(row["balancedTotalCny"] or 0 for row in daily)
    balanced_symbol = math.fsum(math.fsum(by_symbol[symbol].values()) for symbol in basket)
    close(observed_day, observed_symbol, "Observed day/symbol sum disagreement")
    close(balanced_day, balanced_symbol, "Balanced day/symbol sum disagreement")
    for key, expected in (("observedTotalSumCny", observed_symbol), ("balancedTotalSumCny", balanced_symbol)):
        close(math.fsum(row[key] or 0 for row in epoch_rows), expected, "Epoch/full money partition")
    observed_pairs = sum(len(values) for values in by_symbol.values())
    require(sum(row["observedCount"] for row in daily) == observed_pairs
            == sum(row["observedSymbolDays"] for row in epoch_rows), "Observed number partition")
    require(sum(row["unknownCount"] for row in daily) == population * expected_days - observed_pairs,
            "Missing number partition")
    require(sum(row["dayCount"] for row in epoch_rows) == expected_days
            and all(set(by_symbol[symbol]) == set(dates) for symbol in basket), "Balanced date completeness")
    verification = {"passed": True, "board": board, "fullEvidenceRows": full_count,
        "includedDays": expected_days, "excludedAsOfDates": excluded,
        "observedSymbolDays": observed_pairs, "balancedSymbolDays": basket_count * expected_days,
        "observedSumByDayCny": observed_day, "observedSumBySymbolCny": observed_symbol,
        "balancedSumByDayCny": balanced_day, "balancedSumBySymbolCny": balanced_symbol,
        "observedResidualCny": observed_day - observed_symbol, "balancedResidualCny": balanced_day - balanced_symbol,
        "aggregationTolerance": {"relative": 1e-12, "absoluteCny": 1e-6},
        "checks": ["ordered unique dates and fixed complete identities", "valid current amount independent of rolling ratio",
            "day/symbol independent aggregation", "epoch/full amount and number partition", "complete basket exact date set",
            "sorted-middle median cross-check", "post/pre median quotient reconstruction"]}
    basket_row = {"period": PERIOD, "board": board, "fixedPopulation": population,
        "fixedPopulationDigest": digest(identities), "asOfDateDigest": digest(dates),
        "balancedMembers": basket, "balancedMemberDigest": digest(basket),
        "balancedMemberCount": basket_count, "balancedStockShare": basket_count / population,
        "balancedShareOfObservedTotal": distribution(row["balancedShareOfObservedTotal"] for row in daily),
        "balancedShareOfObservedSum": balanced_symbol / observed_symbol if basket_count else None,
        "selection": "Posthoc: current amount known on every included observation date; never a causal stock screen."}
    return {"dailyRows": daily, "epochRows": epoch_rows, "comparisonRow": comparison,
            "basket": basket_row, "verification": verification}


def unit_sources():
    definitions = [
        ("scripts/materialize-board-coverage-source.py", [25, 170, 173, 336],
         "TDX <IIIIIfII amount float is preserved; stock metadata declares amountUnit CNY and volumeUnit shares."),
        ("scripts/verify-board-coverage-reclaim.py", [220, 297, 317],
         "Independent source audit decodes and compares the same unchanged amount field."),
        ("service/tdx.py", [17, 94, 107], "Local daily decoder preserves amount; only OHLC is divided by 100."),
        ("service/market_service.py", [284, 300], "Local TDX route identifies amountUnit as yuan (元)."),
        ("service/signal_leadership_features.py", [63, 105],
         "Amount availability requires aligned raw basis, positive volume and explicit positive amount, and no explicit halt; OHLC validity is separate."),
        ("service/research_board_environment.py", [63, 84],
         "amountToday depends only on current amount availability; prior20 or amountRatio unknown does not erase it.")]
    return [{**ref(ROOT / name), "lines": lines, "meaning": meaning} for name, lines, meaning in definitions]


def build():
    destination = OUT / "amount-level-context.json"
    require(not destination.exists(), "Sidecar already exists; exclusive output will not overwrite")
    refs = {name: ref(OUT / name) for name in ("plan.json", "readout.json", "validation.json")}
    require(refs["validation.json"]["sha256"] == AUDIT_SHA, "Unexpected main audit proof")
    plan, report, proof = (read(OUT / name) for name in ("plan.json", "readout.json", "validation.json"))
    require(proof["passed"] is True and proof["planSha256"] == refs["plan.json"]["sha256"]
            and proof["readoutSha256"] == refs["readout.json"]["sha256"]
            and report["plan"] == refs["plan.json"], "Audit/plan/readout binding mismatch")
    require(report["artifacts"] == proof["artifacts"], "Readout/audit artifact references differ")
    require(all(proof["inputPins"].get(name) == value for name, value in plan["sourcePins"].items()),
            "Audit did not bind registered computation code")
    units = unit_sources()
    for item in units:
        registered = plan["sourcePins"].get(item["path"])
        require(registered is None or registered == item["sha256"], "Amount interpretation code changed")
    script_ref = ref(Path(__file__).resolve())
    built = []
    input_refs = []
    common_dates = None
    for board in BOARDS:
        path = OUT / PERIOD / board / "evidence.jsonl.gz"
        item = report["artifacts"][PERIOD][board]["evidence.jsonl.gz"]
        require(item["path"] == path.relative_to(ROOT).as_posix(), "Unexpected evidence input path")
        result = summarize_board(board, evidence_records(path, item["sha256"]),
                                 population=plan["sourceData"][PERIOD][board]["members"])
        require(result["verification"]["fullEvidenceRows"] == 485
                and result["verification"]["excludedAsOfDates"] == ["2023-12-29"], "Unexpected warmup/date exclusion")
        dates = [row["asOfDate"] for row in result["dailyRows"]]
        require(common_dates is None or common_dates == dates, "Board observation calendars differ")
        common_dates = dates
        built.append(result)
        input_refs.append(item)
    # Small controlling files and code must remain unchanged over the extraction.
    for name, item in refs.items():
        require(sha(OUT / name) == item["sha256"], "Controlling artifact changed during extraction")
    require(ref(Path(__file__).resolve()) == script_ref, "Extractor changed during extraction")
    result = {"schema": VERSION, "completedAt": datetime.now(timezone.utc).isoformat(),
        "posthoc": True, "extractor": script_ref, "extractorSha256": script_ref["sha256"],
        "inputs": {"plan": refs["plan.json"], "readout": refs["readout.json"], "audit": refs["validation.json"],
                   "evidence": input_refs},
        "sourceBindings": {board: plan["sourceData"][PERIOD][board] for board in BOARDS},
        "unit": {"amount": "CNY", "scaleApplied": 1, "sourceCodeRefs": units,
                 "meaning": "Frozen covered-stock sample observed traded amount, not all-market turnover or capital flow; benchmark excluded."},
        "method": {"observationDate": "asOfDate A, not formationDate T", "start": START, "end": END,
            "splitAsOfDate": SPLIT, "before": "A < 2024-09-24", "after": "A >= 2024-09-24",
            "expectedDaysPerBoard": EXPECTED_DAYS,
            "observed": "fsum of available positive amountToday; unknown not zero; an entirely unknown day is null.",
            "balanced": "One fixed per-board basket with valid amountToday on all 484 included days; selected posthoc using full-period availability.",
            "comparison": "Median of daily totals after / median of daily totals before; observed and balanced reported together.",
            "coverage": "Fixed-cohort stock coverage and balanced/observed amount shares are disclosed; no minimum basket size filter."},
        "dailyRows": [row for value in built for row in value["dailyRows"]],
        "epochRows": [row for value in built for row in value["epochRows"]],
        "comparisonRows": [value["comparisonRow"] for value in built],
        "balancedBaskets": [value["basket"] for value in built],
        "counts": {"boards": 3, "dailyRows": 1452, "epochRows": 6, "comparisonRows": 3},
        "verification": {"passed": True, "boards": [value["verification"] for value in built],
                         "allThreeObservationCalendarsEqual": True, "compressedInputsHashedDuringSinglePass": True},
        "limitations": ["Both periods are exposed development histories; this sidecar is posthoc descriptive, not a new preregistered test.",
            "A full-period completeness basket uses future availability; it is not a causal selection rule and can be unrepresentative, even with a nonempty basket.",
            "Observed totals can move with data availability; balanced results must be read alongside stock coverage and amount share.",
            "Missing, zero, invalid volume/amount or known halt may all appear as amountToday null under the inherited availability rule; none is imputed as zero here.",
            "Frozen covered samples are not historical whole-market universes; board labels are not pure technology industry classifications.",
            "Raw unadjusted source and historical-state limitations are inherited; no corporate-action repair or individual-stock discontinuity investigation.",
            "The split uses actual amount date A. No 2026 observations or 2025-12-31 amount are added."],
        "newSignals": 0, "newAccounts": 0, "parameterChanges": 0, "newStrategyTrials": 0,
        "holdoutClaim": False, "certified": False, "targetAchieved": False}
    require([len(result[key]) for key in ("dailyRows", "epochRows", "comparisonRows")] == [1452, 6, 3],
            "Final row counts differ")
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"passed": True, "output": ref(destination), "counts": result["counts"], "posthoc": True}


def self_test():
    checks = []
    days = ["2024-09-20", "2024-09-23", "2024-09-24", "2024-09-25"]
    formations = ["2024-09-23", "2024-09-24", "2024-09-25", "2024-09-26"]

    def fixture(amounts):
        result = []
        for index, day in enumerate(days):
            rows = [{"symbol": symbol, "amountToday": values[index], "amountRatio": None,
                     "amountReasons": ["prior20Incomplete"]} for symbol, values in amounts.items()]
            result.append({"formationDate": formations[index], "asOfDate": day, "rows": rows,
                           "fixedPopulation": len(amounts), "evidenceDigest": digest(rows)})
        return result

    def run(rows):
        return summarize_board("main", iter(rows), population=rows[0]["fixedPopulation"],
                               start=days[0], end=days[-1], expected_days=4)

    def rejected(rows, expected):
        try:
            run(rows)
        except ValueError as error:
            require(expected in str(error), "Unexpected rejection: " + str(error))
        else:
            raise AssertionError("Expected rejection")

    rows = fixture({"a": [10, 20, 40, 60], "b": [100, None, 200, 300],
                    "c": [None, 30, 50, 70], "d": [1, 1, 1, 1]})
    original = deepcopy(rows)
    got = run(rows)
    require([row["observedTotalCny"] for row in got["dailyRows"]] == [111, 51, 291, 431], "Current amounts")
    require(got["dailyRows"][1]["unknownCount"] == 1, "Unknown is not a zero observation")
    checks.append("Current positive amount survives unknown rolling ratio; missing is not zero")
    require(got["basket"]["balancedMembers"] == ["a", "d"]
            and [row["balancedTotalCny"] for row in got["dailyRows"]] == [11, 21, 41, 61], "Fixed complete basket")
    require(got["basket"]["balancedStockShare"] == .5, "Basket stock coverage")
    close(got["basket"]["balancedShareOfObservedSum"], 134 / 884, "Amount share")
    checks.append("Full-period identity intersection and stock/amount coverage")
    require([row["dayCount"] for row in got["epochRows"]] == [2, 2]
            and got["dailyRows"][1]["epoch"] == "before20240924", "A versus T partition")
    checks.append("Actual amount date partition includes 9.24 in the after epoch")
    comp = got["comparisonRow"]
    require(comp["beforeMedianObservedTotalCny"] == 81 and comp["afterMedianObservedTotalCny"] == 361
            and comp["balancedMedianPostPreRatio"] == 51 / 16, "Daily total medians")
    close(comp["observedMedianPostPreRatio"], 361 / 81, "Observed median ratio")
    checks.append("Hand-calculated daily-total medians and post/pre quotients")
    require(rows == original and got["verification"]["observedSumByDayCny"] == 884
            and got["verification"]["observedSymbolDays"] == 14, "Immutable evidence and independent sums")
    checks.append("Input immutability and independent money/number reconciliation")
    empty = run(fixture({"a": [10, None, 30, None], "b": [None, None, None, 40]}))
    require(empty["basket"]["balancedMemberCount"] == 0
            and empty["dailyRows"][1]["observedTotalCny"] is None
            and empty["comparisonRow"]["balancedMedianPostPreRatio"] is None, "Empty basket/day semantics")
    checks.append("Empty complete basket and all-unknown days remain null")
    missing_epoch = run(fixture({"a": [None, None, 30, 40]}))
    require(missing_epoch["comparisonRow"]["observedMedianPostPreRatio"] is None, "Unknown epoch ratio")
    checks.append("All-unknown epoch does not become a zero denominator")
    for invalid in (True, 0, -1, float("inf")):
        bad = deepcopy(rows)
        bad[0]["rows"][0]["amountToday"] = invalid
        if math.isfinite(invalid):
            bad[0]["evidenceDigest"] = digest(bad[0]["rows"])
            rejected(bad, "amountToday")
        else:
            rejected(bad, "Out of range float")
    checks.append("Boolean, zero, negative and nonfinite current amount rejected")
    bad = deepcopy(rows)
    bad[1]["asOfDate"] = bad[0]["asOfDate"]
    rejected(bad, "unordered")
    bad = deepcopy(rows)
    bad[1]["rows"][0]["symbol"] = "z"
    bad[1]["evidenceDigest"] = digest(bad[1]["rows"])
    rejected(bad, "identities changed")
    bad = deepcopy(rows)
    bad[1]["rows"][0]["symbol"] = "b"
    bad[1]["evidenceDigest"] = digest(bad[1]["rows"])
    rejected(bad, "duplicate symbol")
    checks.append("Duplicate dates, duplicate identities and changing sample rejected")
    bad = deepcopy(rows)
    bad[0]["rows"][0]["amountToday"] += 1
    rejected(bad, "digest mismatch")
    checks.append("Evidence digest catches changed values")
    encoded = gzip.compress((json.dumps(rows[0]) + "\n").encode())
    hashed = HashingReader(io.BytesIO(encoded))
    with gzip.GzipFile(fileobj=hashed, mode="rb") as stream:
        decoded = json.loads(stream.read())
    require(decoded == rows[0] and hashed.byte_count == len(encoded)
            and hashed.hasher.hexdigest() == hashlib.sha256(encoded).hexdigest(), "Single-pass compressed hashing")
    checks.append("Compressed stream hashes while decoding without rereading input")
    return {"passed": True, "checks": checks, "syntheticChecks": len(checks), "realEvidenceRead": False,
            "filesWritten": 0, "accountExecutions": 0, "productionUnitIncrement": 0}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
