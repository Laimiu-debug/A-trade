"""Hash-bound paired-layout industry extraction and causal calendar audit."""
from argparse import ArgumentParser
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / "artifacts/strategy-search/market-sector-stock-system-v1"
INTAKE = BASE / "capco-intake-v1"
OUT = BASE / "published-snapshot-foundation-v1"
PLAN = OUT / "plan.json"


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8-sig") if str(path).endswith(".gz") else path.open(encoding="utf-8-sig")) as handle:
        return json.load(handle)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def bound(value):
    path = (ROOT / value["path"]).resolve()
    if not path.is_relative_to(ROOT) or sha(path) != value["sha256"]:
        raise ValueError("Changed or foreign source")
    return path


def write(path, value):
    with (gzip.open(path, "xt", encoding="utf-8") if str(path).endswith(".gz") else path.open("x", encoding="utf-8")) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=None if str(path).endswith(".gz") else 2, allow_nan=False)
        handle.write("\n")


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def clean(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def verify_plan():
    plan = read(PLAN)
    for path, digest in plan["sourcePins"].items():
        bound({"path": path, "sha256": digest})
    for item in plan["bindings"]:
        bound(item)
    return plan


def register():
    if OUT.exists():
        raise ValueError("Source foundation already exists")
    manifest, receipt = read(INTAKE / "pdf-manifest.json"), read(INTAKE / "pdfs/receipt.json")
    if not receipt["allTwelveArchived"] or receipt["manifestSha256"] != sha(INTAKE / "pdf-manifest.json"):
        raise ValueError("All twelve original source attachments must be archived")
    old_plan = read(ROOT / "artifacts/strategy-search/reclaim-market-alignment-v1/plan.json")
    paths = [Path(__file__), ROOT / "service/research_sector_snapshots.py", ROOT / "tests/test_research_sector_snapshots.py"]
    source_pins = {path.relative_to(ROOT).as_posix(): sha(path) for path in paths}
    bindings = [ref(INTAKE / "pdf-manifest.json"), ref(INTAKE / "pdfs/receipt.json"),
        ref(INTAKE / "articles/receipt.json"), ref(INTAKE / "current-site.js"), old_plan["sources"]["star"]]
    groups = []
    for snapshot in manifest["snapshots"]:
        articles = [r for r in receipt["receipts"] if r["publicationDate"] == snapshot["publicationDate"]]
        if len(articles) != 2 or {r["sortOrder"] for r in articles} != {"stockCode", "industry"}:
            raise ValueError("Exactly two source layouts required per publication")
        for item in articles:
            bound(item)
        match = re.fullmatch(r"(\d{4})年(上|下)半年上市公司行业分类结果", snapshot["title"])
        if not match:
            raise ValueError("Dated original document title required")
        year, half = match.groups()
        groups.append({**snapshot, "asOfDate": year + ("-06-30" if half == "上" else "-12-31"), "pdfs": {r["sortOrder"]: {k: r[k] for k in ("path", "sha256", "url")} for r in articles}})
        bindings.extend([snapshot["article"], *[groups[-1]["pdfs"][key] for key in ("stockCode", "industry")]])
    OUT.mkdir()
    write(PLAN, {"schema": "published-sector-snapshot-foundation-plan-v1", "registeredAt": datetime.now(timezone.utc).isoformat(),
        "sourcePins": source_pins, "bindings": bindings, "groups": groups, "calendarSource": old_plan["sources"]["star"],
        "rules": {"publicationDayUnavailable": True, "completeSnapshotReplacement": True, "industryKey": "section code concatenated with exact two-digit major-class code; raw subclass retained separately",
            "noTdxIndexCrosswalk": True, "noSwHistoryAdmission": True, "snapshotNamespacedClassification": True,
            "originalReleaseByteArchiveUnavailable": True}, "newMarketAccounts": 0, "newStrategyTrials": 0})
    emit({"registered": True, "snapshots": len(groups), "plan": ref(PLAN)})


def stock_text_reference(text, page):
    starts = list(re.finditer(r"(?m)^\s*(\d{6})[ \t]+", text))
    result = {}
    for index, start in enumerate(starts):
        segment = text[start.end(): starts[index + 1].start() if index + 1 < len(starts) else len(text)]
        section = re.search(r"\s([A-S])\s", " " + segment)
        if not section:
            raise ValueError(f"Independent stock text section missing p{page}")
        majors = re.findall(r"(?<!\d)(\d{2})(?!\d)", segment[section.end():])
        if len(majors) != 1:
            raise ValueError(f"Independent stock text major ambiguous p{page}:{start.group(1)}")
        code = start.group(1)
        if code in result:
            raise ValueError("Independent page contains duplicate source code")
        result[code] = section.group(1) + majors[0]
    return result


def parse_layout(path, order, title):
    import pdfplumber
    from pypdf import PdfReader
    text_reader = PdfReader(path)
    result, raw_pages, text_codes, previous = {}, [], {}, [None] * 4
    with pdfplumber.open(path) as pdf:
        if clean(title) not in clean(text_reader.pages[0].extract_text()):
            raise ValueError("Original PDF title/date differs from publication article")
        for number, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            if not tables:
                raise ValueError(f"Table missing p{number}")
            raw_pages.append({"page": number, "tables": tables})
            if order == "stockCode":
                reference = stock_text_reference(text_reader.pages[number - 1].extract_text(), number)
                if set(reference) & set(text_codes):
                    raise ValueError("Repeated source code across independent text pages")
                text_codes.update(reference)
            for table in tables:
                for raw in table:
                    row = [clean(value) for value in raw]
                    if order == "stockCode":
                        if len(row) != 8:
                            raise ValueError(f"Unexpected stock layout column count p{number}")
                        if not re.fullmatch(r"[0-9]{6}", row[0]):
                            if "上市公司" in "".join(row):
                                continue
                            raise ValueError(f"Unrecognized stock row p{number}")
                        code, section, subclass, major, name = row[0], row[2], row[4], row[6], row[7]
                    else:
                        if len(row) != 6:
                            raise ValueError(f"Unexpected industry layout column count p{number}")
                        if not re.fullmatch(r"[0-9]{6}", row[4]):
                            if "上市公司" in "".join(row):
                                continue
                            raise ValueError(f"Unrecognized industry row p{number}")
                        for index in range(4):
                            if row[index]:
                                previous[index] = row[index]
                        section_match = re.search(r"\(([A-S])\)", previous[0] or "")
                        if not section_match:
                            raise ValueError("Industry section code not explicit in source")
                        section = section_match.group(1)
                        sub = re.search(r"\((C[A-Z])\)", previous[1] or "")
                        subclass = sub.group(1) if sub else ""
                        code, major, name = row[4], previous[2], previous[3]
                    if not re.fullmatch(r"[A-S]", section) or not re.fullmatch(r"[0-9]{2}", major or "") or not name or (subclass and not re.fullmatch(r"C[A-Z]", subclass)):
                        raise ValueError(f"Incomplete exact industry assignment p{number}")
                    if code in result:
                        raise ValueError(f"Duplicate source stock assignment {code}")
                    result[code] = {"code": code, "sectorCode": section + major, "sectorName": name, "subclassCode": subclass}
            if number % 20 == 0 or number == len(pdf.pages):
                emit({"file": path.name, "page": number, "pages": len(pdf.pages), "records": len(result)})
            page.close()
    if order == "stockCode" and text_codes != {code: row["sectorCode"] for code, row in result.items()}:
        raise ValueError("Independent text and table assignments differ")
    return result, raw_pages, len(text_reader.pages)


def parse_dates(dates):
    plan = verify_plan()
    for day in dates:
        group = next(g for g in plan["groups"] if g["publicationDate"] == day)
        folder = OUT / day
        folder.mkdir()
        write(folder / "start.json", {"plan": ref(PLAN), "startedAt": datetime.now(timezone.utc).isoformat()})
        values, pages = {}, {}
        for order in ("stockCode", "industry"):
            records, raw, page_count = parse_layout(bound(group["pdfs"][order]), order, group["title"])
            write(folder / (order + ".tables.json.gz"), raw)
            write(folder / (order + ".records.json"), list(records.values()))
            values[order], pages[order] = records, page_count
        if values["stockCode"] != values["industry"]:
            mismatch = [code for code in values["stockCode"].keys() | values["industry"].keys() if values["stockCode"].get(code) != values["industry"].get(code)]
            write(folder / "mismatch.json", {"codes": mismatch, "count": len(mismatch)})
            raise ValueError("Paired official layouts disagree; see saved mismatch")
        write(folder / "completion.json", {"passed": True, "plan": ref(PLAN), "stockTableAndIndependentTextEqual": True,
            "pairedSortLayoutsEqual": True, "count": len(values["stockCode"]), "sectorCount": len({r['sectorCode'] for r in values['stockCode'].values()}),
            "pages": pages, "records": ref(folder / "stockCode.records.json"), "comparison": ref(folder / "industry.records.json")})
        emit({"snapshotPassed": day, "members": len(values["stockCode"]), "pages": pages})


def finalize():
    from service.research_sector_snapshots import SectorSnapshotTimeline
    plan = verify_plan()
    data = read(bound(plan["calendarSource"]))
    calendar = data["calendar"]
    if data.get("calendarSource", "").startswith("observed"):
        raise ValueError("Independent frozen calendar required")
    del data
    snapshots, diagnostics = [], []
    previous = {}
    for group in plan["groups"]:
        folder = OUT / group["publicationDate"]
        proof = read(folder / "completion.json")
        if not proof["passed"] or proof["plan"] != ref(PLAN):
            raise ValueError("Per-snapshot original-source audit required")
        members = [{k: v for k, v in row.items() if k != "subclassCode"} for row in read(bound(proof["records"]))]
        current = {row["code"]: row["sectorCode"] for row in members}
        snapshot = {"snapshotId": "capco:" + group["publicationDate"], "publicationDate": group["publicationDate"], "asOfDate": group["asOfDate"],
            "classificationId": "capco-major-snapshot:" + group["publicationDate"], "sourceRefs": [group["article"], *group["pdfs"].values()], "members": members}
        snapshots.append(snapshot)
        diagnostics.append({"publicationDate": group["publicationDate"], "members": len(members), "industries": len(set(current.values())),
            "added": len(current.keys() - previous.keys()), "removed": len(previous.keys() - current.keys()),
            "changedIndustry": sum(current[k] != previous[k] for k in current.keys() & previous.keys()), "proof": ref(folder / "completion.json")})
        previous = current
    timeline = SectorSnapshotTimeline(calendar, snapshots)
    publications = [s["publicationDate"] for s in snapshots]
    expected_maps = [{r["code"]: r for r in s["members"]} for s in snapshots]
    daily = []
    for day in calendar:
        index = bisect_left(publications, day) - 1
        state = timeline.at_close(day)
        if state["membersByCode"] != (expected_maps[index] if index >= 0 else {}):
            raise ValueError("Independent every-session publication-order replay differs")
        daily.append({k: v for k, v in state.items() if k != "membersByCode"} | {"sourceMemberCount": len(state["membersByCode"])})
    tests = subprocess.run([sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.test_research_sector_snapshots"], cwd=ROOT, text=True, capture_output=True, encoding="utf-8")
    write(OUT / "tests.json", {"exitCode": tests.returncode, "stdout": tests.stdout, "stderr": tests.stderr, "passed": tests.returncode == 0})
    if tests.returncode:
        raise ValueError("Causal snapshot boundary tests failed")
    write(OUT / "snapshots.json", snapshots)
    write(OUT / "calendar.json", calendar)
    write(OUT / "daily-availability.json", daily)
    write(OUT / "validation.json", {"passed": True, "plan": ref(PLAN), "snapshots": ref(OUT / "snapshots.json"), "calendar": ref(OUT / "calendar.json"),
        "daily": ref(OUT / "daily-availability.json"), "tests": ref(OUT / "tests.json"), "sourceSnapshots": diagnostics,
        "calendarSessionsChecked": len(calendar), "independentEverySessionWholeMemberMapReplay": True,
        "primaryPairedSortAndIndependentTextChecks": True, "publicationDayExcluded": True,
        "originalReleaseByteArchiveUnavailable": True, "currentOfficialHistoricalArchiveUsed": True,
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
