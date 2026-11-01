from copy import deepcopy
import unittest

from service.research_sector_snapshots import SectorSnapshotTimeline


CALENDAR = ["2024-09-24", "2024-09-25", "2024-09-26", "2024-09-27", "2024-09-30", "2024-10-08", "2024-10-09"]


def snapshot(publication="2024-09-23", identifier="old", members=None):
    return {"snapshotId": identifier, "publicationDate": publication, "asOfDate": "2024-06-30",
        "classificationId": "capco-major-snapshot:" + identifier, "sourceRefs": [{"path": "source.pdf", "sha256": "a" * 64}],
        "members": members or [{"code": "000001", "sectorCode": "J66", "sectorName": "货币金融服务"}]}


class SnapshotTimeTests(unittest.TestCase):
    def test_publication_date_is_not_a_decision_input(self):
        old, new = snapshot(), snapshot("2024-09-30", "new", [{"code": "000001", "sectorCode": "J69", "sectorName": "其他金融业"}])
        timeline = SectorSnapshotTimeline(CALENDAR, [new, old])
        self.assertEqual(timeline.member_at_close("2024-09-30", "000001")["member"]["sectorCode"], "J66")
        self.assertEqual(timeline.member_at_close("2024-10-08", "000001")["member"]["sectorCode"], "J69")
        self.assertEqual(timeline.at_close("2024-10-08")["availableFrom"], "2024-10-08")

    def test_weekend_release_waits_for_actual_calendar(self):
        state = SectorSnapshotTimeline(CALENDAR, [snapshot("2024-09-28")])
        self.assertEqual(state.at_close("2024-09-27")["status"], "unknown")
        self.assertEqual(state.at_close("2024-09-30")["status"], "known")

    def test_current_snapshot_never_backfills_past(self):
        timeline = SectorSnapshotTimeline(CALENDAR, [snapshot("2026-09-30", "current")])
        self.assertIsNone(timeline.snapshots[0]["availableFrom"])
        self.assertEqual(timeline.member_at_close("2024-09-24", "000001")["membershipStatus"], "unknown")

    def test_complete_snapshot_replaces_not_merges_missing_members(self):
        old, new = snapshot(), snapshot("2024-09-30", "new", [{"code": "000002", "sectorCode": "K70", "sectorName": "房地产业"}])
        timeline = SectorSnapshotTimeline(CALENDAR, [old, new])
        result = timeline.member_at_close("2024-10-08", "000001")
        self.assertEqual(result["membershipStatus"], "unknown")
        self.assertEqual(result["membershipReason"], "absentFromLatestKnownCompleteSnapshot")
        self.assertIsNone(result["member"])

    def test_future_poison_cannot_change_earlier_states(self):
        old = snapshot()
        poison = snapshot("2024-10-08", "future", [{"code": "000001", "sectorCode": "F51", "sectorName": "批发业"}])
        before = SectorSnapshotTimeline(CALENDAR, [old]).at_close("2024-09-27")
        after = SectorSnapshotTimeline(CALENDAR, [old, poison]).at_close("2024-09-27")
        self.assertEqual(before, after)

    def test_source_and_return_values_do_not_mutate_saved_state(self):
        source = [snapshot()]
        frozen = deepcopy(source)
        timeline = SectorSnapshotTimeline(CALENDAR, source)
        value = timeline.at_close("2024-09-24")
        value["membersByCode"]["000001"]["sectorCode"] = "fake"
        self.assertEqual(source, frozen)
        self.assertEqual(timeline.member_at_close("2024-09-24", "000001")["member"]["sectorCode"], "J66")

    def test_duplicate_or_conflicting_sources_fail_explicitly(self):
        for values in ([snapshot(), snapshot(identifier="different")], [snapshot(), snapshot("2024-09-26")]):
            with self.assertRaises(ValueError):
                SectorSnapshotTimeline(CALENDAR, values)
        duplicate = snapshot()
        duplicate["members"].append(deepcopy(duplicate["members"][0]))
        with self.assertRaises(ValueError):
            SectorSnapshotTimeline(CALENDAR, [duplicate])

    def test_invalid_dates_order_and_unbound_sources_rejected(self):
        with self.assertRaises(ValueError):
            SectorSnapshotTimeline(CALENDAR[::-1], [snapshot()])
        invalid = snapshot()
        invalid["sourceRefs"][0]["sha256"] = "unknown"
        with self.assertRaises(ValueError):
            SectorSnapshotTimeline(CALENDAR, [invalid])
        invalid = snapshot()
        invalid["asOfDate"] = "2024-09-30"
        with self.assertRaises(ValueError):
            SectorSnapshotTimeline(CALENDAR, [invalid])
        with self.assertRaises(ValueError):
            SectorSnapshotTimeline(CALENDAR, [snapshot()]).at_close("2024-10-01")


if __name__ == "__main__":
    unittest.main()
