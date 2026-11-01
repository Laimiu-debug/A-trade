"""Repair only the distinction between screenshot viewport and iframe widths."""
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import struct
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "scripts/finalize-leader-persistence-admission.py"
ORIGINAL_SHA = "410e497fc9a7555a283d450da3cb4ae86f3d8f57719161b34d9d492610e56819"


def load_original():
    with ORIGINAL.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != ORIGINAL_SHA:
        raise ValueError("Frozen original finalizer changed")
    spec = importlib.util.spec_from_file_location("_frozen_persistence_finalizer", ORIGINAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OLD = load_original()


def png_dimensions(path):
    """Read the bound PNG's native pixel size without changing the image."""
    with Path(path).open("rb") as stream:
        header = stream.read(24)
    OLD.require(len(header) == 24 and header[:8] == b"\x89PNG\r\n\x1a\n" and
                header[8:16] == b"\x00\x00\x00\rIHDR", "Missing PNG IHDR dimensions")
    width, height = struct.unpack(">II", header[16:24])
    OLD.require(width > 0 and height > 0, "Invalid PNG dimensions")
    return width, height


def dimension_evidence(views, images, qa_folder):
    expected = ((780, "light"), (360, "light"), (780, "dark"))
    OLD.require(len(views) == len(images) == 3, "Three bound preview views required")
    checked = []
    for view, item, (viewport, theme) in zip(views, images, expected):
        path = OLD.bound(item)
        OLD.require(path.is_relative_to(qa_folder), "Unbound image")
        width, height = png_dimensions(path)
        OLD.require(width == viewport and view["colorScheme"] == theme, "PNG viewport/theme matrix differs")
        geometry = {key: view[key] for key in ("width", "scroll", "right")}
        OLD.require(all(type(value) in (int, float) and math.isfinite(value) for value in geometry.values()),
                    "Invalid iframe geometry")
        # The frozen QA spreads iframe geometry after viewport width. Its width
        # is therefore the iframe's innerWidth, not the page screenshot width.
        OLD.require(0 < geometry["width"] <= width and 0 < geometry["scroll"] <= geometry["width"] + 1 and
                    0 <= geometry["right"] <= geometry["width"] + 1, "Iframe overflow or invalid inner width")
        checked.append({"image": item, "viewportWidthFromPng": width, "viewportHeightFromPng": height,
                        "colorScheme": theme, "iframeGeometry": geometry})
    return checked


def archive_self():
    folder = OLD.OUT / "presentation-finalizer-repair-sources"
    folder.mkdir(exist_ok=True)
    target = folder / Path(__file__).name
    if not target.exists():
        temporary = folder / (".repair-" + uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(Path(__file__).read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    OLD.require(OLD.sha(target) == OLD.sha(__file__), "Archived v2 finalizer changed")
    return OLD.ref(target)


def repair_receipt(plan, qa_ref, checked, archived):
    return {"schema": "leader-persistence-admission-presentation-finalizer-repair-v1", "passed": True,
        "originalScriptSha256": ORIGINAL_SHA, "scriptSha256": OLD.sha(__file__),
        "originalScript": OLD.ref(ORIGINAL), "actualScript": OLD.ref(Path(__file__)), "archivedScript": archived,
        "plan": OLD.ref(OLD.OUT / "plan.json"), "qa": qa_ref,
        "renderReceipt": OLD.ref(OLD.OUT / "render-receipt.json"),
        "visualReview": OLD.ref(OLD.OUT / "visual-review.json"), "dimensions": checked,
        "originalPinnedSha256": plan["sourcePins"][ORIGINAL.relative_to(ROOT).as_posix()],
        "scope": "Only replace the viewport-width check: bound PNG IHDR verifies780/360/780 external pixels, while the original QA width/scroll/right values verify iframe geometry. All other original finalization checks are unchanged.",
        "reason": "Frozen QA writes {width,colorScheme,...geometry}; geometry.width is iframe innerWidth and replaces the outer viewport width. No QA evidence is rewritten.",
        "planModified": False, "qaModified": False, "analysisArtifactsModified": False,
        "accountExecutionsByRepair": 0, "strategyChanges": 0}


def verify_presentation(plan):
    """Original presentation checks, with only the erroneous width check replaced."""
    render = OLD.read(OLD.OUT / "render-receipt.json")
    OLD.require(render["passed"] is True and render["builderSha256"] == plan["sourcePins"][OLD.RENDERER] and
                render["deliveryOrder"] == ["sources"] and render["sourceRows"] == sum(OLD.TABLES.values()), "Renderer scope or identity differs")
    for name in ("plan.json", "input-completion.json", "input-validation.json", "comparison.json", "validation.json", "readout.json", "readout-validation.json"):
        OLD.require(render["inputPins"].get(name) == OLD.sha(OLD.OUT / name), "Missing render analysis pin: " + name)
    for name, expected in render["inputPins"].items():
        path = (OLD.OUT / name).resolve()
        OLD.require(path.is_relative_to(OLD.OUT) and OLD.sha(path) == expected, "Render input changed")
    OLD.require(OLD.bound(render["payload"]).is_relative_to(OLD.OUT) and OLD.bound(render["fragment"]).is_relative_to(OLD.OUT), "Presentation outside batch")
    payload = OLD.read(OLD.bound(render["payload"]))
    OLD.require(len(payload["items"]) == 1 and [len(q["rows"]) for q in payload["items"][0]["queries"]] == list(OLD.TABLES.values()), "Source table population differs")
    OLD.require(render["presentationHelpers"], "Missing shared renderer provenance")
    for item in render["presentationHelpers"]:
        OLD.require(OLD.sha(item["path"]) == item["sha256"], "Presentation helper changed")
    review = OLD.read(OLD.OUT / "visual-review.json")
    qa_path = OLD.bound(review["qa"])
    OLD.require(qa_path.is_relative_to(OLD.OUT / "browser-qa"), "QA outside batch")
    qa = OLD.read(qa_path)
    OLD.require(review["passed"] is True and review["actualImagesViewed"] is True and qa["passed"] is True and
                not qa["errors"] and review["images"] == qa["images"] and len(qa["images"]) == 3 and
                review["renderReceiptSha256"] == qa["renderReceiptSha256"] == OLD.sha(OLD.OUT / "render-receipt.json") and
                qa["scriptSha256"] == plan["sourcePins"][OLD.PREVIEW], "Actual bound screenshot review required")
    OLD.require([r["rows"] for r in qa["tableChecks"]] == list(OLD.TABLES.values()), "Preview matrix differs")
    checked = dimension_evidence(qa["views"], review["images"], qa_path.parent)
    for item in review["images"]:
        OLD.require(OLD.bound(item).is_relative_to(qa_path.parent), "Unbound image")
    OLD.write_once(OLD.OUT / "presentation-finalizer-repair.json",
                   repair_receipt(plan, review["qa"], checked, archive_self()))
    return render, review


def main():
    plan = OLD.read(OLD.OUT / "plan.json")
    OLD.require(plan["sourcePins"].get(ORIGINAL.relative_to(ROOT).as_posix()) == ORIGINAL_SHA,
                "Original finalizer is not the registered implementation")
    # Recovery must bind this same repair source and receipt before the original
    # sealed-delivery recovery path can publish the registry again.
    if (OLD.OUT / "delivery.json").exists():
        repair = OLD.read(OLD.OUT / "presentation-finalizer-repair.json")
        OLD.require(repair["originalScriptSha256"] == ORIGINAL_SHA and repair["scriptSha256"] == OLD.sha(__file__) and
                    repair["plan"] == OLD.ref(OLD.OUT / "plan.json"), "Existing finalizer repair differs")
        for key in ("originalScript", "actualScript", "archivedScript", "qa", "renderReceipt", "visualReview"):
            OLD.bound(repair[key])
    OLD.verify_presentation = verify_presentation
    OLD.main()


if __name__ == "__main__":
    main()
