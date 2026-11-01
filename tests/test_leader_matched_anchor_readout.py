"""Synthetic matched populations, three-policy clocks, cash and diagnostics."""
from collections import Counter
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("matched_summary", ROOT / "scripts/summarize-leader-matched-anchor.py")
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)
KEY = dict(period="2024-2025", board="main", exit="trail10", policy="realCross")


def fixture():
    original, scheduled, result, summary = M.ACCOUNT.synthetic_fixture()
    original.append(dict(signalId="extra", strategyId="synthetic", symbol="other", signalDate="2024-01-08", originalRank=2))
    dates = ["2023-12-29", "2024-01-01", "2024-01-03", "2024-01-04", "2024-01-07"]
    opps = []
    for i, (s, day) in enumerate(zip(original, dates)):
        real, pseudo = i < 3, i in (0, 2, 4)
        opps.append(dict(formationId=s["signalId"], parentOpportunityId="old:"+s["signalId"],
            parentEventId="p", formationDate=day, peerFormationDate="2023-12-01", decisionDate=s["signalDate"],
            symbol=s["symbol"], anchorSymbol="anchor", pseudoSymbol="pseudo", frozenMemberRank=i+1, originalRank=s["originalRank"],
            commonStatus="commonKnown", flags=dict(matchUnavailable=False, initialUnknownReasons=[], initialAboveAnchors=[], decisionUnknownReasons=[]),
            crossing=dict(real=real, pseudo=pseudo), overlap="both" if real and pseudo else "realOnly" if real else "pseudoOnly" if pseudo else "neither",
            signals=dict(control=deepcopy(s), realCross=deepcopy(s) if real else None, pseudoCross=deepcopy(s) if pseudo else None)))
    for status in M.STATUSES[:-1]:
        flags = dict(matchUnavailable=status=="matchUnavailable", initialUnknownReasons=["missingA"] if status=="initialUnknown" else [],
            initialAboveAnchors=["pseudo"] if status=="initialAbove" else [],
            decisionUnknownReasons=["missingT"] if status in ("initialAbove", "decisionUnknown") else [])
        opps.append(dict(formationId=status, parentOpportunityId="old:"+status, parentEventId="q" if status=="matchUnavailable" else "p",
            formationDate="2024-01-07", peerFormationDate="2023-12-01", decisionDate="2024-01-08", symbol=status,
            anchorSymbol="anchor", pseudoSymbol=None if status=="matchUnavailable" else "pseudo", frozenMemberRank=1, originalRank=1,
            commonStatus=status, flags=flags, crossing=dict(real=None,pseudo=None), overlap=None, signals={p:None for p in M.POLICIES}))
    for o in opps:
        o["diagnostics"] = {name: None if o["commonStatus"]=="matchUnavailable" else 1. for name in (
            "prior20ScaleRatio","matchDistance","initialPseudoRealM3Ratio","initialMemberRealM3Ratio","initialMemberPseudoM3Ratio")}
    matches = []
    for eid, status in (("p","matched"),("unused","matched"),("q","anchorScaleUnknown"),("n","noEligibleCandidate"),("locked","inheritedOther")):
        matches.append(dict(parentEventId=eid,parentStatus="locked" if status=="inheritedOther" else "formed",matchStatus=status,
            pseudoSymbol="pseudo" if status=="matched" else None, distance=.1 if status=="matched" else None,
            anchorScale=dict(meanAmount20=100.),pseudoScale=dict(meanAmount20=110. if status=="matched" else None)))
    schedules={p:[o["signals"][p] for o in opps if o["signals"][p] is not None] for p in M.POLICIES}
    counts=dict(sourceSymbols=25,targetDays=7,parentFormations=5,formed=4,matched=2,anchorScaleUnknown=1,noEligibleCandidate=1,inheritedOther=1,
        originalTop5=9,opportunities=9,commonStatusCounts={s:sum(o["commonStatus"]==s for o in opps) for s in M.STATUSES},
        overlapCounts={s:sum(o["overlap"]==s for o in opps) for s in M.OVERLAPS},distinctPseudoAnchors=1,reusedPseudoAssignments=1,
        signals={p:len(v) for p,v in schedules.items()},notScheduled={p:len(opps)-len(v) for p,v in schedules.items()})
    return opps,schedules,result,summary,matches,counts


class MatchedReadoutTests(unittest.TestCase):
    def test_all_formations_include_matched_without_future_opportunity(self):
        o,s,_,_,matches,c=fixture();v=M.matching_coverage({},matches,o,c,s)
        self.assertEqual((v["parentFormations"],v["formed"],v["matched"],v["originalOpportunities"]),(5,4,2,9))
        self.assertEqual(v["matchingDistance"]["knownCount"],2)

    def test_repeated_pseudo_not_independent_formations(self):
        o,s,_,_,matches,c=fixture();v=M.matching_coverage({},matches,o,c,s)
        self.assertEqual((v["distinctPseudoSymbols"],v["reusedPseudoSymbols"],v["maxFormationsPerPseudo"]),(1,1,2))

    def test_initial_above_priority_over_decision_unknown(self):
        o,s,_,_,matches,c=fixture();v=M.matching_coverage({},matches,o,c,s)
        self.assertEqual(v["commonStatusCounts"]["initialAbove"],1)
        self.assertEqual(v["independentFlagCounts"]["decisionUnknownReasons"],2)
        self.assertEqual(M.admission_status(o[-2],False),"excluded")

    def test_wrong_priority_rejected(self):
        o,s,_,_,matches,c=fixture();o[-2]["commonStatus"]="decisionUnknown"
        with self.assertRaises(ValueError):M.matching_coverage({},matches,o,c,s)

    def test_no_second_match_after_frozen_identity(self):
        o,s,_,_,matches,c=fixture();o[0]["pseudoSymbol"]="second"
        with self.assertRaises(ValueError):M.matching_coverage({},matches,o,c,s)

    def test_four_overlap_cells_and_shared_control(self):
        o,s,*_=fixture();v=M.overlap_coverage({},o,s)
        self.assertEqual([v[k] for k in ("both","realOnly","pseudoOnly","neither","commonKnown","outsideCommon")],[2,1,1,1,5,4])

    def test_overlap_mislabel_rejected(self):
        o,s,_,_,matches,c=fixture();o[0]["overlap"]="neither"
        with self.assertRaises(ValueError):M.matching_coverage({},matches,o,c,s)

    def test_input_count_corruption_rejected(self):
        o,s,_,_,matches,c=fixture();c["signals"]["realCross"]=4
        with self.assertRaises(ValueError):M.matching_coverage({},matches,o,c,s)

    def test_unknown_ratio_retained_without_complete_case_gate(self):
        o,s,_,_,matches,c=fixture();o[0]["diagnostics"]["initialPseudoRealM3Ratio"]=None
        v=M.matching_coverage({},matches,o,c,s)
        self.assertEqual(v["opportunityDiagnostics"]["initialPseudoRealM3Ratio"]["unknownCount"],2)
        self.assertEqual(v["signals"]["realCross"],3)

    def test_empty_distribution_null_not_zero(self):
        v=M.known_distribution([None,None]);self.assertEqual(v["unknownCount"],2);self.assertIsNone(v["distribution"]["mean"])

    def test_full_original_dispositions_and_initial_failure(self):
        o,s,r,z,*_=fixture();d,a=M.join_episodes(KEY,o,s["realCross"],r,z)
        self.assertEqual((len(d),len(a)),(9,3));self.assertEqual(d[-2]["admissionStatus"],"excluded")
        self.assertEqual(d[-2]["commonStatus"],"initialAbove");self.assertTrue(d[-2]["independentFlags"]["decisionUnknownReasons"])

    def test_same_symbol_reentry_open_not_closed_return(self):
        o,s,r,z,*_=fixture();_,a=M.join_episodes(KEY,o,s["realCross"],r,z)
        self.assertEqual([x["formationId"] for x in a],["a","b","c"])
        self.assertEqual([x["closedPnlAmount"] for x in a],[100.,-100.,0.]);self.assertIsNone(a[-1]["closedReturnPct"])

    def test_original_peer_activation_decision_entry_clocks(self):
        o,s,r,z,*_=fixture();_,a=M.join_episodes(KEY,o,s["realCross"],r,z)
        self.assertEqual(tuple(a[0][k] for k in ("peerFormationDate","formationDate","decisionDate","entryDate")),
            ("2023-12-01","2023-12-29","2024-01-02","2024-01-03"))
        self.assertEqual(a[0]["parentOpportunityId"],"old:a")

    def test_coverage_counts_excluded_separately_from_unknown(self):
        o,s,r,z,*_=fixture();d,a=M.join_episodes(KEY,o,s["realCross"],r,z);v=M.account_coverage(KEY,o,s["realCross"],d,a,z)
        self.assertEqual(v["admissionCounts"],dict(included=3,excluded=3,unknown=3))
        self.assertEqual((v["notScheduled"],v["closed"],v["openAtCutoff"]),(6,2,1))

    def test_continuous_equity_and_sell_queue(self):
        o,s,r,z,*_=fixture();r["decisions"]=[dict(date="2024-01-04",action=a,status="queued",reason=a) for a in ("sell","exit")]
        v=M.interval_rows(KEY,z,r,o,s["realCross"])
        self.assertEqual(v[-1]["enteringEquity"],500100.);self.assertEqual(v[0]["queuedExitReasons"],{"sell":1})
        self.assertTrue(all(x["passed"] for x in M.ACCOUNT.partition_intervals(v,z,r["curve"])))

    def test_activation_slice_not_decision_slice(self):
        o,s,r,z,*_=fixture();v=M.interval_rows(KEY,z,r,o,s["realCross"])[0]
        self.assertEqual((v["originalFormations"],v["scheduledFromFormations"],v["decisionSignals"]),(8,2,3))

    def test_thirty_six_signed_comparisons_primary_and_secondary(self):
        values=dict(control=-5.,realCross=-2.,pseudoCross=1.)
        rows=[dict(period=p,board=b,exit=e,policy=q,metrics=dict(accountReturnPct=values[q],profitFactor=None)) for p in M.PERIODS for b in M.BOARDS for e in M.EXITS for q in M.POLICIES]
        v=M.compare_accounts(rows);self.assertEqual(len(v),36)
        primary=[r for r in v if r["role"]=="primary"];self.assertEqual(len(primary),12)
        self.assertTrue(all(r["candidateMinusControl"]==dict(accountReturnPct=-3.,profitFactor=None) for r in primary))
        with self.assertRaises(ValueError):M.compare_accounts(rows[:-1])

    def test_old_control_is_context_not_reused_account(self):
        o,s,_,z,*_=fixture();new=M.account_metrics(z,5,9)
        v=M.population_context({k:KEY[k] for k in ("period","board","exit")},z,8,9,new,{"path":"old-summary","sha256":"x"})
        self.assertFalse(v["reusedAccount"]);self.assertEqual(v["newMinusOld"]["signals"],-3)
        self.assertEqual(v["newMinusOld"]["accountReturnPct"],0.)

    def test_full_account_profit_includes_open_mark(self):
        o,s,_,z,*_=fixture();v=M.account_metrics(z,3,9)
        self.assertEqual(v["closedPnlAmount"],0.);self.assertEqual(v["openUnrealizedPnlAmount"],100.);self.assertAlmostEqual(v["accountReturnPct"],.02)

    def test_no_mutation(self):
        args=fixture();saved=deepcopy(args);o,s,r,z,ma,c=args
        M.matching_coverage({},ma,o,c,s);M.overlap_coverage({},o,s);M.join_episodes(KEY,o,s["realCross"],r,z)
        self.assertEqual(args,saved)


if __name__=="__main__":unittest.main()
