"""Repair Sources query identifiers only; preserve frozen research and first outputs."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/leader-incremental-prediction-v1"
ORIGINAL = ROOT / "scripts/render-leader-incremental-prediction.py"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def relative_ref(path):
    return {"path": path.relative_to(OUT).as_posix(), "sha256": sha(path)}


def main():
    plan = read(OUT / "plan.json")
    for key in ("sourcePins", "inputPins"):
        for name, expected in plan[key].items():
            assert sha(ROOT / name) == expected, name
    assert not (OUT / "render-receipt.json").exists()
    spec = importlib.util.spec_from_file_location("frozen_renderer", ORIGINAL)
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    expected = renderer.payloads(read(OUT / "readout.json"))
    original = read(OUT / "leader-prediction-sources.json")
    assert original == expected["comparisonSources"]
    assert read(OUT / "leader-prediction-chart.json") == expected["comparisonChart"]
    repaired = deepcopy(original)
    mapping = {"primaryRows": "primary-rows", "yearRows": "year-rows",
               "halfYearRows": "half-year-rows", "phaseRows": "phase-rows", "epochRows": "epoch-rows"}
    for query in repaired["items"][0]["queries"]:
        query["id"] = mapping.get(query["id"], query["id"])
    restored = deepcopy(repaired)
    reverse = {v: k for k, v in mapping.items()}
    for query in restored["items"][0]["queries"]:
        query["id"] = reverse.get(query["id"], query["id"])
    assert restored == original
    names = ("plan.json", "completion.json", "validation.json", "readout.json", "readout-validation.json")
    pins = {name: sha(OUT / name) for name in names}
    proof = read(OUT / "readout-validation.json")
    assert proof["passed"] and proof["readoutSha256"] == pins["readout.json"]
    attempt = OUT / "presentation-attempt-0001.json"
    write(attempt, {"passed": False, "recordedAt": datetime.now(timezone.utc).isoformat(),
                   "builderSha256": sha(ORIGINAL), "exitCode": 1,
                   "error": "Invalid inline sources: query.id must be a lowercase hyphenated identifier.",
                   "chartRendered": True, "sourcesRendered": False,
                   "originalPayload": relative_ref(OUT / "leader-prediction-sources.json"),
                   "scope": "Presentation schema failure; no research calculation changed."})
    payload = OUT / "leader-prediction-sources-v2.json"
    fragment = OUT / "inline/leader-prediction-sources.html"
    assert not fragment.exists()
    write(payload, repaired)
    result = subprocess.run([str(renderer.NODE), str(renderer.HELPERS["comparisonSources"]),
                             "--input", str(payload), "--output", str(fragment)],
                            capture_output=True, text=True, encoding="utf-8")
    write(OUT / "presentation-attempt-0002.json", {"passed": result.returncode == 0,
          "repairSha256": sha(__file__), "exitCode": result.returncode,
          "stdout": result.stdout, "stderr": result.stderr, "identifierMapping": mapping})
    assert result.returncode == 0, result.stderr
    assert fragment.stat().st_size < 1_000_000
    assert all(sha(OUT / name) == value for name, value in pins.items())
    for key in ("sourcePins", "inputPins"):
        for name, value in plan[key].items():
            assert sha(ROOT / name) == value
    archived = OUT / "presentation-repair-source.py"
    with archived.open("xb") as stream:
        stream.write(Path(__file__).read_bytes())
    repair = OUT / "presentation-repair.json"
    write(repair, {"passed": True, "createdAt": datetime.now(timezone.utc).isoformat(),
          "script": {"path": Path(__file__).relative_to(ROOT).as_posix(), "sha256": sha(__file__)},
          "archivedScript": relative_ref(archived), "firstFailure": relative_ref(attempt),
          "identifierMapping": mapping, "onlyFiveQueryIdsChanged": True,
          "allNumericAndDefinitionFieldsUnchanged": True, "originalPayload": relative_ref(OUT / "leader-prediction-sources.json"),
          "repairedPayload": relative_ref(payload), "researchReruns": 0, "newAccounts": 0})
    receipt = {"passed": True, "createdAt": datetime.now(timezone.utc).isoformat(),
        "builderSha256": sha(ORIGINAL), "presentationRepair": relative_ref(repair),
        "builderMeaning": "Frozen original payload builder; chart succeeded on first attempt. Sources completed by separately recorded identifier-only repair.",
        "inputPins": pins,
        "presentationHelpers": [{"path": str(p), "sha256": sha(p)} for p in renderer.HELPERS.values()],
        "payloads": {"comparisonChart": relative_ref(OUT / "leader-prediction-chart.json"), "comparisonSources": relative_ref(payload)},
        "fragments": {"comparisonChart": relative_ref(OUT / "inline/leader-prediction-chart.html"), "comparisonSources": relative_ref(fragment)},
        "deliveryOrder": ["comparisonChart", "comparisonSources"], "sourceRows": 102,
        "meaning": "Prediction error diagnostic only; no portfolio return."}
    write(OUT / "render-receipt.json", receipt)
    print(json.dumps({"passed": True, "renderReceiptSha256": sha(OUT / "render-receipt.json"), "researchReruns": 0}))


if __name__ == "__main__":
    main()
