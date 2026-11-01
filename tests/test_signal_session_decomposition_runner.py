"""Temporary raw-source integration fixtures; no actual research data is read.

The panel is a hand-authored eligibility/identity contract with a test-only audit,
not a feature calculation or certification. Raw TDX loading, matching, opening
events, path risks, input hashes and lifecycle are exercised without mocking them.
"""
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
SOURCE = "scripts/run-signal-session-decomposition.py"
REGISTRATION = "docs/signal-session-decomposition-v1.json"


def module():
    spec = importlib.util.spec_from_file_location("session_decomposition_runner_test", ROOT/SOURCE)
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


class SessionDecompositionRunnerTests(unittest.TestCase):
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
            self.write_source(symbol, "intraday" if i < 6 else "overnight")
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
        closure = set(self.doc["sourceImplementation"]) | {SOURCE, "service/signal_session_decomposition.py",
            "tests/test_signal_session_decomposition.py","tests/test_signal_session_decomposition_runner.py"}
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
        self.assertIn("tests/test_signal_session_decomposition_runner.py",protocol["implementation"])
        with zipfile.ZipFile(self.output/"execution-sources.zip") as z:
            self.assertEqual(set(z.namelist()),set(protocol["implementation"]))
        with self.assertRaisesRegex(ValueError,"Exclusive freeze"):
            self.freeze()

    def test_real_collect_full_population_rank_pairs_and_unmatched_anchors(self):
        before=deepcopy(self.data)
        signals,performance=self.collect()
        self.assertEqual(self.data,before)
        self.assertEqual(performance["classificationCounts"],{"intraday":240,"overnight":120,"ineligible":0,"unevaluable":0,"notTriggered":0})
        self.assertEqual(len(lines(self.root/"collected/predicates.jsonl.gz")),360)
        self.assertEqual(len(lines(self.root/"collected/candidates.jsonl.gz")),360)
        self.assertEqual(performance["intradayTop5"],200)
        self.assertEqual(performance["matchedPairs"],120)
        self.assertEqual(performance["unmatchedAnchors"],80)
        first=[s for s in signals if s["signalDate"] == self.target_dates[0]]
        anchors=[s for s in first if s["strategyId"] == self.runner.CANDIDATE_ID]
        self.assertEqual([s["symbol"] for s in anchors],self.symbols[:5])
        self.assertEqual(sum(s["pairedSymbol"] is None for s in anchors),2)
        self.assertFalse(any(s["symbol"] == self.symbols[5] for s in signals))

    def test_lifecycle_actual_events_costs_paths_and_complete_pair_dispositions(self):
        before={p:self.runner.sha(p) for p in [*self.panel.iterdir(),self.workspace/"market.sqlite3",*self.source.rglob("*.day")]}
        self.assertEqual(self.run_complete(),0)
        result=self.result()
        self.assertEqual(set(g["strategyId"] for g in result["groups"]),set(self.runner.GROUPS))
        self.assertTrue(result["events"])
        self.assertTrue(result["overlapSuppressed"])
        self.assertEqual(len(result["pathRiskSummary"]["groups"]),2)
        self.assertTrue(all(len(g["horizons"]) == 7 for g in result["groups"]))
        self.assertEqual(result["pairAccounting"]["anchorSlots"],200)
        pair_rows=lines(self.attempt()/"pair-outcomes.jsonl.gz")
        self.assertEqual(len(pair_rows),200)
        self.assertTrue(any(r["controlDisposition"]["status"] == "unmatched" and r["anchorDisposition"]["entryStatus"] == "filled" for r in pair_rows))
        filled=[e for e in result["events"] if e["entry"]["status"] == "filled"]
        self.assertTrue(all(e["entry"]["fee"] > 0 and e["entry"]["price"] > 0 and e["entry"]["quantity"] > 0 for e in filled))
        self.assertTrue(all(e["entry"]["date"] == self.data["calendar"][self.data["calendar"].index(e["signalDate"])+1] for e in filled))
        self.assertTrue(all(e["outcomes"]["nextOpenNet30"]["tradingDays"] is None or e["outcomes"]["nextOpenNet30"]["tradingDays"] > 1 for e in filled))
        self.assertTrue(any(e["terminal"]["status"] == "proxyExited" for e in filled))
        self.assertEqual(before,{p:self.runner.sha(p) for p in before})
        write(self.output/"independent-result-verification.json",{"testOnly":True})
        with patch.object(self.runner,"load_dataset",side_effect=AssertionError("success must not replay")):
            self.assertEqual(self.execute(),0)

    def test_all_zero_classification_still_writes_full_logs_and_empty_events(self):
        for row in self.rows:
            row["baseEligibility"]={"eligible":False,"reason":"historicalSt","unknown":[]}
        self.rebind()
        self.assertEqual(self.run_complete(),0)
        result=self.result()
        self.assertEqual(result["events"],[])
        self.assertEqual(result["pairAccounting"]["anchorSlots"],0)
        self.assertEqual(result["classificationCounts"]["ineligible"],360)
        self.assertEqual(len(lines(self.attempt()/"predicates.jsonl.gz")),360)
        self.assertEqual(len(lines(self.attempt()/"coverage.jsonl.gz")),40)

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
        self.write_source("sh600099","intraday")
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

    def test_required_missing_and_historical_halt_remain_unknown_without_mutation(self):
        # A deliberate synthetic re-registration of evidence tests raw view routing.
        security=self.data["securities"][0]
        security["tradeStatusByDate"]={self.dates[125]:False}
        self.data["fingerprint"]=self.runner.digest({k:v for k,v in self.data.items() if k != "fingerprint"})
        self.doc["sample"]["expectedFullSnapshotFingerprint"]=self.data["fingerprint"]
        self.build_panel()
        before=deepcopy(self.data)
        self.collect()
        first=lines(self.root/"collected/predicates.jsonl.gz")[0]
        self.assertEqual((first["kind"],first["reason"]),("unevaluable","historicalHaltContradictsPositiveVolume"))
        self.assertEqual(self.data,before)
        self.rows[1]["baseEligibility"]={"eligible":False,"reason":"amount20BelowMinimum","unknown":[]}
        self.rebind()
        self.collect(self.root/"collected2")
        second=lines(self.root/"collected2/predicates.jsonl.gz")[1]
        self.assertEqual((second["kind"],second["reason"]),("unevaluable","baseAmountUnknown"))

    def test_active_release_unfilled_retry_next_signal_and_no_backfill(self):
        signals,_=self.collect()
        symbol=self.symbols[0]
        config=self.doc["config"]
        result=self.runner.evaluate_signals(self.data,config,signals)
        first=next(e for e in result["events"] if e["strategyId"] == self.runner.CANDIDATE_ID and e["symbol"] == symbol)
        hit=first["outcomes"]["nextOpenNet30"]["hitDate"]
        self.assertIsNotNone(hit)
        self.assertTrue(any(e["symbol"] == symbol and e["signalDate"] == hit for e in result["events"]))
        self.assertFalse(any(e["symbol"] == self.symbols[5] for e in result["events"]))
        data=deepcopy(self.data)
        next(s for s in data["securities"] if s["symbol"] == symbol)["tradeStatusByDate"]={self.target_dates[1]:False}
        retry=self.runner.evaluate_signals(data,config,signals)
        rows=[e for e in retry["events"] if e["strategyId"] == self.runner.CANDIDATE_ID and e["symbol"] == symbol]
        self.assertEqual(rows[0]["entry"]["status"],"unfilled")
        self.assertEqual(rows[1]["signalDate"],self.target_dates[1])
        self.assertEqual(rows[1]["entry"]["status"],"filled")
        pairs,_=self.runner.pair_dispositions(lines(self.root/"collected/matches.jsonl.gz"),signals,retry)
        self.assertEqual(pairs[0]["anchorDisposition"]["entryStatus"],"unfilled")
        self.assertEqual(pairs[0]["controlDisposition"]["entryStatus"],"filled")

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
        code=self.root/"service/signal_session_decomposition.py"
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
        path=self.attempt()/"matches.jsonl.gz"
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
