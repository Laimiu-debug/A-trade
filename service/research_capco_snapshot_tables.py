"""Exact publisher code reconciliation, retaining clipped display labels."""
from collections import Counter
import re
import unicodedata


def clean(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def table_rows(pages, width):
    for page in pages:
        for table in page["tables"]:
            for raw in table:
                if len(raw) != width:
                    raise ValueError("Unexpected publisher layout width")
                yield page["page"], [clean(v) for v in raw]


def stock_assignments(pages):
    records, taxonomy = {}, {}
    for page, row in table_rows(pages, 8):
        if not re.fullmatch(r"[0-9]{6}", row[0]):
            if "上市公司" in "".join(row):
                continue
            raise ValueError("Unrecognized publisher stock row")
        code, section, sub, major = row[0], row[2], row[4], row[6]
        if not re.fullmatch(r"[A-S]", section) or not re.fullmatch(r"[0-9]{2}", major) or not row[7] or not row[3] or (sub and not re.fullmatch(r"C[A-Z]", sub)):
            raise ValueError("Incomplete stock-sort assignment")
        if code in records:
            raise ValueError("Duplicate stock code")
        identity = (section, sub)
        if major in taxonomy and taxonomy[major]["identity"] != identity:
            raise ValueError("Major code is not unique across publisher sections/subclasses")
        entry = taxonomy.setdefault(major, {"identity": identity, "sectionLabels": Counter(), "subclassLabels": Counter()})
        entry["sectionLabels"][row[3]] += 1
        entry["subclassLabels"][row[5]] += 1
        records[code] = {"code": code, "sectorCode": section + major, "sectorName": row[7], "subclassCode": sub}
    for entry in taxonomy.values():
        entry["fullSectionLabel"] = max(entry["sectionLabels"], key=lambda s: (len(s), s))
        entry["fullSubclassLabel"] = max(entry["subclassLabels"], key=lambda s: (len(s), s))
    return records, taxonomy


def reconcile_label(raw, expected_code, full_label, kind):
    value = clean(raw)
    match = re.search(r"\(([A-Z]{1,2})\)", value)
    if match:
        if match.group(1) != expected_code:
            raise ValueError("Explicit publisher classification contradicts major-code taxonomy")
        return False
    if not expected_code and value == "无":
        return False
    label = value.split("(", 1)[0]
    if not label or not full_label.startswith(label):
        raise ValueError("Missing code and incompatible publisher label; no fuzzy matching")
    return True


def industry_assignments(pages, taxonomy):
    records, previous, recoveries = {}, [None] * 4, []
    for page, row in table_rows(pages, 6):
        if not re.fullmatch(r"[0-9]{6}", row[4]):
            if "上市公司" in "".join(row):
                continue
            raise ValueError("Unrecognized publisher industry row")
        for i in range(4):
            if row[i]:
                previous[i] = row[i]
        major = previous[2]
        if not re.fullmatch(r"[0-9]{2}", major or "") or major not in taxonomy:
            raise ValueError("Exact independently extracted major code missing from taxonomy")
        source = taxonomy[major]
        section, sub = source["identity"]
        for kind, raw, expected, full in (("section", previous[0], section, source["fullSectionLabel"]),
                ("subclass", previous[1], sub, source["fullSubclassLabel"])):
            if reconcile_label(raw, expected, full, kind):
                recoveries.append({"page": page, "stockCode": row[4], "field": kind, "rawLabel": raw,
                    "exactMajorCode": major, "resolvedCode": expected,
                    "basis": "same-publication unique major-to-section/subclass code dictionary plus compatible source-label prefix"})
        code = row[4]
        if code in records or not previous[3]:
            raise ValueError("Duplicate stock or missing major label")
        records[code] = {"code": code, "sectorCode": section + major, "sectorName": previous[3], "subclassCode": sub}
    return records, recoveries


def reconcile_assignments(stock, industry):
    if stock.keys() != industry.keys():
        raise ValueError("Sorted source files have different complete stock-code sets")
    labels, differences = {}, []
    for code, value in stock.items():
        other = industry[code]
        if (value["sectorCode"], value["subclassCode"]) != (other["sectorCode"], other["subclassCode"]):
            raise ValueError("Original sorted source major/subclass assignments disagree")
        values = labels.setdefault(value["sectorCode"], set())
        values.update((value["sectorName"], other["sectorName"]))
        if value["sectorName"] != other["sectorName"]:
            differences.append({"stockCode": code, "sectorCode": value["sectorCode"], "stockLabel": value["sectorName"], "industryLabel": other["sectorName"]})
    display = {key: max(values, key=lambda s: (len(s), s)) for key, values in labels.items()}
    members = [{"code": code, "sectorCode": value["sectorCode"], "sectorName": display[value["sectorCode"]]} for code, value in sorted(stock.items())]
    alternatives = [{"sectorCode": key, "sourceLabels": sorted(values), "displayLabel": display[key],
        "allShorterLabelsArePrefixes": all(display[key].startswith(v) for v in values)} for key, values in sorted(labels.items()) if len(values) > 1]
    return members, {"rawLabelDifferences": differences, "displayLabelAlternatives": alternatives,
        "displayNamesDoNotChangeStockAssignment": True}
