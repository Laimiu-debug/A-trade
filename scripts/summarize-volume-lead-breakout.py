"""Read both volume-lead admission accounts after their independent audit.

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
FAMILY = "volume-lead-breakout-v1"
DEFAULT = ROOT / "artifacts/strategy-search" / FAMILY
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
EXITS = ("trail10",)
POLICIES = ("priceBreakout", "volumeLead")
STATES = ("passed", "failed", "unknown")
COMMON_STATES = ("candidate", "notTriggered", "commonUnknown")
KEYS = ("period", "board", "exit", "policy")
MAIN = "scripts/verify-volume-lead-breakout.py"
OLD = "scripts/verify-online-feedback-readout.py"
OLD_SHA = "dd2f467f25d91ecbd5712b40dc8d7c02717ab4ac82a6d755dd51c306c9c233b2"
EPISODES = "scripts/verify-reclaim-exit-mechanism.py"
EPISODES_SHA = "96ef23022f1a8a515ecf94eabdab8696057bcb166a2cd309fe43f101a507cd89"
TABLE_KINDS = {"yearRows": "calendarYear", "halfYearRows": "calendarHalfYear",
    "phaseRows": "retrospectiveNarrativePhase", "curveEpochRows": "descriptiveEpoch"}
TABLE_COUNTS = dict(yearRows=24, halfYearRows=48, phaseRows=96, curveEpochRows=12)
INTERVAL_MEANING = ("Continuous actual equity and carried positions; whole closed episodes follow final exitDate. "
    "Opportunity and signal counts use T, while peerFormationDate is the preceding session T-1. "
    "Unequal pre/post spans are not restarted accounts.")
COMPARISON_MEANING = ("Volume-lead minus price-breakout is a signed difference of two complete separately funded accounts. "
    "It is not an additive trade or signal-return decomposition; cash, exposure, rejection and later entries may differ.")
COVERAGE_MEANING = ("All symbol-days retain common-window unknown separately from known common failures. "
    "The shared complete-window price-breakout candidates are ranked before the OBV condition; selected Top5 states "
    "are passed or failed, never unknown. Excluded volume-lead slots are not refilled.")


def load_file(name):
    spec = importlib.util.spec_from_file_location("volume_lead_" + Path(name).stem.replace("-", "_"), ROOT / name)
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
    require(len({d["formationDate"] for d in daily}) == len(daily), "Unique target days")
    by_date = {d["formationDate"]:d for d in daily}
    common = {state:0 for state in COMMON_STATES}; reasons = Counter()
    for day in daily:
        candidates = day["currentCandidates"]
        require(len(day["selectedSignalIds"]) == min(5,len(candidates)), "Top5 before volume gate")
        require(len({r["symbol"] for r in candidates}) == len(candidates), "Unique daily candidates")
        require([r["originalRank"] for r in candidates] == list(range(1,len(candidates)+1)), "Common candidate ranks")
        require(all(r["selectedTop5"] == (i<5) for i,r in enumerate(candidates)), "No candidate refill")
        dc = day["counts"]["commonStatusCounts"]
        require(set(dc) == set(COMMON_STATES) and all(type(v) is int and v>=0 for v in dc.values()), "Common status domain")
        require(dc["candidate"] == len(candidates) and sum(dc.values()) == counts["sampleCount"], "All symbol-day common states")
        for state in COMMON_STATES: common[state] += dc[state]
        rc = day["counts"]["commonReasonCounts"]
        require(all(type(v) is int and v>=0 for v in rc.values()), "Common reason counts")
        reasons.update(rc)
    candidates = sum(len(day["currentCandidates"]) for day in daily)
    excluded = sum(max(0,len(day["currentCandidates"])-5) for day in daily)
    states = {state:sum(r["formationStatus"] == state for r in opportunities) for state in STATES}
    require(states["unknown"] == 0 and sum(states.values()) == len(opportunities), "Selected OBV evidence is known")
    for row in opportunities:
        require(row["formationId"] == row["signalId"] and row["formationDate"] == row["signalDate"], "T/signal identity")
        require(isinstance(row["peerFormationDate"],str) and row["peerFormationDate"] < row["formationDate"], "T-1 is before T")
        require(row["originalRank"] in range(1,6), "Common Top5 rank retained")
        day = by_date[row["formationDate"]]
        require(row["peerFormationDate"] == day["peerFormationDate"], "Daily preceding-session identity")
        candidate = day["currentCandidates"][row["originalRank"]-1]
        require(candidate["symbol"] == row["symbol"] and candidate["formationStatus"] == row["formationStatus"], "Selected common queue identity")
        for policy in POLICIES:
            allowed = policy == "priceBreakout" or row["formationStatus"] == "passed"
            require(row["policyAdmission"][policy]["status"] == ("included" if allowed else "excluded"), "Known policy mask")
            require(row["signals"][policy] == (original_signal(row) if allowed else None), "Exact five-field signal mask")
    require(candidates == len(opportunities)+excluded, "Candidate/Top5/rank exclusion conservation")
    policy_counts = {p:{state:sum(r["policyAdmission"][p]["status"] == state for r in opportunities)
        for state in ("included","excluded","unknown")} for p in POLICIES}
    derived = dict(currentCandidateCount=candidates,top5OpportunityCount=len(opportunities),rankExcludedCount=excluded,
        selectedFormationCounts=states,policyCounts=policy_counts,signals={p:len(schedules[p]) for p in POLICIES},
        notScheduled={p:len(opportunities)-len(schedules[p]) for p in POLICIES},
        commonStatusCounts=common,commonReasonCounts=dict(sorted(reasons.items())))
    for name,value in derived.items(): require(counts[name] == value, "Input count: "+name)
    for policy in POLICIES:
        require(schedules[policy] == [r["signals"][policy] for r in opportunities if r["signals"][policy] is not None],
            "Original-order schedule without refill")
    require(len(schedules["priceBreakout"]) == len(opportunities) and len(schedules["volumeLead"]) == states["passed"],
        "Candidate schedule is a subset, not a separate partition account")
    return {**key,"sampleCount":counts["sampleCount"],"targetDayCount":len(daily),
        "symbolDayCount":counts["sampleCount"]*len(daily),**derived,
        "originalOpportunities":len(opportunities),"opportunityDays":len({r["formationDate"] for r in opportunities}),
        "uniqueSymbols":len({r["symbol"] for r in opportunities}),"sourceCounts":deepcopy(counts),
        "conservationPassed":True,"meaning":COVERAGE_MEANING}


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
                "Preceding OBV session, T close and next executable entry")
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
    require(len(indexed) == len(rows) == 12 and set(indexed) == expected, "All twelve independent accounts")
    answer = []
    for period in PERIODS:
        for board in BOARDS:
            left, right = [indexed[period,board,"trail10",p] for p in ("priceBreakout", "volumeLead")]
            require(left.keys() == right.keys(), "Paired scalar columns")
            answer.append(dict(period=period, board=board, exit="trail10", id="volumeLeadVsPriceBreakout", role="primary",
                control="priceBreakout", candidate="volumeLead", controlMetrics=deepcopy(left), candidateMetrics=deepcopy(right),
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
    """Independent aggregation of common symbol-days, shared queue and known OBV masks."""
    observed = {state:0 for state in STATES}
    masks = {policy:{state:0 for state in ("included","excluded","unknown")} for policy in POLICIES}
    expected = {policy:[] for policy in POLICIES}
    full_states = dict(candidate=0,notTriggered=0,commonUnknown=0); full_reasons = {}
    selected = {}; candidate_count = 0; rank_excluded = 0; day_dates = []
    flattened = []
    for day in daily:
        date = day["formationDate"]; day_dates.append(date)
        queue = day["currentCandidates"]; n = len(queue)
        candidate_count += n; rank_excluded += max(n-5,0)
        same(len(day["selectedSignalIds"]),min(n,5),"Daily common queue size")
        require(len({v["symbol"] for v in queue}) == n,"No repeated symbol on a target day")
        for offset,item in enumerate(queue):
            same(item["originalRank"],offset+1,"Rank precedes OBV")
            same(item["selectedTop5"],offset<5,"Selected common candidate")
            if offset<5:
                sid=day["selectedSignalIds"][offset]
                require(sid not in selected,"Unique selected ID")
                selected[sid]=(date,day["peerFormationDate"],offset+1,item)
                flattened.append(sid)
        state_counts=day["counts"]["commonStatusCounts"]
        same(set(state_counts),set(full_states),"Common evidence state domain")
        require(all(type(v) is int and v>=0 for v in state_counts.values()),"Nonnegative common counts")
        same(state_counts["candidate"],n,"Common candidates")
        same(sum(state_counts.values()),counts["sampleCount"],"Daily fixed population")
        for state in full_states:full_states[state]+=state_counts[state]
        for reason,value in day["counts"]["commonReasonCounts"].items():
            require(type(value) is int and value>=0,"Reason count domain")
            full_reasons[reason]=full_reasons.get(reason,0)+value
    require(len(day_dates)==len(set(day_dates)),"Unique daily calendar")
    same(flattened,[r["formationId"] for r in opportunities],"Complete ordered common queue")
    for row in opportunities:
        state=row["formationStatus"]
        require(state in ("passed","failed"),"Selected complete OBV status")
        require(row["signalId"]==row["formationId"],"Opportunity signal identity")
        date,previous,rank,item=selected[row["formationId"]]
        require(isinstance(previous,str) and previous<date,"Prior source session exists")
        same((row["formationDate"],row["signalDate"],row["peerFormationDate"],row["originalRank"],row["symbol"],state),
            (date,date,previous,rank,item["symbol"],item["formationStatus"]),"Selected identity")
        observed[state]+=1
        signal={name:row[name] for name in ("signalId","strategyId","symbol","signalDate","originalRank")}
        for policy in POLICIES:
            admitted=(policy=="priceBreakout") or state=="passed"
            status="included" if admitted else "excluded"
            masks[policy][status]+=1
            same(row["policyAdmission"][policy]["status"],status,"OBV admission")
            same(row["signals"][policy],signal if admitted else None,"Original five-field signal")
            if admitted:expected[policy].append(signal)
    same(schedules,expected,"No replacement or schedule reordering")
    values=dict(currentCandidateCount=candidate_count,top5OpportunityCount=len(opportunities),rankExcludedCount=rank_excluded,
        selectedFormationCounts=observed,policyCounts=masks,signals={p:len(expected[p]) for p in POLICIES},
        notScheduled={p:len(opportunities)-len(expected[p]) for p in POLICIES},
        commonStatusCounts=full_states,commonReasonCounts=dict(sorted(full_reasons.items())))
    for name,value in values.items():same(counts[name],value,"Source count "+name)
    same(counts["targetDayCount"],len(daily),"All target dates")
    return {**key,"sampleCount":counts["sampleCount"],"targetDayCount":len(daily),
        "symbolDayCount":counts["sampleCount"]*len(daily),**values,
        "originalOpportunities":len(opportunities),"opportunityDays":len(set(r["formationDate"] for r in opportunities)),
        "uniqueSymbols":len(set(r["symbol"] for r in opportunities)),"sourceCounts":deepcopy(counts),
        "conservationPassed":True,"meaning":COVERAGE_MEANING}


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
        if "entryDate" in item: require(item["signalDate"] < item["entryDate"], "Entry after T")
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
        require((proof["counts"]["accounts"],proof["counts"]["newAccounts"],proof["counts"]["reusedAccounts"]) == (12,12,0), "Scope 12 new accounts")
        same([r["artifactRefs"] for r in proof["accounts"]], comp["rows"], "Exactly audited descriptors")
        for name,value in plan["sourcePins"].items(): self.bound(dict(path=name,sha256=value))
        for name,value in DEPENDENCIES.items(): self.bound(dict(path=name,sha256=value))
        self.pin(__file__)
        self.phases = {r["id"]:r for r in read(self.bound(plan["atlas"]))["phases"]}
        self.descriptors = {identity(r):r for r in comp["rows"]}
        expected = {(p,b,"trail10",q) for p in PERIODS for b in BOARDS for q in POLICIES}
        require(len(self.descriptors) == len(comp["rows"]) == 12 and set(self.descriptors) == expected, "Complete 12 account matrix")
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
    require(source_count==inputs.proof["counts"]["sourceOpportunityCount"] and len(details)==source_count*2,"All original Top5 x two policies")
    contrasts=compare_accounts(flat)
    def write_lines(name,rows):
        path=directory/name
        with gzip.open(path,"xt",encoding="utf-8",newline="\n") as stream:
            for row in rows: stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(",",":"))+"\n")
        return {**reference(path),"count":len(rows),"format":"jsonl+gzip","identity":[*KEYS,"formationId"]}
    counts=dict(accounts=12,newAccounts=12,reusedAccounts=0,comparisons=6,primaryComparisons=6,**TABLE_COUNTS,
        formationCoverageRows=6,coverageRows=12,partitionReconciliation=12,sourceOpportunityCount=source_count,
        currentCandidateCount=sum(r["currentCandidateCount"] for r in formations),policyDispositions=len(details),
        actualEntryEpisodes=len(entries),fullCurveIntervals=12,subIntervals=len(intervals))
    report=dict(schema="volume-lead-breakout-readout-v1",completedAt=datetime.now(timezone.utc).isoformat(),
        planSha256=inputs.plan_sha,comparisonSha256=sha(directory/"comparison.json"),
        verification={"passed":True,**reference(directory/"validation.json")},inputPins=inputs.pins,
        accounts=accounts,accountRows=flat,comparisons=contrasts,primaryComparisons=deepcopy(contrasts),**tables,
        formationCoverageRows=formations,coverageRows=coverage,partitionReconciliation=partitions,
        opportunityRowsArtifact=write_lines(names[1],details),entryRowsArtifact=write_lines(names[2],entries),counts=counts,
        method=dict(account="Each arm is a complete separately funded account; net return includes open marks, fees and capacity constraints.",
            opportunities=COVERAGE_MEANING,comparison=COMPARISON_MEANING,intervals=INTERVAL_MEANING,
            payoff="Means, medians, expectancy, PF/payoff, return tails, holding-duration distributions and concentration describe closed episodes. Open realized/unrealized amounts and durations remain separate; undefined values remain null.",
            clocks="T close forms and signals the candidate itself. peerFormationDate is T-1, the preceding-session OBV high test. Both arms share full 23-session OHLCVA, first rolling 20-high price breakout and the preceding upward close; actual entry is later.",
            atlas="Fixed retrospective narrative stages and 9.24 slices are descriptive; no date is used to change entry or exit."),
        limitations=["2022-2025 repeatedly inspected development history; 2026 is unused and not pristine holdout.",
            "Repeated stock-days, overlapping positions and cross-account results are dependent, not independent causal observations.",
            "The common population already requires a preceding upward close and current price breakout. This comparison describes the extra OBV-history admission condition within that population, not unique causal volume information.",
            "OBV is price-direction-signed trading volume, not traded-value net inflow, main-force or retail ownership. Missing common windows are not failed OBV signals.",
            "The candidate is a subset of a common Top5 queue, not a decomposition of account PnL. Exclusions may alter cash, exposure, rejection and later entries.",
            "Closed winners/losses omit still-open outcomes; profit concentration and open marks must be read with account equity and quality flags.",
            "Close stops cannot guarantee their nominal percentage loss; next-session gaps, execution restrictions and stale/unresolved marks remain.",
            "No relative-strength, main/retail-identity or market-regime filter is introduced. Board membership is not a pure technology-sector classification; inherited raw-price and status/action quality limitations remain."],
        newAccounts=12,referenceAccountsReused=0,accountExecutionsBySummarizer=0,holdoutClaim=False,certified=False,targetAchieved=False)
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
            x,y=indexed[p,b,"trail10","priceBreakout"],indexed[p,b,"trail10","volumeLead"]
            contrasts.append(dict(period=p,board=b,exit="trail10",id="volumeLeadVsPriceBreakout",role="primary",control="priceBreakout",candidate="volumeLead",
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
    counts=dict(accounts=12,newAccounts=12,reusedAccounts=0,comparisons=6,primaryComparisons=6,**TABLE_COUNTS,
        formationCoverageRows=6,coverageRows=12,partitionReconciliation=12,sourceOpportunityCount=source_count,
        currentCandidateCount=sum(r["currentCandidateCount"] for r in formations),policyDispositions=source_count*2,
        actualEntryEpisodes=len(entries),fullCurveIntervals=12,subIntervals=180)
    same(report["counts"],counts,"Complete readout scope")
    require(source_count==inputs.proof["counts"]["sourceOpportunityCount"] and report["newAccounts"]==12
        and report["referenceAccountsReused"]==report["accountExecutionsBySummarizer"]==0
        and report["holdoutClaim"] is report["certified"] is report["targetAchieved"] is False,"Descriptive scope")
    proof=dict(passed=True,schema="volume-lead-breakout-readout-independent-validation-v1",
        completedAt=datetime.now(timezone.utc).isoformat(),planSha256=inputs.plan_sha,comparisonSha256=sha(directory/"comparison.json"),
        readoutSha256=sha(directory/"readout.json"),accountValidationSha256=sha(directory/"validation.json"),
        inputCompletionSha256=sha(directory/"input-completion.json"),verifierSha256=sha(__file__),
        opportunityRowsArtifactSha256=report["opportunityRowsArtifact"]["sha256"],entryRowsArtifactSha256=report["entryRowsArtifact"]["sha256"],
        sourcePins={MAIN:sha(ROOT/MAIN),OLD:OLD_SHA,EPISODES:EPISODES_SHA},counts=counts,
        checks=["All12 complete accounts and shared Top5 before known OBV admission", "Independent raw-fill episodes and closed/open money",
            "Independent continuous equity, drawdown and180 fixed intervals", "All original Top5 dispositions; full-population common unknown separately preserved",
            "Six volumeLead-minus-priceBreakout signed differences; no additive arm decomposition"])
    destination.write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    return dict(passed=True,validation=reference(destination),counts=counts)


def self_test():
    import unittest
    suite=unittest.defaultTestLoader.discover(str(ROOT/"tests"),pattern="test_volume_lead_breakout_readout.py")
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    require(result.wasSuccessful(),"Synthetic readout checks failed")
    return dict(passed=True,tests=result.testsRun,filesWritten=0,accountExecutions=0)


if __name__ == "__main__":
    parser=ArgumentParser();parser.add_argument("--out",type=Path,default=DEFAULT)
    mode=parser.add_mutually_exclusive_group();mode.add_argument("--self-test",action="store_true");mode.add_argument("--verify",action="store_true")
    args=parser.parse_args()
    print(json.dumps(self_test() if args.self_test else verify(args.out) if args.verify else build(args.out),ensure_ascii=False))
