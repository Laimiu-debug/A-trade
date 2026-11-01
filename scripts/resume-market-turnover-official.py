"""Resume archived public observations slowly, stopping at first source failure.

No parallel requests, hostname substitution, authentication or challenge bypass.
Existing successful responses remain immutable. Partial completion is explicit.
"""
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_turnover_archive", ROOT / "scripts/fetch-market-turnover-official.py")
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)


def main():
    with gzip.open(fetcher.SOURCE, "rt", encoding="utf-8") as f:
        days = [d for d in json.load(f)["calendar"] if "2023-11-20" <= d <= "2025-12-31"]
    rows = []
    started = datetime.now(timezone.utc).isoformat()
    for i, day in enumerate(days, 1):
        cached = all((fetcher.OUT / "raw" / market / (day + ".json")).exists() for market in ("sh", "sz"))
        if not cached:
            time.sleep(1.8)
        row = fetcher.fetch_day(day)
        rows.append(row)
        if "failedExchange" in row:
            receipt = {"passed": False, "startedAt": started, "stoppedAt": datetime.now(timezone.utc).isoformat(),
                       "failedObservation": row, "completedDatesBeforeStop": len(rows)-1,
                       "newFetcherSha256": fetcher.sha(Path(__file__)), "strategyAccountsRun": 0,
                       "policy": "Stop immediately rather than continue a failing batch; do not bypass source controls."}
            target = fetcher.OUT / ("slow-resume-stopped-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".json")
            fetcher.save(target, receipt)
            print(json.dumps(receipt), flush=True)
            return
        if not cached or i % 75 == 0:
            print(json.dumps({"completedDates": i, "totalDates": len(days), "lastDate": day, "cacheHit": cached}), flush=True)
    receipt = {"schema": "official-sh-sz-a-share-turnover-v1", "passed": True, "failures": [], "expectedDates": len(days), "rows": rows,
               "scope": "SSE main A+STAR; SZSE main A+ChiNext A; excludes B/funds/bonds/Beijing", "outputUnit": "CNY",
               "sourceUnit": "100 million CNY rounded to0.01 units", "pointInTimeArchive": False, "publicationTimeVerified": False,
               "availabilityAssumption": "EOD T observations available for T+1 opening decisions; historical publication timestamps not archived",
               "startedAt": started, "completedAt": datetime.now(timezone.utc).isoformat(),
               "builderSha256": fetcher.sha(ROOT / "scripts/fetch-market-turnover-official.py"),
               "resumeBuilderSha256": fetcher.sha(Path(__file__)), "maxConcurrentRequests": 1, "delayBeforeUncachedDateSeconds": 1.8}
    fetcher.save(fetcher.OUT / "completion.json", receipt)
    print(json.dumps({"passed": True, "rows": len(rows), "receipt": str(fetcher.OUT / 'completion.json')}), flush=True)


if __name__ == "__main__":
    main()
