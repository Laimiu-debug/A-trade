"""Read all three persistence-admission accounts after their independent audit.

Build uses frozen account-summary helpers. --verify reconstructs episodes from
fills and interval arithmetic through separately implemented frozen references.
Neither path creates signals, executes accounts, or chooses outcome thresholds.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAMILY = "leader-persistence-admission-v1"
DEFAULT = ROOT / "artifacts/strategy-search" / FAMILY
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
EXITS = ("trail10",)
POLICIES = ("knownAll", "persistent", "nonpersistent")
STATES = ("passed", "failed", "unknown")
KEYS = ("period", "board", "exit", "policy")
MAIN = "scripts/verify-leader-persistence-admission.py"
OLD = "scripts/verify-online-feedback-readout.py"
OLD_SHA = "dd2f467f25d91ecbd5712b40dc8d7c02717ab4ac82a6d755dd51c306c9c233b2"
EPISODES = "scripts/verify-reclaim-exit-mechanism.py"
EPISODES_SHA = "96ef23022f1a8a515ecf94eabdab8696057bcb166a2cd309fe43f101a507cd89"
TABLE_KINDS = {"yearRows": "calendarYear", "halfYearRows": "calendarHalfYear",
    "phaseRows": "retrospectiveNarrativePhase", "curveEpochRows": "descriptiveEpoch"}
TABLE_COUNTS = dict(yearRows=36, halfYearRows=72, phaseRows=144, curveEpochRows=18)
INTERVAL_MEANING = ("Continuous actual equity and carried positions; whole closed episodes follow final exitDate. "
    "Opportunity and signal counts use A, not historical qualification F. Unequal pre/post spans are not restarted accounts.")
COMPARISON_MEANING = ("Known-all minus persistent is a difference of complete independently replayed accounts. "
    "Nonpersistent is a third executed account, never inferred by subtracting the other two; cash, capacity and later entries differ.")
COVERAGE_MEANING = ("All current-A candidates are ranked before F masks. Every common Top5 slot, including unknown F, remains. "
    "The two known F subsets partition signals, not account PnL; exclusions never refill slots.")


def load_file(name):
    spec = importlib.util.spec_from_file_location("persistence_" + Path(name).stem.replace("-", "_"), ROOT / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACCOUNT = load_file("scripts/summarize-online-feedback-entry.py")
MATH = ACCOUNT.MATH
require, finite, close, total = ACCOUNT.require, ACCOUNT.finite, ACCOUNT.close, ACCOUNT.total
sha, digest, read = ACCOUNT.sha, ACCOUNT.digest, ACCOUNT.read
DEPENDENCIES = {Path(path).relative_to(ROOT).as_posix(): sha(path)
    for path in (ACCOUNT.__file__, ACCOUNT.HELPER, ROOT / OLD, ROOT / EPISODES)}
require(DEPENDENCIES[OLD] == OLD_SHA and DEPENDENCIES[EPISODES] == EPISODES_SHA,
    "Frozen independent arithmetic changed")


def records(path):
    with (gzip.open(path, "rt", encoding="utf-8") if str(path).endswith(".gz")
          else Path(path).open(encoding="utf-8")) as stream:
        return [json.loads(line) for line in stream if line.strip()]


def identity(row):
    return tuple(row[k] for k in KEYS)


def original_signal(row):
    return {k: row[k] for k in ("signalId", "strategyId", "symbol", "signalDate", "originalRank")}


def account_metrics(summary, scheduled, original_count):
    value = ACCOUNT.metrics(summary, scheduled, original_count)
    for name, source in (("medianWinPct", "medianWinReturnPct"), ("medianLossPct", "medianLossReturnPct"),
        ("expectancyReturnPct", "expectancyReturnPct"), ("expectancyPnlAmount", "expectancyPnlAmount")):
        value[name] = summary["closedEpisodes"][source]
    return value


def formation_coverage(key, daily, opportunities, counts, schedules):
    require(len({r["formationId"] for r in opportunities}) == len(opportunities), "Unique original Top5")
    require(counts["targetDayCount"] == len(daily), "Complete target daily evidence")
    require([r["formationId"] for r in opportunities] == [sid for day in daily for sid in day["selectedSignalIds"]],
        "All common Top5 slots in original order")
    require(all(len(day["selectedSignalIds"]) == min(5, len(day["currentCandidates"])) for day in daily),
        "Daily Top5 is formed before F exclusion")
    candidates = sum(len(day["currentCandidates"]) for day in daily)
    excluded = sum(max(0, len(day["currentCandidates"]) - 5) for day in daily)
    states = {s: sum(r["formationStatus"] == s for r in opportunities) for s in STATES}
    require(sum(states.values()) == len(opportunities), "Unknown F cannot become failed")
    for row in opportunities:
        require(row["formationId"] == row["signalId"] and row["formationDate"] == row["signalDate"], "A/signal identity")
        require((row["peerFormationDate"] is None and row["formationStatus"] == "unknown")
            or (isinstance(row["peerFormationDate"], str) and row["peerFormationDate"] < row["formationDate"]), "F/A identity or explicit unknown")
        require(row["originalRank"] in range(1, 6), "Common Top5 rank retained")
        for p in POLICIES:
            allowed = row["formationStatus"] != "unknown" and (p == "knownAll"
                or row["formationStatus"] == {"persistent": "passed", "nonpersistent": "failed"}[p])
            expected = "unknown" if row["formationStatus"] == "unknown" else "included" if allowed else "excluded"
            require(row["policyAdmission"][p]["status"] == expected, "Policy admission state")
            require(row["signals"][p] == (original_signal(row) if allowed else None), "Exact frozen signal mask")
    require(candidates == len(opportunities) + excluded, "Candidate/Top5/rank exclusion conservation")
    policy_counts = {p: {s: sum(r["policyAdmission"][p]["status"] == s for r in opportunities)
        for s in ("included", "excluded", "unknown")} for p in POLICIES}
    derived = dict(currentCandidateCount=candidates, top5OpportunityCount=len(opportunities), rankExcludedCount=excluded,
        selectedFormationCounts=states, policyCounts=policy_counts, signals={p: len(schedules[p]) for p in POLICIES},
        notScheduled={p: len(opportunities) - len(schedules[p]) for p in POLICIES})
    for name, value in derived.items(): require(counts[name] == value, "Input count: " + name)
    for p in POLICIES:
        require(schedules[p] == [r["signals"][p] for r in opportunities if r["signals"][p] is not None],
            "Original-order schedule without refill")
    require(len(schedules["knownAll"]) == len(schedules["persistent"]) + len(schedules["nonpersistent"]),
        "Known signal partition")
    return {**key, "sampleCount": counts["sampleCount"], "targetDayCount": len(daily), **derived,
        "originalOpportunities": len(opportunities), "opportunityDays": len({r["formationDate"] for r in opportunities}),
        "uniqueSymbols": len({r["symbol"] for r in opportunities}), "sourceCounts": deepcopy(counts),
        "conservationPassed": True, "meaning": COVERAGE_MEANING}


def join_episodes(key, opportunities, schedule, result, summary):
    originals = [original_signal(r) for r in opportunities]
    require(schedule == [r["signals"][key["policy"]] for r in opportunities if r["signals"][key["policy"]] is not None],
        "Frozen policy schedule")
    details, entries = ACCOUNT.attach_episodes(key, originals, schedule, result, summary, set())
    by_id = {r["formationId"]: r for r in opportunities}
    for row in details + entries:
        source = by_id[row["formationId"]]
        row.pop("shadowEverEntered", None); row.pop("candidateOnlyEntry", None)
        row.update(signalDate=source["signalDate"], peerFormationDate=source["peerFormationDate"],
            formationStatus=source["formationStatus"], admissionStatus=source["policyAdmission"][key["policy"]]["status"],
            admissionReason=source["policyAdmission"][key["policy"]]["reason"])
        if "entryDate" in row:
            require(row["peerFormationDate"] < row["formationDate"] == row["signalDate"] < row["entryDate"],
                "Historical F, current A and next executable entry")
    return details, entries


def account_coverage(key, opportunities, schedule, details, entries, summary):
    states, admissions = Counter(r["positionState"] for r in details), Counter(r["admissionStatus"] for r in details)
    require(set(states) <= {"notScheduled", "notEntered", "closed", "openAtCutoff"}, "Maturity enum")
    require(sum(states.values()) == len(opportunities) and len(schedule) == len(entries) + states["notEntered"]
        and len(entries) == states["closed"] + states["openAtCutoff"], "Complete opportunity conservation")
    return {**key, "originalOpportunities": len(opportunities), "scheduledSignals": len(schedule),
        "notScheduled": states["notScheduled"], "notEntered": states["notEntered"], "entered": len(entries),
        "closed": states["closed"], "openAtCutoff": states["openAtCutoff"],
        "admissionCounts": {s: admissions[s] for s in ("included", "excluded", "unknown")},
        "accountOpportunityReasons": MATH.labels(r["accountDisposition"]["reason"] for r in details if r["scheduled"]),
        "valuationUnreliableDays": len(summary["quality"]["valuationUnreliableDates"]),
        "openStaleMarks": sum(r["markStaleAtCutoff"] for r in summary["openPositions"]["positions"])}


def interval_rows(key, summary, result, opportunities, schedule):
    answer = []
    for saved in summary["periods"]:
        if saved["id"] == "overall": continue
        start, end = saved["requestedStart"], saved["requestedEnd"]
        fills = [r for r in result["trades"] if start <= r["date"] <= end]
        cohort = [r for r in opportunities if start <= r["formationDate"] <= end]
        selected = sum(r["signals"][key["policy"]] is not None for r in cohort)
        answer.append({**key, **deepcopy(saved), "closedEpisodes": ACCOUNT.compact_closed(saved["closedEpisodes"]),
            "feesPaid": total(r["fee"] for r in fills), "slippageAlreadyInFillPrices": total(r["slippage"] for r in fills),
            "buyFillCount": sum(r["side"] == "buy" for r in fills), "sellFillCount": sum(r["side"] == "sell" for r in fills),
            "sellFillReasons": MATH.labels(r.get("reason") for r in fills if r["side"] == "sell"),
            "queuedExitReasons": MATH.labels(r.get("reason") for r in result.get("decisions", [])
                if start <= r["date"] <= end and r.get("action") == "sell" and r.get("status") == "queued"),
            "originalFormations": len(cohort), "scheduledFromFormations": selected,
            "notScheduledFromFormations": len(cohort) - selected,
            "decisionSignals": sum(start <= r["signalDate"] <= end for r in schedule),
            "formationStatusCounts": {s: sum(r["formationStatus"] == s for r in cohort) for s in STATES},
            "attributionMeaning": INTERVAL_MEANING})
    return answer


def compare_accounts(rows):
    indexed = {identity(r): r["metrics"] for r in rows}
    expected = {(p,b,"trail10",q) for p in PERIODS for b in BOARDS for q in POLICIES}
    require(len(indexed) == len(rows) == 18 and set(indexed) == expected, "All eighteen independent accounts")
    answer = []
    for period in PERIODS:
        for board in BOARDS:
            left, right = [indexed[period,board,"trail10",p] for p in ("persistent", "knownAll")]
            require(left.keys() == right.keys(), "Paired scalar columns")
            answer.append(dict(period=period, board=board, exit="trail10", id="knownAllVsPersistent", role="primary",
                control="persistent", candidate="knownAll", controlMetrics=deepcopy(left), candidateMetrics=deepcopy(right),
                candidateMinusControl={k: right[k]-v if finite(v) and finite(right[k]) else None for k,v in left.items()},
                meaning=COMPARISON_MEANING))
    return answer


def same(actual, expected, label="value"):
    """Independent comparison, including tuple-key provenance diagnostics."""
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), label + " keys")
        for k,v in expected.items(): same(actual[k], v, label + "." + str(k))
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + " length")
        for i,v in enumerate(expected): same(actual[i], v, label + ":" + str(i))
    elif finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7), label + " number")
    else: require(type(actual) is type(expected) and actual == expected, label + " value")


def formation_reference(key, daily, opportunities, counts, schedules):
    """Independent aggregate of the audited common queue and F masks."""
    observed = {s: 0 for s in STATES}
    masks = {p: {s: 0 for s in ("included", "excluded", "unknown")} for p in POLICIES}
    expected = {p: [] for p in POLICIES}
    day_slots = [sid for d in daily for sid in d["selectedSignalIds"]]
    same(day_slots, [r["signalId"] for r in opportunities], "Common daily selection")
    require(len(set(day_slots)) == len(day_slots), "No duplicate common opportunity")
    for r in opportunities:
        state = r["formationStatus"]
        require(state in observed and r["signalId"] == r["formationId"] and 1 <= r["originalRank"] <= 5,
            "Selected identity/rank/status")
        require(r["formationDate"] == r["signalDate"], "A is signal clock")
        require((r["peerFormationDate"] is None and state == "unknown")
            or (isinstance(r["peerFormationDate"], str) and r["peerFormationDate"] < r["formationDate"]), "F before A or unknown")
        observed[state] += 1
        eligible = {"passed": ("knownAll", "persistent"), "failed": ("knownAll", "nonpersistent"), "unknown": ()}[state]
        signal = {k: r[k] for k in ("signalId", "strategyId", "symbol", "signalDate", "originalRank")}
        for p in POLICIES:
            status = "unknown" if state == "unknown" else "included" if p in eligible else "excluded"
            masks[p][status] += 1
            same(r["policyAdmission"][p]["status"], status, "F admission")
            same(r["signals"][p], signal if p in eligible else None, "Exact five-field signal")
            if p in eligible: expected[p].append(signal)
    same(schedules, expected, "All schedules preserve slots/order")
    n = sum(len(d["currentCandidates"]) for d in daily)
    rank_excluded = sum(len(d["currentCandidates"])-len(d["selectedSignalIds"]) for d in daily)
    require(all(len(d["selectedSignalIds"]) == min(5,len(d["currentCandidates"])) for d in daily), "Top5 before F")
    values = dict(currentCandidateCount=n, top5OpportunityCount=len(opportunities), rankExcludedCount=rank_excluded,
        selectedFormationCounts=observed, policyCounts=masks, signals={p:len(expected[p]) for p in POLICIES},
        notScheduled={p:len(opportunities)-len(expected[p]) for p in POLICIES})
    for k,v in values.items(): same(counts[k], v, "Source count " + k)
    same(counts["targetDayCount"], len(daily), "Full calendar")
    return {**key, "sampleCount": counts["sampleCount"], "targetDayCount": len(daily), **values,
        "originalOpportunities": len(opportunities), "opportunityDays": len(set(r["formationDate"] for r in opportunities)),
        "uniqueSymbols": len(set(r["symbol"] for r in opportunities)), "sourceCounts": deepcopy(counts),
        "conservationPassed": True, "meaning": COVERAGE_MEANING}


def episode_reference(key, opportunities, schedule, result, old, episode_math):
    originals = [{k:r[k] for k in ("signalId","strategyId","symbol","signalDate","originalRank")} for r in opportunities]
    facade = SimpleNamespace(require=require, finite=finite, same=same, near=same)
    details, entries = old.episodes_reference(key, originals, schedule, result, set(), facade, episode_math)
    indexed = {r["formationId"]: r for r in opportunities}
    for item in details + entries:
        r = indexed[item["formationId"]]
        item.pop("shadowEverEntered", None); item.pop("candidateOnlyEntry", None)
        item["signalDate"] = r["signalDate"]; item["peerFormationDate"] = r["peerFormationDate"]
        item["formationStatus"] = r["formationStatus"]
        item["admissionStatus"] = r["policyAdmission"][key["policy"]]["status"]
        item["admissionReason"] = r["policyAdmission"][key["policy"]]["reason"]
        if "entryDate" in item: require(item["signalDate"] < item["entryDate"], "Entry after A")
    return details, entries


def interval_reference(key, summary, result, opportunities, schedule, phases, old):
    originals = [{k:r[k] for k in ("signalId","strategyId","symbol","signalDate","originalRank")} for r in opportunities]
    facade = SimpleNamespace(require=require, finite=finite, same=same, near=same)
    measured, partition = old.intervals_reference({**key,"candidate":"baseline"}, result, summary, originals,
        schedule, [], facade, phases)
    partition.pop("candidate")
    for row in measured:
        for k in ("candidate", "modelStatusCounts", "modelReasonCounts"): row.pop(k)
        start,end = row["requestedStart"],row["requestedEnd"]
        cohort = [r for r in opportunities if start <= r["formationDate"] <= end]
        row.update(decisionSignals=sum(start <= s["signalDate"] <= end for s in schedule),
            formationStatusCounts={s:sum(r["formationStatus"] == s for r in cohort) for s in STATES},
            queuedExitReasons=old.labels(r.get("reason") for r in result.get("decisions", [])
                if start <= r["date"] <= end and r.get("action") == "sell" and r.get("status") == "queued"),
            attributionMeaning=INTERVAL_MEANING)
    return measured, partition


class Inputs:
    """Shared hash-only I/O boundary, not a numerical reference implementation."""
    def __init__(self, directory):
        self.directory = Path(directory).resolve(); self.pins = {}
        self.plan, self.comparison, self.completion, self.input_proof, self.proof = [read(self.pin(self.directory/n)) for n in
            ("plan.json", "comparison.json", "input-completion.json", "input-validation.json", "validation.json")]
        plan, comp, done, ip, proof = self.plan,self.comparison,self.completion,self.input_proof,self.proof
        self.plan_sha = sha(self.directory/"plan.json")
        require(plan["schema"] == FAMILY and comp["planSha256"] == done["planSha256"] == self.plan_sha, "Exact plan chain")
        require(proof["passed"] is True and ip["passed"] is True and proof["planSha256"] == ip["planSha256"] == self.plan_sha
            and proof["comparisonSha256"] == sha(self.directory/"comparison.json")
            and proof["inputCompletionSha256"] == ip["inputCompletionSha256"] == sha(self.directory/"input-completion.json")
            and proof["inputValidationSha256"] == sha(self.directory/"input-validation.json"), "Passed input/account audit chain")
        require(proof["verifierSha256"] == plan["sourcePins"][MAIN] == sha(ROOT/MAIN), "Frozen account auditor")
        require((proof["counts"]["accounts"],proof["counts"]["newAccounts"],proof["counts"]["reusedAccounts"]) == (18,18,0), "Scope 18 new accounts")
        same([r["artifactRefs"] for r in proof["accounts"]], comp["rows"], "Exactly audited descriptors")
        for name,value in plan["sourcePins"].items(): self.bound(dict(path=name,sha256=value))
        for name,value in DEPENDENCIES.items(): self.bound(dict(path=name,sha256=value))
        self.pin(__file__)
        self.phases = {r["id"]:r for r in read(self.bound(plan["atlas"]))["phases"]}
        self.descriptors = {identity(r):r for r in comp["rows"]}
        expected = {(p,b,"trail10",q) for p in PERIODS for b in BOARDS for q in POLICIES}
        require(len(self.descriptors) == len(comp["rows"]) == 18 and set(self.descriptors) == expected, "Complete 18 account matrix")
        self.groups = {(r["period"],r["board"]):r for r in done["groups"]}
        require(len(self.groups) == len(done["groups"]) == 6 and set(self.groups) == {(p,b) for p in PERIODS for b in BOARDS}, "Six source groups")

    def pin(self,path):
        path=Path(path).resolve(); require(path.is_relative_to(ROOT), "Repository path only")
        name,value=path.relative_to(ROOT).as_posix(),sha(path)
        require(self.pins.setdefault(name,value)==value,"Input changed during readout")
        return path

    def bound(self,ref):
        path=self.pin(ROOT/ref["path"])
        require(sha(path)==ref["sha256"],"Changed artifact: "+ref["path"])
        return path

    def source(self,period,board):
        group=self.groups[period,board]
        metadata=read(self.bound(group["metadata"]))
        daily=records(self.bound(group["daily"]))
        opportunities=records(self.bound(group["opportunities"]))
        schedules={p:read(self.bound(group["schedules"][p])) for p in POLICIES}
        same([r["formationDate"] for r in daily],metadata["targetDates"],"Every source target day, including zero opportunities")
        return group,metadata,daily,opportunities,schedules

    def account(self,key,group,metadata):
        item=self.descriptors[identity(key)]
        require(item["schedule"]==group["schedules"][key["policy"]] and item["reusedAccount"] is False,"New exact schedule binding")
        result,summary=read(self.bound(item["result"])),read(self.bound(item["summary"]))
        self.bound(item["checkpoint"])
        require(result["status"]=="completed" and summary["inputResultSha256"]==digest(result)
            and summary["policyId"]==key["policy"] and not summary["quality"]["integrityIssues"],"Completed summary identity")
        same([r["date"] for r in result["curve"]],metadata["targetDates"],"Complete target account calendar")
        return item,result,summary


def reference(path):
    return dict(path=Path(path).resolve().relative_to(ROOT).as_posix(),sha256=sha(path))


def detail_account(key,item,summary,metrics):
    return {**key,"artifacts":deepcopy(item),"metrics":metrics,
        **{k:deepcopy(summary[k]) for k in ("account","closedEpisodes","concentration","openPositions","rights",
            "unresolvedExits","reconciliation","quality","warnings")},
        "sourceQualityScalarFlags":{k:v for k,v in summary["sourceQuality"].items() if not isinstance(v,(dict,list))},
        "fullSourceQuality":{"summaryRef":deepcopy(item["summary"]),"field":"sourceQuality"}}


def build(directory=DEFAULT):
    directory=Path(directory).resolve()
    names=("readout.json","opportunity-dispositions.jsonl.gz","entry-episodes.jsonl.gz")
    require(all(not (directory/n).exists() for n in names),"Never overwrite readout outputs")
    inputs=Inputs(directory)
    accounts,flat,coverage,formations,intervals,partitions,details,entries=([] for _ in range(8))
    source_count=0
    for period in PERIODS:
        for board in BOARDS:
            group,metadata,daily,opportunities,schedules=inputs.source(period,board)
            formations.append(formation_coverage(dict(period=period,board=board),daily,opportunities,group["counts"],schedules))
            source_count+=len(opportunities)
            for policy in POLICIES:
                key=dict(period=period,board=board,exit="trail10",policy=policy)
                item,result,summary=inputs.account(key,group,metadata)
                detail,actual=join_episodes(key,opportunities,schedules[policy],result,summary)
                details.extend(detail);entries.extend(actual)
                scalar=account_metrics(summary,len(schedules[policy]),len(opportunities))
                flat.append({**key,"metrics":scalar});accounts.append(detail_account(key,item,summary,scalar))
                coverage.append(account_coverage(key,opportunities,schedules[policy],detail,actual,summary))
                measured=interval_rows(key,summary,result,opportunities,schedules[policy])
                for row in measured:
                    if row["kind"]=="retrospectiveNarrativePhase":
                        phase=inputs.phases[row["id"]]
                        require((row["requestedStart"],row["requestedEnd"])==(phase["startDate"],phase["endDate"]),"Frozen atlas bounds")
                        row["phaseTitle"]=phase["title"]
                intervals.extend(measured)
                partitions.append({**key,"passed":True,"checks":ACCOUNT.partition_intervals(measured,summary,result["curve"])})
            print(json.dumps({"summarized":[period,board],"commonTop5":len(opportunities)}),flush=True)
    tables={name:[r for r in intervals if r["kind"]==kind] for name,kind in TABLE_KINDS.items()}
    require(all(len(tables[k])==v for k,v in TABLE_COUNTS.items()),"Complete fixed time matrices")
    require(source_count==inputs.proof["counts"]["sourceOpportunityCount"] and len(details)==source_count*3,"All original Top5 x three policies")
    contrasts=compare_accounts(flat)
    def write_lines(name,rows):
        path=directory/name
        with gzip.open(path,"xt",encoding="utf-8",newline="\n") as stream:
            for row in rows: stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(",",":"))+"\n")
        return {**reference(path),"count":len(rows),"format":"jsonl+gzip","identity":[*KEYS,"formationId"]}
    counts=dict(accounts=18,newAccounts=18,reusedAccounts=0,comparisons=6,primaryComparisons=6,**TABLE_COUNTS,
        formationCoverageRows=6,coverageRows=18,partitionReconciliation=18,sourceOpportunityCount=source_count,
        currentCandidateCount=sum(r["currentCandidateCount"] for r in formations),policyDispositions=len(details),
        actualEntryEpisodes=len(entries),fullCurveIntervals=18,subIntervals=len(intervals))
    report=dict(schema="leader-persistence-admission-readout-v1",completedAt=datetime.now(timezone.utc).isoformat(),
        planSha256=inputs.plan_sha,comparisonSha256=sha(directory/"comparison.json"),
        verification={"passed":True,**reference(directory/"validation.json")},inputPins=inputs.pins,
        accounts=accounts,accountRows=flat,comparisons=contrasts,primaryComparisons=deepcopy(contrasts),**tables,
        formationCoverageRows=formations,coverageRows=coverage,partitionReconciliation=partitions,
        opportunityRowsArtifact=write_lines(names[1],details),entryRowsArtifact=write_lines(names[2],entries),counts=counts,
        method=dict(account="Each arm is a complete separately funded account; net return includes open marks, fees and capacity constraints.",
            opportunities=COVERAGE_MEANING,comparison=COMPARISON_MEANING,intervals=INTERVAL_MEANING,
            payoff="Means, medians, expectancy, PF/payoff, return tails, holding-duration distributions and concentration describe closed episodes. Open realized/unrealized amounts and durations remain separate; undefined values remain null.",
            clocks="A close forms and signals the candidate itself. F=A-10 is historical qualification only. Actual entry is later; no follower, group ownership, market-stage or outcome gate.",
            atlas="Fixed retrospective narrative stages and 9.24 slices are descriptive; no date is used to change entry or exit."),
        limitations=["2022-2025 repeatedly inspected development history; 2026 is unused and not pristine holdout.",
            "Repeated stock-days, overlapping positions and cross-account results are dependent, not independent causal observations.",
            "Current A strength already requires past strength and breakout; nonpersistent means failed F qualification, not first emergence or a newly discovered market leader.",
            "Common Top5 before F differs from the old F-before-Top5 strategy. No old account is reused or claimed equivalent.",
            "Signal partition is not a return decomposition. Extra signals may alter cash, exposure, rejection and later entries.",
            "Closed winners/losses omit still-open outcomes; profit concentration and open marks must be read with account equity and quality flags.",
            "Close stops cannot guarantee their nominal percentage loss; next-session gaps, execution restrictions and stale/unresolved marks remain.",
            "Relative strength uses CSI 300 (sh000300), not matched board indices or beta-neutral returns; boards are not pure technology sectors."],
        newAccounts=18,referenceAccountsReused=0,accountExecutionsBySummarizer=0,holdoutClaim=False,certified=False,targetAchieved=False)
    (directory/names[0]).write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    return dict(passed=True,readout=reference(directory/names[0]),counts=counts)


def verify(directory=DEFAULT):
    """No call to build, its metrics, episode joins, intervals, or comparisons."""
    directory=Path(directory).resolve();destination=directory/"readout-validation.json"
    require(not destination.exists(),"Never overwrite readout validation")
    inputs=Inputs(directory);report=read(directory/"readout.json")
    require(report["verification"]["passed"] is True and inputs.bound(report["verification"])==directory/"validation.json"
        and report["planSha256"]==inputs.plan_sha and report["comparisonSha256"]==sha(directory/"comparison.json"),"Readout source binding")
    for name,value in report["inputPins"].items(): inputs.bound(dict(path=name,sha256=value))
    old,episode_math=load_file(OLD),load_file(EPISODES)
    accounts,flat,coverage,formations,intervals,partitions,details,entries=([] for _ in range(8))
    source_count=0
    for period in PERIODS:
        for board in BOARDS:
            group,metadata,daily,opportunities,schedules=inputs.source(period,board)
            formations.append(formation_reference(dict(period=period,board=board),daily,opportunities,group["counts"],schedules))
            source_count+=len(opportunities)
            for policy in POLICIES:
                key=dict(period=period,board=board,exit="trail10",policy=policy)
                item,result,summary=inputs.account(key,group,metadata)
                detail,actual=episode_reference(key,opportunities,schedules[policy],result,old,episode_math)
                details.extend(detail);entries.extend(actual)
                metrics=old.metric_reference(summary,len(schedules[policy]),len(opportunities))
                metrics.update({name:summary["closedEpisodes"][source] for name,source in (
                    ("medianWinPct","medianWinReturnPct"),("medianLossPct","medianLossReturnPct"),
                    ("expectancyReturnPct","expectancyReturnPct"),("expectancyPnlAmount","expectancyPnlAmount"))})
                flat.append({**key,"metrics":metrics})
                accounts.append({**key,"artifacts":item,"metrics":metrics,
                    **{n:summary[n] for n in ("account","closedEpisodes","concentration","openPositions","rights",
                        "unresolvedExits","reconciliation","quality","warnings")},
                    "sourceQualityScalarFlags":{n:v for n,v in summary["sourceQuality"].items() if not isinstance(v,(dict,list))},
                    "fullSourceQuality":{"summaryRef":item["summary"],"field":"sourceQuality"}})
                states=Counter(r["positionState"] for r in detail)
                coverage.append({**key,"originalOpportunities":len(opportunities),"scheduledSignals":len(schedules[policy]),
                    "notScheduled":states["notScheduled"],"notEntered":states["notEntered"],"entered":len(actual),
                    "closed":states["closed"],"openAtCutoff":states["openAtCutoff"],
                    "admissionCounts":{s:sum(r["admissionStatus"]==s for r in detail) for s in ("included","excluded","unknown")},
                    "accountOpportunityReasons":old.labels(r["accountDisposition"]["reason"] for r in detail if r["scheduled"]),
                    "valuationUnreliableDays":metrics["unreliableValuationDays"],"openStaleMarks":metrics["openStaleMarks"]})
                measured,partition=interval_reference(key,summary,result,opportunities,schedules[policy],inputs.phases,old)
                intervals.extend(measured);partitions.append(partition)
            print(json.dumps({"verifiedReadout":[period,board],"commonTop5":len(opportunities)}),flush=True)
    indexed={identity(r):r["metrics"] for r in flat};contrasts=[]
    for p in PERIODS:
        for b in BOARDS:
            x,y=indexed[p,b,"trail10","persistent"],indexed[p,b,"trail10","knownAll"]
            contrasts.append(dict(period=p,board=b,exit="trail10",id="knownAllVsPersistent",role="primary",control="persistent",candidate="knownAll",
                controlMetrics=x,candidateMetrics=y,candidateMinusControl={k:y[k]-x[k] if finite(x[k]) and finite(y[k]) else None for k in x},
                meaning=COMPARISON_MEANING))
    expected=dict(accounts=accounts,accountRows=flat,comparisons=contrasts,primaryComparisons=contrasts,
        formationCoverageRows=formations,coverageRows=coverage,partitionReconciliation=partitions)
    for name,kind in TABLE_KINDS.items(): expected[name]=[r for r in intervals if r["kind"]==kind]
    for name,value in expected.items(): same(report[name],value,"Table "+name)
    for name,value in (("opportunityRowsArtifact",details),("entryRowsArtifact",entries)):
        ref=report[name]
        require(ref["count"]==len(value) and ref["format"]=="jsonl+gzip" and ref["identity"]==[*KEYS,"formationId"],"Full sidecar descriptor")
        require(len({tuple(r[k] for k in (*KEYS,"formationId")) for r in value})==len(value),"Unique sidecar episodes")
        same(records(inputs.bound(ref)),value,name)
    counts=dict(accounts=18,newAccounts=18,reusedAccounts=0,comparisons=6,primaryComparisons=6,**TABLE_COUNTS,
        formationCoverageRows=6,coverageRows=18,partitionReconciliation=18,sourceOpportunityCount=source_count,
        currentCandidateCount=sum(r["currentCandidateCount"] for r in formations),policyDispositions=source_count*3,
        actualEntryEpisodes=len(entries),fullCurveIntervals=18,subIntervals=270)
    same(report["counts"],counts,"Complete readout scope")
    require(source_count==inputs.proof["counts"]["sourceOpportunityCount"] and report["newAccounts"]==18
        and report["referenceAccountsReused"]==report["accountExecutionsBySummarizer"]==0
        and report["holdoutClaim"] is report["certified"] is report["targetAchieved"] is False,"Descriptive scope")
    proof=dict(passed=True,schema="leader-persistence-admission-readout-independent-validation-v1",
        completedAt=datetime.now(timezone.utc).isoformat(),planSha256=inputs.plan_sha,comparisonSha256=sha(directory/"comparison.json"),
        readoutSha256=sha(directory/"readout.json"),accountValidationSha256=sha(directory/"validation.json"),
        inputCompletionSha256=sha(directory/"input-completion.json"),verifierSha256=sha(__file__),
        opportunityRowsArtifactSha256=report["opportunityRowsArtifact"]["sha256"],entryRowsArtifactSha256=report["entryRowsArtifact"]["sha256"],
        sourcePins={MAIN:sha(ROOT/MAIN),OLD:OLD_SHA,EPISODES:EPISODES_SHA},counts=counts,
        checks=["All18 complete accounts and three disjoint admission states", "Independent raw-fill episodes and closed/open money",
            "Independent continuous equity, drawdown and270 fixed intervals", "All original Top5 dispositions including unknown",
            "Six knownAll-minus-persistent signed differences; nonpersistent never inferred"])
    destination.write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    return dict(passed=True,validation=reference(destination),counts=counts)


def self_test():
    import unittest
    suite=unittest.defaultTestLoader.discover(str(ROOT/"tests"),pattern="test_leader_persistence_admission_readout.py")
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    require(result.wasSuccessful(),"Synthetic readout checks failed")
    return dict(passed=True,tests=result.testsRun,filesWritten=0,accountExecutions=0)


if __name__ == "__main__":
    parser=ArgumentParser();parser.add_argument("--out",type=Path,default=DEFAULT)
    mode=parser.add_mutually_exclusive_group();mode.add_argument("--self-test",action="store_true");mode.add_argument("--verify",action="store_true")
    args=parser.parse_args()
    print(json.dumps(self_test() if args.self_test else verify(args.out) if args.verify else build(args.out),ensure_ascii=False))
