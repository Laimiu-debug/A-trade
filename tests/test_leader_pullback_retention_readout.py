"""Synthetic sequence denominators, clocks, maturity and continuous equity."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pullback_summary", ROOT / "scripts/summarize-leader-pullback-retention.py")
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)
KEY = dict(period="2024-2025", board="main", exit="trail10", policy="retained")


def event(signal, formation, down, *, state="commonKnown", retained=True, missing_breach=False):
    prior = state in ("commonKnown", "memberPathUnknown")
    pd = down if prior else signal["signalDate"] if state=="sameDayDown" else None
    first = dict(status="priorDown" if prior else state, formationDate=formation, decisionDate=signal["signalDate"],
        firstDownDate=pd, unknownDate=down if state=="firstDownUnknown" else None,
        windowDates=[formation,pd or signal["signalDate"]], evaluatedPairs=[], reasons=["missingAnchor"] if state=="firstDownUnknown" else [])
    breaches = [down] if prior and (not retained or missing_breach) else []
    path = dict(status="known" if state=="commonKnown" else "unknown" if prior else "notApplicable",
        referenceDate=formation if prior else None, referenceClose=10. if prior else None, referenceReason=None,
        windowDates=[formation,down] if prior else [], observations=[dict(date=down,close=9. if breaches else 10.,reason=None,retained=not breaches,breached=bool(breaches))] if prior else [],
        unknownReasons=["memberMissing"] if state=="memberPathUnknown" else [], breachDates=breaches,
        firstBreachDate=breaches[0] if breaches else None, retained=retained if state=="commonKnown" else None)
    return dict(formationId=signal["signalId"],parentOpportunityId="old:"+signal["signalId"],parentEventId="parent",
        formationDate=formation,peerFormationDate="2023-12-01",decisionDate=signal["signalDate"],symbol=signal["symbol"],
        anchorSymbol="anchor",frozenMemberRank=1,originalRank=signal["originalRank"],firstDown=first,retention=path,
        commonStatus=state,parentAmountStatus="unknown" if signal["signalId"]=="a" else "known",
        flags=dict(firstDownUnknownReasons=first["reasons"][:],memberUnknownReasons=path["unknownReasons"][:],memberBreachDates=breaches[:]),
        diagnostics=dict(formationToDownSlots=1 if pd else None,downToDecisionSlots=1 if prior else 0 if pd else None,retentionObservationCount=len(path["observations"])),
        signals={p:deepcopy(signal) if state=="commonKnown" and (p=="control" or retained) else None for p in M.POLICIES})


def fixture():
    original,_,result,summary = M.ACCOUNT.synthetic_fixture()
    days=[("2023-12-29","2024-01-01"),("2024-01-01","2024-01-02"),("2024-01-03","2024-01-04"),("2024-01-04","2024-01-05")]
    opps=[event(sig,a,p,retained=i<3) for i,(sig,(a,p)) in enumerate(zip(original,days))]
    for state in M.STATUSES[:-1]:
        sig=dict(signalId=state,strategyId="synthetic",symbol=state,signalDate="2024-01-08",originalRank=1)
        opps.append(event(sig,"2024-01-06","2024-01-07",state=state,retained=False,missing_breach=state=="memberPathUnknown"))
    forms=[dict(parentEventId=x,status=y) for x,y in (("parent","formed"),("noOpportunity","formed"),("locked","locked"),("unformed","notFormed"))]
    schedules={p:[r["signals"][p] for r in opps if r["signals"][p] is not None] for p in M.POLICIES}
    counts=dict(sourceSymbols=21,targetDays=7,parentFormations=4,formed=2,inheritedOther=2,originalTop5=8,opportunities=8,
        commonStatusCounts={s:sum(r["commonStatus"]==s for r in opps) for s in M.STATUSES},retainedKnown=3,lostKnown=1,
        signals={p:len(v) for p,v in schedules.items()},notScheduled={p:len(opps)-len(v) for p,v in schedules.items()})
    return opps,schedules,result,summary,forms,counts


class PullbackReadoutTests(unittest.TestCase):
    def test_all_inherited_formations_even_without_opportunity(self):
        o,s,_,_,f,c=fixture();v=M.sequence_coverage({},f,o,c,s)
        self.assertEqual((v["parentFormations"],v["formed"],v["inheritedOther"],v["originalOpportunities"]),(4,2,2,8))

    def test_five_states_and_retained_lost_conservation(self):
        o,s,_,_,f,c=fixture();v=M.sequence_coverage({},f,o,c,s)
        self.assertEqual(list(v["commonStatusCounts"].values()),[1,1,1,1,4])
        self.assertEqual((v["retainedKnown"],v["lostKnown"],v["signals"]),(3,1,dict(control=4,retained=3)))

    def test_unknown_plus_known_breach_preserved(self):
        o,s,_,_,f,c=fixture();v=M.sequence_coverage({},f,o,c,s)
        self.assertEqual(v["independentEvidenceCounts"]["memberUnknownWithKnownBreach"],1)
        self.assertEqual(v["independentEvidenceCounts"]["knownBreach"],2)
        self.assertEqual(M.admission_status(o[-1],False),"unknown")

    def test_parent_amount_unknown_does_not_mask_price_gate(self):
        o,s,_,_,f,c=fixture();v=M.sequence_coverage({},f,o,c,s)
        self.assertEqual(v["parentAmountStatusCounts"]["unknown"],1)
        self.assertEqual(s["retained"][0]["signalId"],"a")

    def test_proven_P_before_T_needs_no_anchor_after_P(self):
        o,s,_,_,f,c=fixture();self.assertLess(o[0]["firstDown"]["windowDates"][-1],o[0]["decisionDate"])
        M.sequence_coverage({},f,o,c,s)

    def test_same_day_not_prior_down(self):
        o,s,_,_,f,c=fixture();o[0]["firstDown"]["firstDownDate"]=o[0]["decisionDate"]
        with self.assertRaises(ValueError):M.sequence_coverage({},f,o,c,s)

    def test_unknown_cannot_be_relabelled_common(self):
        o,s,_,_,f,c=fixture();o[-1]["commonStatus"]="commonKnown"
        with self.assertRaises(ValueError):M.sequence_coverage({},f,o,c,s)

    def test_single_breach_cannot_be_repaired_by_breakout(self):
        o,s,_,_,f,c=fixture();o[3]["retention"]["retained"]=True
        with self.assertRaises(ValueError):M.sequence_coverage({},f,o,c,s)

    def test_exact_schedule_order_and_no_refill(self):
        o,s,_,_,f,c=fixture();s["retained"].reverse()
        with self.assertRaises(ValueError):M.sequence_coverage({},f,o,c,s)

    def test_input_count_corruption(self):
        o,s,_,_,f,c=fixture();c["retainedKnown"]=4
        with self.assertRaises(ValueError):M.sequence_coverage({},f,o,c,s)

    def test_empty_distributions_unknown_not_zero(self):
        v=M.known_distribution([None,None]);self.assertEqual(v["unknownCount"],2);self.assertIsNone(v["distribution"]["mean"])

    def test_complete_original_maturity_and_reasons(self):
        o,s,r,z,*_=fixture();d,a=M.join_episodes(KEY,o,s["retained"],r,z);v=M.account_coverage(KEY,o,s["retained"],d,a,z)
        self.assertEqual((len(d),len(a),v["notScheduled"],v["closed"],v["openAtCutoff"]),(8,3,5,2,1))
        self.assertEqual(v["admissionCounts"],dict(included=3,excluded=3,unknown=2))

    def test_same_symbol_reentry_and_partial_open_not_matured(self):
        o,s,r,z,*_=fixture();_,a=M.join_episodes(KEY,o,s["retained"],r,z)
        self.assertEqual([x["formationId"] for x in a],["a","b","c"])
        self.assertEqual([x["closedPnlAmount"] for x in a],[100.,-100.,0.]);self.assertIsNone(a[-1]["closedReturnPct"])

    def test_AFPT_entry_clocks(self):
        o,s,r,z,*_=fixture();_,a=M.join_episodes(KEY,o,s["retained"],r,z);v=a[0]
        self.assertEqual((v["peerFormationDate"],v["formationDate"],v["sequenceEvidence"]["firstDown"]["firstDownDate"],v["decisionDate"],v["entryDate"]),
            ("2023-12-01","2023-12-29","2024-01-01","2024-01-02","2024-01-03"))

    def test_continuous_equity_and_queued_sell(self):
        o,s,r,z,*_=fixture();r["decisions"]=[dict(date="2024-01-04",action=a,status="queued",reason=a) for a in ("sell","exit")]
        v=M.interval_rows(KEY,z,r,o,s["retained"])
        self.assertEqual(v[-1]["enteringEquity"],500100.);self.assertEqual(v[0]["queuedExitReasons"],{"sell":1})
        self.assertTrue(all(x["passed"] for x in M.ACCOUNT.partition_intervals(v,z,r["curve"])))

    def test_A_formations_not_T_decisions(self):
        o,s,r,z,*_=fixture();v=M.interval_rows(KEY,z,r,o,s["retained"])[0]
        self.assertEqual((v["originalFormations"],v["scheduledFromFormations"],v["decisionSignals"]),(7,2,3))

    def test_all_twenty_four_accounts_twelve_signed_differences(self):
        rows=[dict(period=p,board=b,exit=e,policy=q,metrics=dict(accountReturnPct=-2. if q=="retained" else -5.,profitFactor=None)) for p in M.PERIODS for b in M.BOARDS for e in M.EXITS for q in M.POLICIES]
        v=M.compare_accounts(rows);self.assertEqual(len(v),12)
        self.assertTrue(all(r["id"]=="retainedVsControl" and r["candidateMinusControl"]==dict(accountReturnPct=3.,profitFactor=None) for r in v))
        with self.assertRaises(ValueError):M.compare_accounts(rows[:-1])

    def test_old_control_population_not_reused(self):
        o,s,_,z,*_=fixture();v=M.population_context({},z,7,8,M.account_metrics(z,4,8),dict(path="old",sha256="x"))
        self.assertFalse(v["reusedAccount"]);self.assertEqual(v["newMinusOld"]["signals"],-3)

    def test_account_return_contains_open_marks(self):
        _,_,_,z,*_=fixture();v=M.account_metrics(z,3,8)
        self.assertEqual(v["closedPnlAmount"],0.);self.assertEqual(v["openUnrealizedPnlAmount"],100.);self.assertAlmostEqual(v["accountReturnPct"],.02)

    def test_inputs_not_mutated(self):
        args=fixture();before=deepcopy(args);o,s,r,z,f,c=args
        M.sequence_coverage({},f,o,c,s);M.join_episodes(KEY,o,s["retained"],r,z);self.assertEqual(args,before)

    def test_independent_tuple_key_diagnostic_regression(self):
        spec=importlib.util.spec_from_file_location("independent_pullback",ROOT/"scripts/verify-leader-pullback-retention-readout.py")
        v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
        key=("2024-2025","main","trail10");v.same({key:{"path":"a","sha256":"x"}},{key:{"path":"a","sha256":"x"}})
        with self.assertRaises(AssertionError):v.same({key:1.},{key:2.})


if __name__=="__main__": unittest.main()
