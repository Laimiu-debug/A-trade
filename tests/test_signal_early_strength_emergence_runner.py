"""Isolated raw-source and audited-panel-contract integration tests.

Only synthetic temporary files are used. Panel ranks and audit acceptance are
explicit fixture contracts, not independent rank or feature certification.
Actual raw snapshot loading, state logic, costs, paths and freeze closure run.
"""
from statistics import mean
from service.signal_leadership_features import daily_features
from argparse import Namespace
from contextlib import closing, contextmanager, redirect_stdout
from copy import deepcopy
from datetime import date, timedelta
import gzip
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "scripts/run-signal-early-strength-emergence.py"
REGISTRATION = "docs/signal-early-strength-emergence-v1.json"


def module():
    spec = importlib.util.spec_from_file_location("early_strength_emergence_runner_test", ROOT/SOURCE)
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


class EarlyStrengthEmergenceRunnerTests(unittest.TestCase):
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
        self.symbols = [f"sh60000{i}" for i in range(1,9)]+["sz000001"]
        self.target_dates = self.dates[130:170]
        for i, symbol in enumerate(self.symbols):
            self.write_source(symbol, "intraday")
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
            expectedScoredSessions=len(self.target_dates),expectedWarmupSessions=130,expectedCalendarSessions=170,
            expectedFeatureRows=len(self.symbols)*len(self.target_dates))
        self.registration = self.root/REGISTRATION
        self.registration.parent.mkdir()
        closure = set(self.doc["sourceImplementation"]) | {SOURCE, "service/signal_early_strength_emergence.py",
            "tests/test_signal_early_strength_emergence.py","tests/test_signal_early_strength_emergence_runner.py"}
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
            closing = round(previous*1.02) if i >= 110 and kind != "flat" else previous
            opening = closing if kind == "overnight" else previous
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
        self.rows=[{"symbol":symbol,"asOfDate":day,"featureVersion":"daily-leadership-raw-v1",
            "sampleManifestHash":self.manifest_hash,"calendarHash":inputs["calendar"]["sha256"],
            "sourceHashes":{**identity[symbol],"benchmarkRawBarsSha256":inputs["benchmark"]["rawBarsSha256"]},
            "baseEligibility":{"eligible":True,"reason":"eligible","unknown":["historicalSt","historicalMembership","listingDate"]}}
            for day in self.target_dates for symbol in self.symbols]
        write_lines(self.panel/"daily-coverage.jsonl.gz",[])
        write_lines(self.panel/"warmup-coverage.jsonl.gz",[])
        write(self.panel/"summary.json",{"synthetic":True})
        write(self.panel/"attempt.json",{"synthetic":True})
        self.audit={"passed":True,"failureCount":0,"sampleManifestHash":self.manifest_hash,
                    "featureRows":len(self.rows),"testOnly":True}
        self.add_features()
        self.rebind()

    def add_features(self):
        securities={s["symbol"]:s for s in self.data["securities"]}
        calendar=self.data["calendar"]
        benchmark={b["date"]:{**b,"priceBasisId":"raw-unadjusted"} for b in self.data["benchmark"]["bars"]}
        def rank_at(index):
            t=index-130
            return .4 if t<0 or t%4==0 else .95
        for row in self.rows:
            symbol,day=row["symbol"],row["asOfDate"]
            index=calendar.index(day)
            slots=calendar[index-60:index+1]
            bars={b["date"]:{**b,"priceBasisId":"raw-unadjusted"} for b in securities[symbol]["bars"]}
            features=daily_features(symbol,day,slots,[bars[d] for d in slots],[benchmark[d] for d in slots])
            identity={k:row[k] for k in ("symbol","asOfDate","featureVersion","sampleManifestHash","calendarHash","sourceHashes","baseEligibility")}
            row.update(features);row.update(identity)
            history=[rank_at(i) for i in range(index-19,index+1)]
            row.update(relativeReturn20Percentile=history[-1],relativeReturn60Percentile=.7,
                relativeReturn20CrossSectionCount=len(self.symbols),relativeReturn60CrossSectionCount=len(self.symbols),
                trailing20DailyStrengthPercentiles=history,trailing20StrengthCrossSectionCounts=[len(self.symbols)]*20,
                strengthRankSessionDates20=calendar[index-19:index+1],knownStrengthRankSessions20=20,
                availableStrengthRankSessions20=20,meanStrengthPercentile20=mean(history),minimumStrengthPercentile20=min(history),
                topQuantileSessionCount20=None)
            if symbol==self.symbols[1]: row["relativeReturn60Percentile"]=.9
            if symbol==self.symbols[2]:
                row["relativeReturn60Percentile"]=None
                row["completeness"]["benchmark60"]["complete"]=False

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
        self.prerequisites()

    def prerequisites(self):
        # Explicitly synthetic proof contracts test binding, not actual review.
        files={name:self.runner.sha(self.root/name) for name in self.runner.REVIEWED_FILES}
        scoped=self.root/self.runner.PREREQUISITES["scopedTests"]
        review=self.root/self.runner.PREREQUISITES["prerunReview"]
        scoped.parent.mkdir(parents=True,exist_ok=True)
        write(scoped,{"schema":"early-strength-emergence-scoped-tests-v1","passed":True,"testsRun":1,
            "failures":0,"errors":0,"skipped":0,"uniqueTestIds":["synthetic.fixture.contract"],
            "sourcesStableDuringTests":True,"files":files,"certified":False,"targetAchieved":False,"testOnly":True})
        write(review,{"schema":"early-strength-emergence-prerun-review-v1","passed":True,"failureCount":0,
            "scopedTestsSha256":self.runner.sha(scoped),"files":files,"certified":False,"targetAchieved":False,"testOnly":True})

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

    def test_exclusive_freeze_is_metadata_only_and_full_archive(self):
        with patch.object(self.runner,"collect_signals",side_effect=AssertionError("no predicates during freeze")),\
             patch.object(self.runner,"load_dataset",side_effect=AssertionError("no raw load during freeze")):
            protocol=self.freeze()
        self.assertFalse((self.output/"attempts").exists())
        self.assertEqual(set(protocol["prerequisites"]),{"scopedTests","prerunReview"})
        self.assertIn("tests/test_signal_early_strength_emergence_runner.py",protocol["implementation"])
        with zipfile.ZipFile(self.output/"execution-sources.zip") as z:
            self.assertEqual(set(z.namelist()),set(protocol["implementation"]))
        with self.assertRaisesRegex(ValueError,"Exclusive freeze"): self.freeze()

    def test_full_population_original_top5_before_mask_no_backfill_and_immutability(self):
        before=deepcopy(self.data)
        signals,perf=self.collect()
        self.assertEqual(self.data,before)
        self.assertEqual(perf["sourceFeatureRows"],360)
        self.assertEqual(len(lines(self.root/"collected/projections.jsonl.gz")),360)
        self.assertEqual(len(lines(self.root/"collected/predicates.jsonl.gz")),360)
        self.assertEqual(len(lines(self.root/"collected/coverage.jsonl.gz")),40)
        selections=lines(self.root/"collected/selection.jsonl.gz")
        self.assertFalse(any(r["selection"]["slots"] for r in selections[:3]))
        first=selections[3]["selection"]
        self.assertEqual([r["symbol"] for r in first["fullRanking"]],self.symbols)
        self.assertEqual([r["symbol"] for r in first["slots"]],self.symbols[:5])
        self.assertEqual([r["earlyMask"] for r in first["slots"]],["true","false","unknown","true","true"])
        candidate=[r for r in first["signals"] if r["strategyId"]==self.runner.CANDIDATE_ID]
        self.assertEqual([r["rank"] for r in candidate],[1,4,5])
        self.assertEqual(len(first["signals"]),8)
        self.assertFalse(any(s["symbol"]==self.symbols[5] for s in signals))
        self.assertTrue(all(s["formationDate"]==self.target_dates[1] for s in first["signals"]))

    def test_ranking_uses_frozen_a_amount_not_confirmation_day_amount(self):
        for row in self.rows:
            n=self.symbols.index(row["symbol"])+1
            if row["asOfDate"]==self.target_dates[1]: row["prior20MeanAmount"]=n*100_000_000.
            if row["asOfDate"]==self.target_dates[3]: row["prior20MeanAmount"]=(10-n)*1_000_000_000.
        self.rebind();self.collect()
        first=lines(self.root/"collected/selection.jsonl.gz")[3]["selection"]
        self.assertEqual([s["symbol"] for s in first["fullRanking"]],list(reversed(self.symbols)))
        self.assertEqual([s["prior20MeanAmount"] for s in first["slots"]],[900_000_000.,800_000_000.,700_000_000.,600_000_000.,500_000_000.])
        self.assertTrue(all(s["formationDate"]==self.target_dates[1] for s in first["signals"]))

    def test_rejected_episode_keeps_lock_and_suppressed_crossing_does_not_add_mask(self):
        symbol=self.symbols[0];changed_day=self.target_dates[2]
        for row in self.rows:
            if row["symbol"]!=symbol: continue
            if row["asOfDate"]==self.target_dates[1]:
                row["baseEligibility"]={"eligible":False,"reason":"historicalSt","unknown":[]}
            history=row["trailing20DailyStrengthPercentiles"]
            for i,day in enumerate(row["strengthRankSessionDates20"]):
                if day==changed_day: history[i]=.4
            row.update(relativeReturn20Percentile=history[-1],meanStrengthPercentile20=mean(history),minimumStrengthPercentile20=min(history))
        self.rebind();signals,performance=self.collect()
        formations=[r["record"] for r in lines(self.root/"collected/formations.jsonl.gz") if r["record"]["symbol"]==symbol]
        self.assertEqual(formations[0]["kind"],"formationRejected")
        self.assertEqual(formations[1]["kind"],"formationSuppressedByLock")
        self.assertEqual(formations[1]["date"],self.target_dates[3])
        self.assertIsNone(formations[1]["episode"])
        self.assertEqual(formations[2]["date"],self.target_dates[5])
        accounting=performance["episodeAccounting"]
        self.assertEqual(accounting["formations"],accounting["episodes"]+accounting["suppressedByLock"])
        self.assertEqual(sum(accounting["activatedEarlyMaskCounts"].values()),accounting["episodes"])
        self.assertFalse(any(s["symbol"]==symbol and s["signalDate"]==self.target_dates[3] for s in signals))

    def test_actual_events_costs_paths_early_mask_lineage_and_verify_only(self):
        before={p:self.runner.sha(p) for p in [*self.panel.iterdir(),self.workspace/"market.sqlite3",*self.source.rglob("*.day")]}
        self.assertEqual(self.run_complete(),0)
        result=self.result()
        self.assertEqual({g["strategyId"] for g in result["groups"]},set(self.runner.GROUPS))
        self.assertTrue(result["events"]);self.assertTrue(result["overlapSuppressed"])
        self.assertTrue(all(len(g["horizons"])==7 for g in result["groups"]))
        self.assertEqual(len(result["pathRiskSummary"]["groups"]),2)
        slots=lines(self.attempt()/"slot-outcomes.jsonl.gz")
        self.assertEqual(len(slots),result["slotAccounting"]["globalTop5Slots"])
        self.assertEqual(slots[1]["candidateDisposition"]["status"],"filteredEarlyFalse")
        self.assertEqual(slots[2]["candidateDisposition"]["status"],"filteredEarlyUnknown")
        filled=[e for e in result["events"] if e["entry"]["status"]=="filled"]
        self.assertTrue(filled)
        self.assertTrue(all(e["entry"]["fee"]>0 and e["entry"]["quantity"]>0 for e in filled))
        self.assertTrue(all(e["entry"]["date"]==self.data["calendar"][self.data["calendar"].index(e["signalDate"])+1] for e in filled))
        self.assertTrue(all(e["signal"]["earlyMask"] in ("true","false","unknown") and e["signal"]["episodeId"] for e in result["events"]))
        self.assertTrue(any(e["terminal"]["status"]=="proxyExited" for e in filled))
        self.assertEqual(before,{p:self.runner.sha(p) for p in before})
        write(self.output/"independent-result-verification.json",{"testOnly":True})
        with patch.object(self.runner,"load_dataset",side_effect=AssertionError("success cannot replay")):
            self.assertEqual(self.execute(),0)

    def test_no_signals_still_logs_full_rejected_episodes_and_cutoff(self):
        for row in self.rows: row["baseEligibility"]={"eligible":False,"reason":"historicalSt","unknown":[]}
        self.rebind();self.run_complete()
        result=self.result()
        self.assertEqual(result["events"],[])
        self.assertEqual(result["slotAccounting"]["globalTop5Slots"],0)
        self.assertEqual(len(lines(self.attempt()/"predicates.jsonl.gz")),360)
        terminal=lines(self.attempt()/"episode-terminals.jsonl.gz")
        self.assertTrue(terminal)
        self.assertTrue(all(e["state"]=="formationRejected" for e in terminal))

    def test_bad_audit_hash_inventory_and_complete_stream_fail(self):
        original=deepcopy(self.audit)
        for change in ({"passed":False},{"passed":1},{"failureCount":1},{"featureRows":359}):
            self.audit={**original,**change};self.rebind()
            with self.registered(),self.assertRaises(ValueError): self.runner.load_binding()
        self.audit=original;self.rebind()
        original_rows=deepcopy(self.rows)
        for rows in (original_rows[:-1],original_rows+[original_rows[-1]],original_rows[:1]+original_rows,
                     [original_rows[1],original_rows[0],*original_rows[2:]]):
            self.rows=rows;self.rebind()
            with self.registered(),self.assertRaises(ValueError): list(self.runner.iter_panel_days(self.runner.load_binding()))
        self.rows=original_rows;self.rebind()
        self.doc["sourceBinding"]["files"]["manifest.json"]="0"*64
        write(self.registration,self.doc);self.registration_hash=self.runner.sha(self.registration)
        with self.registered(),self.assertRaisesRegex(ValueError,"Panel hash"): self.runner.load_binding()
        self.rebind();self.write_source("sh600099","intraday")
        with self.registered(),self.assertRaisesRegex(ValueError,"inventory"): self.runner.load_binding()

    def test_raw_historical_halt_unknown_status_and_terminal_lock(self):
        security=self.data["securities"][0]
        security["tradeStatusByDate"]={self.target_dates[0]:False}
        self.data["fingerprint"]=self.runner.digest({k:v for k,v in self.data.items() if k!="fingerprint"})
        self.doc["sample"]["expectedFullSnapshotFingerprint"]=self.data["fingerprint"]
        self.build_panel();before=deepcopy(self.data)
        self.collect()
        formations=lines(self.root/"collected/formations.jsonl.gz")
        first=next(r["record"]["episode"] for r in formations if r["record"]["symbol"]==self.symbols[0])
        self.assertEqual(first["state"],"formationUnknown")
        self.assertEqual(first["lockThroughIndex"],3)
        self.assertEqual(self.data,before)
        ordinary=next(r["record"]["episode"] for r in formations if r["record"]["symbol"]==self.symbols[3])
        self.assertTrue(ordinary["formationEvidence"]["advance"]["stockQuality"]["unknownTradeStatusDates"])

    def test_a1_qualification_does_not_replace_final_t_qualification(self):
        for row in self.rows:
            if row["asOfDate"]==self.target_dates[2]: row["baseEligibility"]={"eligible":False,"reason":"historicalSt","unknown":[]}
            if row["asOfDate"]==self.target_dates[3] and row["symbol"]==self.symbols[0]:
                row["baseEligibility"]={"eligible":False,"reason":"historicalSt","unknown":[]}
        self.rebind();signals,_=self.collect()
        first=[s for s in signals if s["signalDate"]==self.target_dates[3]]
        self.assertFalse(any(s["symbol"]==self.symbols[0] for s in first))
        self.assertTrue(any(s["symbol"]==self.symbols[3] for s in first))
        confirmation=next(r["record"] for r in lines(self.root/"collected/confirmations.jsonl.gz")
            if r["date"]==self.target_dates[3] and r["record"]["symbol"]==self.symbols[0])
        self.assertFalse(confirmation["selectionEligible"])

    def test_active_separate_policies_release_and_unfilled_does_not_reserve(self):
        signals,_=self.collect();symbol=self.symbols[0]
        result=self.runner.evaluate_signals(self.data,self.doc["config"],signals)
        first=next(e for e in result["events"] if e["symbol"]==symbol and e["strategyId"]==self.runner.CONTROL_ID)
        hit=first["outcomes"]["nextOpenNet30"]["hitDate"]
        self.assertIsNotNone(hit)
        self.assertTrue(any(e["symbol"]==symbol and e["signalDate"]>=hit for e in result["events"]))
        self.assertFalse(any(e["symbol"]==self.symbols[5] for e in result["events"]))
        changed=deepcopy(self.data)
        next(s for s in changed["securities"] if s["symbol"]==symbol)["tradeStatusByDate"]={self.target_dates[4]:False}
        result=self.runner.evaluate_signals(changed,self.doc["config"],signals)
        rows=[e for e in result["events"] if e["strategyId"]==self.runner.CONTROL_ID and e["symbol"]==symbol]
        self.assertEqual(rows[0]["entry"]["status"],"unfilled")
        self.assertEqual(rows[1]["signalDate"],self.target_dates[7])
        self.assertEqual(rows[1]["entry"]["status"],"filled")
        slots,_=self.runner.slot_dispositions(lines(self.root/"collected/selection.jsonl.gz"),signals,result)
        self.assertEqual(slots[0]["baselineDisposition"]["entryStatus"],"unfilled")

    def test_failure_retained_no_retry_and_no_unfrozen_execution(self):
        with self.assertRaisesRegex(ValueError,"Freeze"): self.execute()
        self.freeze()
        with patch.object(self.runner,"evaluate_signals",side_effect=RuntimeError("synthetic failure")):
            with self.assertRaisesRegex(RuntimeError,"synthetic"): self.execute()
        self.assertTrue((self.output/"failure.json").is_file())
        self.assertTrue((self.attempt()/"failure.json").is_file())
        self.assertFalse((self.attempt()/"completion.json").exists())
        with self.assertRaisesRegex(ValueError,"restart forbidden|retry"): self.execute()

    def test_prerequisites_are_real_current_bytes_and_stay_bound(self):
        scoped=self.root/self.runner.PREREQUISITES["scopedTests"]
        good=scoped.read_bytes();proof=json.loads(good);proof["files"][SOURCE]="0"*64;write(scoped,proof)
        with self.assertRaisesRegex(ValueError,"Scoped synthetic"): self.freeze()
        scoped.write_bytes(good);self.freeze()
        proof=json.loads(good);proof["testsRun"]=2;write(scoped,proof)
        with self.assertRaisesRegex(ValueError,"Scoped synthetic"): self.execute()

    def test_archive_source_code_and_completed_artifact_drift_rejected(self):
        self.freeze();archive=self.output/"execution-sources.zip";original=archive.read_bytes()
        archive.write_bytes(original+b"tail")
        with self.assertRaisesRegex(ValueError,"archive drift"): self.execute()
        archive.write_bytes(original)
        code=self.root/"service/signal_early_strength_emergence.py";content=code.read_bytes()
        code.write_bytes(content+b"\n# drift\n")
        with self.assertRaisesRegex(ValueError,"source changed"): self.execute()
        code.write_bytes(content);self.execute()
        path=self.attempt()/"slot-outcomes.jsonl.gz";path.write_bytes(path.read_bytes()+b"corrupt")
        with self.assertRaisesRegex(ValueError,"artifact changed"): self.execute()

    def test_during_evaluation_source_drift_retains_failure(self):
        self.freeze();evaluate=self.runner.evaluate_signals
        def changed(*args):
            result=evaluate(*args);path=self.source/"vipdoc/sh/lday/sh600001.day"
            path.write_bytes(path.read_bytes()+b"drift");return result
        with patch.object(self.runner,"evaluate_signals",side_effect=changed):
            with self.assertRaisesRegex(ValueError,"Physical source inventory"): self.execute()
        self.assertFalse((self.attempt()/"completion.json").exists())

    def test_during_evaluation_protocol_drift_retains_failure(self):
        self.freeze();evaluate=self.runner.evaluate_signals
        def changed(*args):
            result=evaluate(*args);path=self.output/"protocol.json";value=self.runner.read_json(path)
            value["createdAt"]="synthetic drift";value["protocolHash"]=self.runner.digest({k:v for k,v in value.items() if k!="protocolHash"})
            write(path,value);return result
        with patch.object(self.runner,"evaluate_signals",side_effect=changed):
            with self.assertRaisesRegex(ValueError,"Post-run input/protocol drift"): self.execute()
        self.assertTrue((self.attempt()/"failure.json").exists())

    def test_output_overlap_and_missing_explicit_diagnostic_are_rejected(self):
        with self.registered():
            for destination in (self.workspace/"nested",self.source/"nested",self.panel/"nested",self.root):
                with self.assertRaisesRegex(ValueError,"overlaps"): self.runner.freeze(self.args(output=destination))
            with self.assertRaisesRegex(ValueError,"Explicit"): self.runner.freeze(self.args(diagnostic_unverified_history=False))


if __name__=="__main__": unittest.main()
