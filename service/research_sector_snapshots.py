"""Causal use of publisher-dated complete industry snapshots.

The publication day is deliberately unavailable: article dates lack an exact
release time. A later complete snapshot replaces the earlier map in full.
This module does not infer historical publication from current classifications.
"""
from copy import deepcopy
from datetime import date
import re

VERSION = "published-sector-snapshot-timeline-v1"


def canonical_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Canonical calendar dates required")
    return value


class SectorSnapshotTimeline:
    def __init__(self, calendar, snapshots):
        if not isinstance(calendar, list) or not calendar:
            raise ValueError("Independent nonempty calendar required")
        self.calendar = [canonical_day(day) for day in calendar]
        if self.calendar != sorted(set(self.calendar)):
            raise ValueError("Calendar dates must be unique and ordered")
        if not isinstance(snapshots, list):
            raise ValueError("Explicit source snapshots required")
        self.snapshots, identifiers, publications = [], set(), set()
        for value in snapshots:
            item = deepcopy(value)
            identifier = item.get("snapshotId")
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise ValueError("Stable unique snapshot identity required")
            publication = canonical_day(item.get("publicationDate"))
            as_of = canonical_day(item.get("asOfDate"))
            if as_of > publication:
                raise ValueError("Snapshot statistics cannot postdate publication")
            if publication in publications:
                raise ValueError("Same-day competing full snapshots require explicit reconciliation")
            if not item.get("classificationId") or not isinstance(item["classificationId"], str):
                raise ValueError("Explicit snapshot classification identity required")
            if not isinstance(item.get("sourceRefs"), list) or not item["sourceRefs"]:
                raise ValueError("Bound publisher sources required")
            for source in item["sourceRefs"]:
                if not isinstance(source, dict) or not source.get("path") or not re.fullmatch(r"[a-f0-9]{64}", source.get("sha256", "")):
                    raise ValueError("Stable source paths and SHA256 required")
            if not isinstance(item.get("members"), list) or not item["members"]:
                raise ValueError("A complete explicit member map is required")
            members = {}
            for member in item["members"]:
                if not isinstance(member, dict) or set(member) != {"code", "sectorCode", "sectorName"}:
                    raise ValueError("Explicit stock code, exact sector code and source label required")
                code = member["code"]
                if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code) or code in members:
                    raise ValueError("Unique six-digit source security codes required")
                if not isinstance(member["sectorCode"], str) or not member["sectorCode"] or not isinstance(member["sectorName"], str) or not member["sectorName"]:
                    raise ValueError("Sector fields cannot be missing")
                members[code] = member
            labels = {}
            for member in members.values():
                if member["sectorCode"] in labels and labels[member["sectorCode"]] != member["sectorName"]:
                    raise ValueError("One exact sector code cannot have conflicting labels in a snapshot")
                labels[member["sectorCode"]] = member["sectorName"]
            item["membersByCode"] = members
            item["availableFrom"] = next((day for day in self.calendar if day > publication), None)
            self.snapshots.append(item)
            identifiers.add(identifier)
            publications.add(publication)
        self.snapshots.sort(key=lambda item: item["publicationDate"])

    def at_close(self, day):
        canonical_day(day)
        if day not in self.calendar:
            raise ValueError("A decision needs an independent calendar session")
        known = [item for item in self.snapshots if item["availableFrom"] is not None and item["availableFrom"] <= day]
        if not known:
            return {"date": day, "status": "unknown", "reason": "noPreviouslyPublishedSnapshot", "snapshotId": None,
                "publicationDate": None, "availableFrom": None, "classificationId": None, "membersByCode": {}}
        latest = known[-1]
        return {"date": day, "status": "known", "reason": "completePublisherSnapshotKnownBeforeSession",
            **{key: deepcopy(latest[key]) for key in ("snapshotId", "publicationDate", "availableFrom", "classificationId", "membersByCode")}}

    def member_at_close(self, day, code):
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            raise ValueError("Use exact six-digit source codes, without exchange guessing")
        state = self.at_close(day)
        member = state["membersByCode"].get(code)
        return {key: value for key, value in state.items() if key != "membersByCode"} | {
            "code": code, "membershipStatus": "known" if member is not None else "unknown",
            "membershipReason": "presentInLatestKnownCompleteSnapshot" if member is not None else "absentFromLatestKnownCompleteSnapshot" if state["status"] == "known" else state["reason"],
            "member": deepcopy(member)}
