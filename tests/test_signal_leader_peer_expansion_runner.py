"""Temporary raw-source integration fixtures; no actual research data is read.

The panel uses real causal price-feature calculations plus hand-authored cross-sectional
strength labels and a test-only audit. Source binding, parent evaluation, peers,
selection, opening events, path risks and lifecycle execute without source mocks.
"""
from argparse import Namespace
from contextlib import closing, contextmanager, redirect_stdout
from copy import deepcopy
from datetime import date, timedelta
import gzip
import importlib.util
import io
import json
import math
from pathlib import Path
import shutil
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "scripts/run-signal-leader-peer-expansion.py"
REGISTRATION = "docs/signal-leader-peer-expansion-v1.json"


def module():
    spec = importlib.util.spec_from_file_location("leader_peer_expansion_runner_test", ROOT/SOURCE)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")


def write_lines(path, rows):
    with gzip.open(path,"wt",encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n")


def lines(path):
    with gzip.open(path,"rt",encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


class LeaderPeerExpansionRunnerTests(unittest.TestCase):
    """Public isolated fixture for the separate independent verifier."""
    def setUp(self):
        self.runner = module()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace, self.source = self.root/"workspace", self.root/"tdx"
        self.panel, self.output = self.root/"panel", self.root/"output"
        self.workspace.mkdir()
        self.panel.mkdir()
        for market in ("sh","sz"):
            (self.source/"vipdoc"/market/"lday").mkdir(parents=True)
        with closing(sqlite3.connect(self.workspace/"market.sqlite3")) as db, db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("CREATE TABLE securities(symbol TEXT,name TEXT)")
            db.execute("INSERT INTO meta VALUES('path',?)", (json.dumps(str(self.source)),))
        self.dates, day = [], date(2022,1,3)
        while len(self.dates) < 190:
            if day.weekday() < 5:
                self.dates.append(day.isoformat())
            day += timedelta(days=1)
        self.symbols = [f"sh600{i:03d}" for i in range(1,27)]
        self.target_dates = self.dates[130:154]
        for i, symbol in enumerate(self.symbols):
            self.write_source(symbol, "leader" if i < 6 else "peer")
        self.write_source("sh000300", "flat")
        self.doc = self.runner.read_json(ROOT/REGISTRATION)
        self.document = self.doc
        self.doc["registeredAt"] = "synthetic-only"
        self.doc["sample"].update(signalStart=self.target_dates[0],signalEnd=self.target_dates[-1],warmupStart=self.dates[0])
        self.doc["config"]["startDate"], self.doc["config"]["endDate"] = self.target_dates[0],self.target_dates[-1]
        self.doc["sourceBinding"].update(workspace="workspace",panelDirectory="panel")
        self.data = self.dataset()
        self.doc["sample"].update(expectedFullSnapshotFingerprint=self.data["fingerprint"],
            expectedFullSecurities=len(self.symbols),expectedFullBars=sum(len(s["bars"]) for s in self.data["securities"]),
            expectedScoredSessions=len(self.target_dates),expectedWarmupSessions=130,expectedCalendarSessions=154,
            expectedFeatureRows=len(self.symbols)*len(self.target_dates))
        self.registration = self.root/REGISTRATION
        self.registration.parent.mkdir()
        closure = set(self.doc["sourceImplementation"]) | {SOURCE, "service/signal_leader_peer_expansion.py",
            "tests/test_signal_leader_peer_expansion.py","tests/test_signal_leader_peer_expansion_runner.py"}
        closure.update(getattr(self.runner,"AUDIT_REFERENCES", {}))
        closure.update(getattr(self.runner,"ADDITIONAL_IMPLEMENTATION", ()))
        for name in closure:
            destination = self.root/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,destination)
        self.build_panel()

    def write_source(self,symbol,kind,*,gap_index=None):
        content, previous = bytearray(),1000
        for i, day in enumerate(self.dates):
            growth = 0.008+0.003*math.sin(i*.7)
            if kind == "peer" and 131 <= i <= 135:
                growth = -0.008+0.003*math.sin(i*.7)
            if i >= 136 and kind != "flat":
                growth = 0.035+0.003*math.sin(i*.7)
            closing = round(previous*(1+growth)) if kind != "flat" else previous
            opening = previous
            if i != gap_index:
                content.extend(struct.pack("<IIIIIfII",int(day.replace("-","")),opening,max(opening,closing)+1,
                    min(opening,closing)-1,closing,100_000_000.,10_000_000,0))
            previous = closing
        (self.source/"vipdoc"/symbol[:2]/"lday"/(symbol+".day")).write_bytes(content)

    def args(self,**changes):
        values={"workspace":self.workspace,"output":self.output,"diagnostic_unverified_history":True}
        values.update(changes)
        return Namespace(**values)

    def dataset(self):
        sample=self.doc["sample"]
        data=self.runner.runtime.snapshot_universe(self.workspace,sample["selection"],sample["signalEnd"],sample["signalStart"])
        return self.runner.source_tools.bound_snapshot(data,sample["signalStart"],sample["signalEnd"])

    def build_panel(self):
        identity={}
        for s in self.data["securities"]:
            evidence={k:v for k,v in s.items() if k not in ("bars","name","sourceSha256","forceSeed","historyWindow")}
            identity[s["symbol"]]={"retainedRawBarsSha256":self.runner.digest(s["bars"]),
                "sourceSha256":s.get("sourceSha256"),"datedEvidenceSha256":self.runner.digest(evidence)}
        inputs={"snapshotFingerprint":self.data["fingerprint"],"fullDatasetContentSha256":self.runner.digest(self.data),
            "symbols":self.symbols,"symbolCount":len(self.symbols),"symbolsSha256":self.runner.digest(self.symbols),
            "retainedBarCount":sum(len(s["bars"]) for s in self.data["securities"]),"securities":identity,
            "calendar":{"dates":self.data["calendar"],"sha256":self.runner.digest(self.data["calendar"])},
            "benchmark":{"symbol":"sh000300","rawBarsSha256":self.runner.digest(self.data["benchmark"]["bars"])}}
        with zipfile.ZipFile(self.panel/"execution-sources.zip","w") as z:
            z.writestr("synthetic-contract.txt",b"Eligibility fixture only; no actual market audit.")
        import hashlib
        self.manifest={"featureVersion":"daily-leadership-raw-v1","inputs":inputs,
            "physicalSourceHashes":self.runner.physical_source_hashes(self.workspace,self.doc["sample"]),
            "implementation":{"synthetic-contract.txt":hashlib.sha256(b"Eligibility fixture only; no actual market audit.").hexdigest()},
            "executionArchiveSha256":self.runner.sha(self.panel/"execution-sources.zip")}
        write(self.panel/"manifest.json",self.manifest)
        self.manifest_hash=self.runner.sha(self.panel/"manifest.json")
        from service.signal_leadership_features import daily_features
        calendar = self.data["calendar"]
        sources = {security["symbol"]: security for security in self.data["securities"]}
        raw = {symbol: {bar["date"]: {**bar,"priceBasisId":"raw-unadjusted"} for bar in security["bars"]}
               for symbol,security in sources.items()}
        benchmark = {bar["date"]: {**bar,"priceBasisId":"raw-unadjusted"} for bar in self.data["benchmark"]["bars"]}
        self.rows=[]
        for day in self.target_dates:
            index=calendar.index(day)
            slots=calendar[index-60:index+1]
            for n,symbol in enumerate(self.symbols):
                row=daily_features(symbol,day,slots,[raw[symbol].get(d) for d in slots],[benchmark.get(d) for d in slots])
                strength=.95 if n < 6 else .5
                # Synthetic ranks stand in for the already audited panel, without re-ranking this small fixture.
                row.update(relativeReturn20Percentile=strength,relativeReturn60Percentile=.96-n*.001 if n<6 else .5,
                    relativeReturn20CrossSectionCount=100,relativeReturn60CrossSectionCount=100,
                    trailing20DailyStrengthPercentiles=[strength]*20,trailing20StrengthCrossSectionCounts=[100]*20,
                    strengthRankSessionDates20=slots[-20:],knownStrengthRankSessions20=20,availableStrengthRankSessions20=20,
                    meanStrengthPercentile20=strength,minimumStrengthPercentile20=strength,topQuantileSessionCount20=None,
                    sampleManifestHash=self.manifest_hash,calendarHash=inputs["calendar"]["sha256"],
                    sourceHashes={**identity[symbol],"benchmarkRawBarsSha256":inputs["benchmark"]["rawBarsSha256"]},
                    baseEligibility={"eligible":True,"reason":"eligible","unknown":["historicalSt","historicalMembership","listingDate"]})
                self.rows.append(row)
        write_lines(self.panel/"daily-coverage.jsonl.gz",[])
        write_lines(self.panel/"warmup-coverage.jsonl.gz",[])
        write(self.panel/"summary.json",{"synthetic":True})
        write(self.panel/"attempt.json",{"synthetic":True})
        self.audit={"passed":True,"failureCount":0,"sampleManifestHash":self.manifest_hash,
                    "featureRows":len(self.rows),"testOnly":True}
        self.rebind()

    def rebind(self):
        write_lines(self.panel/"features.jsonl.gz",self.rows)
        write(self.panel/"independent-verification.json",self.audit)
        completion={"sampleManifestHash":self.manifest_hash,"featureRows":len(self.symbols)*len(self.target_dates),
            "targetSessions":len(self.target_dates),"files":{name:{"path":name,"sha256":self.runner.sha(self.panel/name)}
                for name in self.runner.PANEL_FILES}}
        write(self.panel/"completion.json",completion)
        self.doc["sourceBinding"]["files"]={p.name:self.runner.sha(p) for p in self.panel.iterdir()}
        write(self.registration,self.doc)
        self.registration_hash=self.runner.sha(self.registration)

    @contextmanager
    def registered(self):
        with patch.object(self.runner,"ROOT",self.root),patch.object(self.runner,"__file__",str(self.root/SOURCE)),\
                patch.object(self.runner,"REGISTRATION",self.registration),\
                patch.object(self.runner,"REGISTRATION_SHA256",self.registration_hash):
            yield

    def freeze(self):
        with self.registered(),redirect_stdout(io.StringIO()):
            return self.runner.freeze(self.args())

    def execute(self):
        with self.registered(),redirect_stdout(io.StringIO()):
            return self.runner.execute(self.args())

    def run_complete(self):
        self.freeze()
        return self.execute()

    def attempt(self):
        return self.output/"attempts/attempt-0001"

    def collect(self,destination=None):
        path=destination or self.root/"collected"
        path.mkdir()
        with self.registered():
            return self.runner.collect_signals(self.data,self.runner.load_binding(),path)

    def result(self):
        with gzip.open(self.attempt()/"result.json.gz","rt",encoding="utf-8") as stream:
            return json.load(stream)

    def test_exclusive_freeze_is_metadata_only_and_archive_closure(self):
        with patch.object(self.runner,"collect_signals",side_effect=AssertionError("no classification during freeze")),\
                patch.object(self.runner,"load_dataset",side_effect=AssertionError("no raw load during freeze")):
            protocol=self.freeze()
        self.assertFalse((self.output/"attempts").exists())
        self.assertIn("service/signal_persistent_leader.py",protocol["implementation"])
        self.assertIn("tests/test_signal_leader_peer_expansion_runner.py",protocol["implementation"])
        with zipfile.ZipFile(self.output/"execution-sources.zip") as archive:
            self.assertEqual(set(archive.namelist()),set(protocol["implementation"]))
        with self.assertRaisesRegex(ValueError,"Exclusive freeze"):
            self.freeze()

    def test_full_population_first_ten_unknown_then_frozen_peers_and_raw_immutability(self):
        before=deepcopy(self.data)
        signals,performance=self.collect()
        self.assertEqual(self.data,before)
        rows=lines(self.root/"collected/predicates.jsonl.gz")
        self.assertEqual(len(rows),26*24)
        self.assertTrue(all(row["temporalClassification"]=="formationUnavailable" for row in rows[:260]))
        self.assertTrue(all(row["formationParent"]["outcomeSha256"] is None for row in rows[:260]))
        prior={(row["symbol"],row["date"]):row["currentParent"] for row in rows}
        for row in rows[260:]:
            formation=row["formationParent"]
            self.assertEqual(formation["outcomeSha256"],self.runner.digest(prior[row["symbol"],formation["date"]]))
            self.assertEqual(self.data["calendar"].index(row["date"])-self.data["calendar"].index(formation["date"]),10)
        self.assertEqual(performance["parentCandidates"],6*14)
        self.assertEqual(performance["parentTop5"],5*14)
        self.assertFalse(any(signal["symbol"]==self.symbols[5] for signal in signals))
        groups=lines(self.root/"collected/peer-groups.jsonl.gz")
        self.assertEqual(len(groups),70)
        first=groups[0]
        self.assertEqual(first["formationDate"],self.target_dates[0])
        self.assertEqual(first["date"],self.target_dates[10])
        self.assertEqual(len(first["group"]["candidateEvaluations"]),26)
        self.assertEqual(len(first["group"]["members"]),20)
        self.assertEqual(first["groupSha256"],self.runner.digest(first["group"]))
        self.assertNotIn(first["symbol"],first["group"]["members"])
        self.assertGreater(performance["candidateSignalCount"],0)
        self.assertEqual(performance["controlSignalCount"],70)
        self.assertEqual({s["rank"] for s in signals},set(range(1,6)))

    def test_f_no_breakout_allowed_but_unevaluable_even_five_true_never_upgraded(self):
        # A high wick at F invalidates only F breakout, not its five archived strength tests.
        symbol=self.symbols[0]
        security=next(s for s in self.data["securities"] if s["symbol"]==symbol)
        previous=next(b for b in security["bars"] if b["date"]==self.dates[129])
        previous["high"]*=1.1
        self.data["fingerprint"]=self.runner.digest({k:v for k,v in self.data.items() if k!="fingerprint"})
        self.doc["sample"]["expectedFullSnapshotFingerprint"]=self.data["fingerprint"]
        self.build_panel()
        signals,_=self.collect()
        initial=lines(self.root/"collected/predicates.jsonl.gz")[0]
        self.assertEqual(initial["currentParent"]["kind"],"notTriggered")
        self.assertTrue(all(initial["currentParent"]["predicates"][name]["passed"] is True for name,_,_ in self.runner.THRESHOLDS))
        self.assertTrue(any(s["symbol"]==symbol and s["signalDate"]==self.target_dates[10] for s in signals))
        self.rows[0]["benchmarkQuality"]["complete"]=False
        self.rebind()
        signals,_=self.collect(self.root/"unknownF")
        first=lines(self.root/"unknownF/predicates.jsonl.gz")[0]
        self.assertEqual(first["currentParent"]["kind"],"unevaluable")
        self.assertTrue(all(first["currentParent"]["predicates"][name]["passed"] is True for name,_,_ in self.runner.THRESHOLDS))
        self.assertFalse(any(s["symbol"]==symbol and s["signalDate"]==self.target_dates[10] for s in signals))

    def test_unknown_expansion_keeps_baseline_original_rank_and_no_sixth_backfill(self):
        evaluate=self.runner.evaluate_peer_expansion
        calls=[]
        def unknown_first(group,*args):
            calls.append((group["anchor"],group["formationDate"]))
            result=evaluate(group,*args)
            if group["anchor"]==self.symbols[0]:
                result.update(kind="unevaluable",reason="syntheticMissingMember",expanded=None,
                              beforeCount=None,nowCount=None,countChange=None)
            return result
        with patch.object(self.runner,"evaluate_peer_expansion",side_effect=unknown_first):
            signals,performance=self.collect()
        self.assertNotIn(self.symbols[5],{symbol for symbol,_ in calls})
        first=[s for s in signals if s["signalDate"]==self.target_dates[10]]
        self.assertEqual([s["rank"] for s in first if s["strategyId"]==self.runner.CANDIDATE_ID],[2,3,4,5])
        self.assertEqual([s["rank"] for s in first if s["strategyId"]==self.runner.CONTROL_ID],[1,2,3,4,5])
        result=self.runner.evaluate_signals(self.data,self.doc["config"],signals)
        ledger,accounting=self.runner.slot_dispositions(lines(self.root/"collected/selection.jsonl.gz"),signals,result)
        self.assertEqual(ledger[0]["candidateDisposition"]["status"],"filteredUnknown")
        self.assertEqual(ledger[0]["baselineDisposition"]["status"],"attempted")
        self.assertEqual(accounting["baselineAdmitted"],performance["parentTop5"])

    def test_benchmark_and_each_peer_raw_history_route_known_halts(self):
        self.data["benchmark"]["tradeStatusByDate"]={self.dates[130]:False}
        self.data["fingerprint"]=self.runner.digest({k:v for k,v in self.data.items() if k!="fingerprint"})
        self.doc["sample"]["expectedFullSnapshotFingerprint"]=self.data["fingerprint"]
        self.build_panel()
        before=deepcopy(self.data)
        signals,_=self.collect()
        first=lines(self.root/"collected/peer-groups.jsonl.gz")[0]
        self.assertEqual(first["group"]["kind"],"unevaluable")
        self.assertIn("suspension",first["group"]["benchmarkEvidence"]["reason"].lower())
        self.assertEqual(self.data,before)
        self.assertTrue(all(s["strategyId"]==self.runner.CONTROL_ID for s in signals))

    def test_future_raw_changes_do_not_change_preceding_signals_or_peer_evidence(self):
        signals,_=self.collect()
        old=lines(self.root/"collected/peer-groups.jsonl.gz")
        boundary=self.target_dates[12]
        for security in self.data["securities"]:
            for bar in security["bars"]:
                if bar["date"]>boundary:
                    for key in ("open","high","low","close"):
                        bar[key]*=2
        self.data["fingerprint"]=self.runner.digest({k:v for k,v in self.data.items() if k!="fingerprint"})
        self.doc["sample"]["expectedFullSnapshotFingerprint"]=self.data["fingerprint"]
        self.build_panel()
        later,_=self.collect(self.root/"future")
        self.assertEqual([s for s in signals if s["signalDate"]<=boundary],[s for s in later if s["signalDate"]<=boundary])
        self.assertEqual([g for g in old if g["date"]<=boundary],
                         [g for g in lines(self.root/"future/peer-groups.jsonl.gz") if g["date"]<=boundary])

    def test_lifecycle_costs_tplus1_separate_active_and_all_original_slot_dispositions(self):
        before={p:self.runner.sha(p) for p in [*self.panel.iterdir(),self.workspace/"market.sqlite3",*self.source.rglob("*.day")]}
        self.assertEqual(self.run_complete(),0)
        result=self.result()
        self.assertEqual({g["strategyId"] for g in result["groups"]},set(self.runner.GROUPS))
        self.assertTrue(result["events"])
        self.assertTrue(result["overlapSuppressed"])
        self.assertTrue(all(len(g["horizons"])==7 for g in result["groups"]))
        self.assertEqual(len(result["pathRiskSummary"]["groups"]),2)
        self.assertEqual(result["slotAccounting"]["parentTop5"],70)
        ledger=lines(self.attempt()/"slot-outcomes.jsonl.gz")
        self.assertEqual(len(ledger),70)
        self.assertTrue(any(r["candidateDisposition"]["status"]=="filteredNotExpanded" for r in ledger))
        for event in result["events"]:
            if event["entry"]["status"]=="filled":
                self.assertGreater(event["entry"]["fee"],0)
                self.assertEqual(event["entry"]["date"],self.data["calendar"][self.data["calendar"].index(event["signalDate"])+1])
                hit=event["outcomes"]["nextOpenNet30"]["hitDate"]
                if hit:
                    self.assertGreater(event["outcomes"]["nextOpenNet30"]["tradingDays"],1)
        self.assertEqual(before,{p:self.runner.sha(p) for p in before})
        write(self.output/"independent-result-verification.json",{"testOnly":True})
        with patch.object(self.runner,"load_dataset",side_effect=AssertionError("success must not replay")):
            self.assertEqual(self.execute(),0)

    def test_active_can_diverge_between_nested_intentions_and_unfilled_does_not_reserve(self):
        signals,_=self.collect()
        symbol=self.symbols[0]
        sample=next(s for s in signals if s["symbol"]==symbol and s["strategyId"]==self.runner.CONTROL_ID)
        first=self.target_dates[10]
        later=self.target_dates[11]
        def intention(sid,day):
            return {**deepcopy(sample),"strategyId":sid,"signalDate":day}
        nested=[intention(self.runner.CONTROL_ID,first),intention(self.runner.CANDIDATE_ID,later),intention(self.runner.CONTROL_ID,later)]
        result=self.runner.evaluate_signals(self.data,self.doc["config"],nested)
        self.assertEqual(len(result["events"]),2)
        self.assertEqual(result["overlapSuppressed"][0]["strategyId"],self.runner.CONTROL_ID)
        data=deepcopy(self.data)
        next(s for s in data["securities"] if s["symbol"]==symbol)["tradeStatusByDate"]={self.target_dates[11]:False}
        retry=self.runner.evaluate_signals(data,self.doc["config"],[intention(self.runner.CONTROL_ID,first),intention(self.runner.CONTROL_ID,later)])
        self.assertEqual([e["entry"]["status"] for e in retry["events"]],["unfilled","filled"])

    def test_zero_parent_candidates_still_retains_every_row_and_empty_artifacts(self):
        for row in self.rows:
            row["baseEligibility"]={"eligible":False,"reason":"historicalSt","unknown":[]}
        self.rebind()
        self.run_complete()
        self.assertEqual(len(lines(self.attempt()/"predicates.jsonl.gz")),26*24)
        self.assertEqual(self.result()["events"],[])
        self.assertEqual(self.result()["slotAccounting"]["parentTop5"],0)
        self.assertEqual(lines(self.attempt()/"peer-groups.jsonl.gz"),[])
        self.assertEqual(len(lines(self.attempt()/"selection.jsonl.gz")),24)

    def test_bad_binding_failed_audit_and_extra_source_fail_before_classification(self):
        original=deepcopy(self.audit)
        for change in ({"passed":False},{"passed":1},{"failureCount":1},{"featureRows":359}):
            self.audit={**original,**change}
            self.rebind()
            with self.registered(),self.assertRaises(ValueError):
                self.runner.load_binding()
        self.audit=original
        self.rebind()
        self.doc["sourceBinding"]["files"]["manifest.json"]="0"*64
        write(self.registration,self.doc)
        self.registration_hash=self.runner.sha(self.registration)
        with self.registered(),self.assertRaisesRegex(ValueError,"Panel hash"):
            self.runner.load_binding()
        self.rebind()
        self.write_source("sh600099","leader")
        with self.registered(),self.assertRaisesRegex(ValueError,"inventory"):
            self.runner.load_binding()

    def test_full_stream_missing_extra_duplicate_and_reordered_fail(self):
        original=deepcopy(self.rows)
        for rows in (original[:-1],original+[original[-1]],original[:1]+original,
                     [original[1],original[0],*original[2:]]):
            self.rows=rows
            self.rebind()
            with self.registered(),self.assertRaises(ValueError):
                list(self.runner.iter_panel_days(self.runner.load_binding()))
        self.rows=original
        self.rebind()

    def test_failure_preserved_no_retry_and_no_unregistered_run(self):
        with self.assertRaisesRegex(ValueError,"Freeze"):
            self.execute()
        self.freeze()
        with patch.object(self.runner,"evaluate_signals",side_effect=RuntimeError("synthetic event failure")):
            with self.assertRaisesRegex(RuntimeError,"synthetic"):
                self.execute()
        self.assertTrue((self.output/"failure.json").is_file())
        self.assertTrue((self.attempt()/"failure.json").is_file())
        self.assertFalse((self.attempt()/"completion.json").exists())
        with self.assertRaisesRegex(ValueError,"restart forbidden|retry"):
            self.execute()

    def test_during_run_source_or_protocol_drift_prevents_completion(self):
        self.freeze()
        evaluate=self.runner.evaluate_signals
        def changed(*args):
            result=evaluate(*args)
            path=self.source/"vipdoc/sh/lday/sh600001.day"
            path.write_bytes(path.read_bytes()+b"drift")
            return result
        with patch.object(self.runner,"evaluate_signals",side_effect=changed):
            with self.assertRaisesRegex(ValueError,"Physical source changed"):
                self.execute()
        self.assertFalse((self.attempt()/"completion.json").exists())

    def test_protocol_archive_code_and_completed_artifacts_reject_drift(self):
        self.freeze()
        archive=self.output/"execution-sources.zip"
        original=archive.read_bytes()
        archive.write_bytes(original+b"tail")
        with self.assertRaisesRegex(ValueError,"archive drift"):
            self.execute()
        archive.write_bytes(original)
        code=self.root/"service/signal_leader_peer_expansion.py"
        content=code.read_bytes()
        code.write_bytes(content+b"\n# drift\n")
        with self.assertRaisesRegex(ValueError,"source changed"):
            self.execute()
        code.write_bytes(content)
        protocol=self.output/"protocol.json"
        original=protocol.read_bytes()
        value=json.loads(original)
        value["groups"]=list(reversed(value["groups"]))
        value["protocolHash"]=self.runner.digest({k:v for k,v in value.items() if k != "protocolHash"})
        write(protocol,value)
        with self.assertRaisesRegex(ValueError,"identity/config"):
            self.execute()
        protocol.write_bytes(original)
        self.execute()
        path=self.attempt()/"peer-groups.jsonl.gz"
        path.write_bytes(path.read_bytes()+b"corrupt")
        with self.assertRaisesRegex(ValueError,"artifact changed"):
            self.execute()

    def test_protocol_changes_during_evaluation_are_retained_as_failure(self):
        self.freeze()
        evaluate=self.runner.evaluate_signals
        def changed(*args):
            result=evaluate(*args)
            path=self.output/"protocol.json"
            value=self.runner.read_json(path)
            value["createdAt"]="changed-during-event-evaluation"
            value["protocolHash"]=self.runner.digest({k:v for k,v in value.items() if k != "protocolHash"})
            write(path,value)
            return result
        with patch.object(self.runner,"evaluate_signals",side_effect=changed):
            with self.assertRaisesRegex(ValueError,"Post-run input/protocol drift"):
                self.execute()
        self.assertTrue((self.attempt()/"failure.json").exists())
        self.assertFalse((self.attempt()/"completion.json").exists())

    def test_output_source_overlap_and_non_diagnostic_flag_are_rejected(self):
        with self.registered():
            for destination in (self.workspace/"nested",self.source/"nested",self.panel/"nested",self.root):
                with self.assertRaisesRegex(ValueError,"overlaps"):
                    self.runner.freeze(self.args(output=destination))
            with self.assertRaisesRegex(ValueError,"Explicit"):
                self.runner.freeze(self.args(diagnostic_unverified_history=False))


if __name__ == "__main__":
    unittest.main()
