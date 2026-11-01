"""Preserved-source code-level reconciliation with explicit publisher defects."""
from argparse import ArgumentParser
from bisect import bisect_left
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("_capco_preserved_v2_helpers", ROOT / "scripts/parse-capco-sector-snapshots-v2.py")
U = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(U)
read, write, ref, bound, sha, emit = (getattr(U, name) for name in ("read", "write", "ref", "bound", "sha", "emit"))
BASE = U.BASE
OUT = BASE / "published-snapshot-foundation-v3"
PLAN = OUT / "plan.json"


def verify_plan():
    plan = read(PLAN)
    for name, digest in plan["sourcePins"].items():
        bound({"path": name, "sha256": digest})
    for value in plan["bindings"]:
        bound(value)
    return plan


def register():
    if OUT.exists():
        raise ValueError("Reconciliation already registered")
    previous = U.verify_plan()
    plan = deepcopy(previous)
    plan.update(schema="published-sector-snapshot-foundation-plan-v3", registeredAt=datetime.now(timezone.utc).isoformat())
    plan["sourcePins"].update({path: sha(ROOT / path) for path in (
        "scripts/reconcile-capco-sector-snapshots-v3.py", "service/research_capco_snapshot_tables.py", "tests/test_research_capco_snapshot_tables.py")})
    plan["bindings"].append(ref(U.PLAN))
    for group in plan["groups"]:
        group["rawTableCaches"] = {}
        for order in ("stockCode", "industry"):
            for version in (2, 1):
                folder = BASE / ("published-snapshot-foundation-v" + str(version))
                source = folder / group["publicationDate"] / (order + ".tables.json.gz")
                if not source.is_file():
                    continue
                cache_plan = read(folder / "plan.json")
                old_group = next(g for g in cache_plan["groups"] if g["publicationDate"] == group["publicationDate"])
                if old_group["pdfs"][order] != group["pdfs"][order]:
                    raise ValueError("Cached geometry came from a different original PDF")
                group["rawTableCaches"][order] = ref(source)
                plan["bindings"].extend((ref(source), ref(folder / "plan.json")))
                break
    plan["rules"].update({"exactStockAndMajorAndSubclassCodesMustAgree": True, "displayNamesDoNotSelectOrRank": True,
        "clippedIndustrySectionRecovery": "Only exact same-publication unique major-code taxonomy plus compatible source-label prefix; explicit code contradiction fails",
        "rawDisplayLabelDifferencesRetained": True, "displayLabel": "longest publisher-observed name for the same exact sector code, lexical tie break; alternatives retained"})
    OUT.mkdir()
    write(PLAN, plan)
    emit({"registered": True, "snapshots": len(plan["groups"]), "plan": ref(PLAN)})


def source_tables(group, order):
    import pdfplumber
    from pypdf import PdfReader
    path = bound(group["pdfs"][order])
    reader = PdfReader(path)
    if U.clean(group["title"]) not in U.clean(reader.pages[0].extract_text()):
        raise ValueError("Original PDF title differs from dated publication")
    if order in group["rawTableCaches"]:
        pages = read(bound(group["rawTableCaches"][order]))
        emit({"rawGeometryCacheReused": path.name, "pages": len(pages)})
    else:
        pages = []
        with pdfplumber.open(path) as pdf:
            for number, page in enumerate(pdf.pages, 1):
                tables = page.extract_tables()
                if not tables:
                    raise ValueError("A publisher table page was not extracted")
                pages.append({"page": number, "tables": tables})
                page.close()
                if number % 30 == 0 or number == len(pdf.pages):
                    emit({"file": path.name, "page": number, "pages": len(pdf.pages)})
    if [p["page"] for p in pages] != list(range(1, len(reader.pages) + 1)):
        raise ValueError("Every original page must be present exactly once")
    return pages, reader


def parse_dates(dates):
    from service.research_capco_snapshot_tables import industry_assignments, reconcile_assignments, stock_assignments
    plan = verify_plan()
    for day in dates:
        group = next(g for g in plan["groups"] if g["publicationDate"] == day)
        folder = OUT / day
        folder.mkdir()
        write(folder / "start.json", {"plan": ref(PLAN), "startedAt": datetime.now(timezone.utc).isoformat()})
        stock_pages, stock_pdf = source_tables(group, "stockCode")
        stock, taxonomy = stock_assignments(stock_pages)
        text = {}
        for number, page in enumerate(stock_pdf.pages, 1):
            expected = U.stock_text_reference(page.extract_text(), number)
            if text.keys() & expected.keys():
                raise ValueError("Independent stock text repeats a source code")
            text.update(expected)
        if text != {code: row["sectorCode"] for code, row in stock.items()}:
            raise ValueError("Independent source text and primary geometry assignment differ")
        industry_pages, industry_pdf = source_tables(group, "industry")
        industry, recoveries = industry_assignments(industry_pages, taxonomy)
        members, labels = reconcile_assignments(stock, industry)
        for order, raw_pages in (("stockCode", stock_pages), ("industry", industry_pages)):
            if order not in group["rawTableCaches"]:
                write(folder / (order + ".tables.json.gz"), raw_pages)
        write(folder / "stockCode.records.json", list(stock.values()))
        write(folder / "industry.records.json", list(industry.values()))
        write(folder / "members.json", members)
        write(folder / "source-defects.json", {"codeRecoveries": recoveries, **labels})
        write(folder / "completion.json", {"passed": True, "plan": ref(PLAN), "count": len(members), "sectorCount": len({r['sectorCode'] for r in members}),
            "allOriginalStockCodeSetsAndMajorSubclassAssignmentsEqual": True, "independentEveryStockTextAgainstGeometryPassed": True,
            "rawDisplayLabelsEqual": not labels["rawLabelDifferences"], "codeRecoveries": len(recoveries), "rawLabelDifferences": len(labels["rawLabelDifferences"]),
            "pages": {"stockCode": len(stock_pdf.pages), "industry": len(industry_pdf.pages)},
            "stockRecords": ref(folder / "stockCode.records.json"), "industryRecords": ref(folder / "industry.records.json"),
            "members": ref(folder / "members.json"), "sourceDefects": ref(folder / "source-defects.json")})
        emit({"snapshotPassed": day, "members": len(members), "codeRecoveries": len(recoveries), "rawLabelDifferences": len(labels["rawLabelDifferences"])})


def finalize():
    from service.research_sector_snapshots import SectorSnapshotTimeline
    plan = verify_plan()
    data = read(bound(plan["calendarSource"]))
    calendar = data["calendar"]
    if not data.get("calendarSource") or data["calendarSource"].startswith("observed"):
        raise ValueError("Independent frozen calendar required")
    del data
    snapshots, diagnostics, previous = [], [], {}
    for group in plan["groups"]:
        proof = read(OUT / group["publicationDate"] / "completion.json")
        if not proof["passed"] or proof["plan"] != ref(PLAN):
            raise ValueError("All six code-level source audits required")
        members = read(bound(proof["members"]))
        current = {m["code"]: m["sectorCode"] for m in members}
        snapshots.append({"snapshotId": "capco:" + group["publicationDate"], "publicationDate": group["publicationDate"],
            "asOfDate": group["asOfDate"], "classificationId": "capco-major-snapshot:" + group["publicationDate"],
            "sourceRefs": [group["article"], *group["pdfs"].values()], "members": members})
        diagnostics.append({"publicationDate": group["publicationDate"], "members": len(members), "industries": len(set(current.values())),
            "added": len(current.keys() - previous.keys()), "removed": len(previous.keys() - current.keys()),
            "changedIndustry": sum(current[k] != previous[k] for k in current.keys() & previous.keys()),
            "codeRecoveries": proof["codeRecoveries"], "rawLabelDifferences": proof["rawLabelDifferences"],
            "sourceProof": ref(OUT / group["publicationDate"] / "completion.json")})
        previous = current
    timeline = SectorSnapshotTimeline(calendar, snapshots)
    publications = [s["publicationDate"] for s in snapshots]
    maps = [{m["code"]: m for m in s["members"]} for s in snapshots]
    daily = []
    for day in calendar:
        index = bisect_left(publications, day) - 1
        state = timeline.at_close(day)
        if state["membersByCode"] != (maps[index] if index >= 0 else {}):
            raise ValueError("Independent every-session publication-time member map differs")
        daily.append({k: v for k, v in state.items() if k != "membersByCode"} | {"sourceMemberCount": len(state["membersByCode"])})
    tests = subprocess.run([sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.test_research_sector_snapshots", "tests.test_capco_snapshot_parser_v2", "tests.test_research_capco_snapshot_tables"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    write(OUT / "tests.json", {"passed": tests.returncode == 0, "exitCode": tests.returncode, "stdout": tests.stdout, "stderr": tests.stderr})
    if tests.returncode:
        raise ValueError("Source and timing boundary tests failed")
    write(OUT / "snapshots.json", snapshots)
    write(OUT / "calendar.json", calendar)
    write(OUT / "daily-availability.json", daily)
    write(OUT / "validation.json", {"passed": True, "plan": ref(PLAN), "snapshots": ref(OUT / "snapshots.json"),
        "calendar": ref(OUT / "calendar.json"), "dailyAvailability": ref(OUT / "daily-availability.json"), "tests": ref(OUT / "tests.json"),
        "sourceSnapshots": diagnostics, "calendarSessionsChecked": len(calendar), "everyOriginalMemberCodeAssignmentChecked": True,
        "independentStockTextAndGeometryPassed": True, "pairedOfficialStockAndIndustrySortCodeAssignmentsPassed": True,
        "displayLabelAndSourceClippingDefectsExplicitlyRetained": True, "independentEverySessionWholeMemberReplayPassed": True,
        "publicationDayExcluded": True, "currentOfficialHistoricalArchiveUsed": True, "originalReleaseByteArchiveUnavailable": True,
        "newMarketAccounts": 0, "newStrategyTrials": 0, "completeSystemValidated": False, "targetAchieved": False})
    emit({"foundationPassed": True, "snapshots": len(snapshots), "calendarSessions": len(calendar), "validation": ref(OUT / "validation.json")})


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("command", choices=("register", "parse", "finalize"))
    parser.add_argument("--dates", nargs="+")
    args = parser.parse_args()
    if args.command == "register":
        register()
    elif args.command == "parse":
        parse_dates(args.dates or [])
    else:
        finalize()
