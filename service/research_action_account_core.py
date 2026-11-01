"""Explicit action seams for a separate synthetic research account version.

No legacy module is patched. The companion day function is a controlled static
copy of the original day's order, valuation and target flow, with four action
seams replaced. Market use is intentionally unavailable until full source and
feature contracts have been implemented, rather than treating two notices as a
complete ledger for a historical universe.
"""
from collections import Counter
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
import hashlib

from .portfolio_engine import PortfolioEngine, _digest
from .research_fixed_signal_account import FixedSignalAccount
from .research_action_basis import (
    normalize_distribution, reference_change, distribution_entitlements,
    grant_lots, rebase_risk_and_mark, DistributionCashBook,
)
from .research_action_account_day import action_step_day

CORE_VERSION = "research-action-account-synthetic-v1"


class ActionAccountMixin:
    def __init__(self, dataset, signals, config, *, entry_weight, fee_policy, state=None):
        if state is not None:
            raise ValueError("Action-aware checkpoint restore is unsupported")
        if (dataset.get("calendarSource") != "synthetic-independent" or
                dataset.get("researchActionCoverage") != {"scope": "synthetic-fixture", "complete": True}):
            raise ValueError("This engineering version accepts explicit synthetic fixtures only")
        if dataset.get("corporateActions") or any(s.get("corporateActions") for s in dataset["securities"]):
            raise ValueError("Legacy corporateActions cannot be mixed with researchDistributions")
        source = deepcopy(dataset)
        policy = source.get("researchActionPolicy")
        if (not isinstance(policy, dict) or set(policy) != {"fractionPolicy", "taxReserveRate", "cashCredit"} or
                policy["fractionPolicy"] not in ("floor", "ceil", "exact") or policy["cashCredit"] != "afterPayDateOpen"):
            raise ValueError("Explicit fraction, tax-reserve and cash-timing scenario required")
        # Validate the tax rate even if an empty ledger contains no dividends.
        try:
            rate = D(policy["taxReserveRate"])
        except Exception as exc:
            raise ValueError("Explicit decimal tax reserve required") from exc
        if type(policy["taxReserveRate"]) is not str or not rate.is_finite() or not 0 <= rate <= 1:
            raise ValueError("Explicit decimal tax reserve in [0,1] required")
        events = source.get("researchDistributions")
        if not isinstance(events, list):
            raise ValueError("An explicit event ledger including empty is required")
        self.distributions = [normalize_distribution(row) for row in events]
        self.action_policy = deepcopy(policy)
        self.action_book = DistributionCashBook()
        self.record_snapshots = {}
        self.reference_changes = {}
        self.applied_action_ids = set()
        self.actions_by_ex = {}
        self.actions_by_record = {}
        securities = {s["symbol"]: s for s in source["securities"]}
        identities, keys = set(), set()
        for event in self.distributions:
            if (event["id"] in identities or (event["symbol"], event["exDate"]) in keys or
                    event["symbol"] not in securities):
                raise ValueError("Unique identified per-symbol distributions required")
            identities.add(event["id"])
            keys.add((event["symbol"], event["exDate"]))
            calendar = source["calendar"]
            if (event["recordDate"] not in calendar or event["exDate"] not in calendar or
                    calendar.index(event["exDate"]) != calendar.index(event["recordDate"]) + 1):
                raise ValueError("Record-close to next-session ex timing required by this version")
            security = securities[event["symbol"]]
            prior = [b for b in security["bars"] if b["date"] < event["exDate"]]
            if not prior or prior[-1]["date"] != event["recordDate"]:
                raise ValueError("A record-close raw price is required for reference derivation")
            change = reference_change(event, str(prior[-1]["close"]))
            self.reference_changes[event["id"]] = change
            mapping = security.setdefault("precloseByDate", {})
            old_reference = mapping.get(event["exDate"])
            if old_reference is not None and abs(D(str(old_reference)) - D(change["exReference"])) > D("0.005"):
                raise ValueError("Published action conflicts with supplied preclose")
            mapping[event["exDate"]] = float(change["exReference"])
            self.actions_by_ex.setdefault(event["exDate"], []).append(event)
            self.actions_by_record.setdefault(event["recordDate"], []).append(event)
        self.action_definition = _digest({"events": self.distributions, "policy": self.action_policy,
                                          "references": self.reference_changes})
        super().__init__(source, signals, config, entry_weight=entry_weight, fee_policy=fee_policy)
        names = ("research_action_basis.py", "research_action_account_core.py",
                 "research_action_account_day.py", "research_action_account.py", "research_volatility_trail.py")
        self.action_source_pins = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in names}

    def _identity_stable(self):
        super()._identity_stable()
        if self.action_definition != _digest({"events": self.distributions, "policy": self.action_policy,
                                              "references": self.reference_changes}):
            raise ValueError("Action definition changed after account construction")
        for name, digest in self.action_source_pins.items():
            if hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != digest:
                raise ValueError("Action source changed after account construction")

    def _transaction_snapshot(self):
        return (super()._transaction_snapshot(), self.action_book.checkpoint(),
                deepcopy(self.record_snapshots), set(self.applied_action_ids))

    def _transaction_restore(self, snapshot):
        base, book, records, applied = snapshot
        super()._transaction_restore(base)
        self.action_book.restore(book)
        self.record_snapshots, self.applied_action_ids = records, applied

    def _execute_order(self, day, symbol, side, requested, pending):
        start = len(self.state["trades"])
        super()._execute_order(day, symbol, side, requested, pending)
        fills = self.state["trades"][start:]
        if fills and side == "buy":
            if len(fills) != 1 or fills[0]["positionAction"] != "open":
                raise ValueError("One initial entry fill required")
            position = self.state["positions"][symbol]
            position["researchEpisodeId"] = fills[0]["fillId"]
            if len(position["lots"]) != 1:
                raise ValueError("Unexpected initial lots")
            position["lots"][0]["researchLotId"] = fills[0]["fillId"]

    def _capture_record_close(self, day):
        for event in self.actions_by_record.get(day, []):
            if event["id"] in self.record_snapshots:
                raise ValueError("Record close already captured")
            position = self.state["positions"].get(event["symbol"])
            lots = [] if position is None else [{"lotId": row["researchLotId"],
                "episodeId": position["researchEpisodeId"], "acquiredDate": row["date"],
                "quantity": row["quantity"], "cost": str(row["cost"])} for row in position["lots"]]
            self.record_snapshots[event["id"]] = distribution_entitlements(event, lots, record_as_of=day,
                fraction_policy=self.action_policy["fractionPolicy"], tax_reserve_rate=self.action_policy["taxReserveRate"])

    def _apply_before_open(self, day):
        credit = self.action_book.advance_open(day)
        self.state["cash"] += float(D(credit["spendableCashDelta"]))
        if credit["claimIds"]:
            self.state["actionLedger"].append({"stage": "researchCashCredit", **credit})
        for event in self.actions_by_ex.get(day, []):
            if event["id"] in self.applied_action_ids:
                raise ValueError("Ex event already applied")
            entitlement = self.record_snapshots.get(event["id"])
            if entitlement is None:
                if event["recordDate"] >= self.dates[0]:
                    raise ValueError("Missing record-date ownership snapshot")
                # A cash-start account beginning after registration owned none.
                entitlement = distribution_entitlements(event, [], record_as_of=event["recordDate"],
                    fraction_policy=self.action_policy["fractionPolicy"], tax_reserve_rate=self.action_policy["taxReserveRate"])
            before = self.action_book.summary()["incomeByOriginalEpisode"]
            self.action_book.accrue(entitlement, day=day)
            after = self.action_book.summary()["incomeByOriginalEpisode"]
            position = self.state["positions"].get(event["symbol"])
            if entitlement["lots"]:
                if position is None:
                    raise ValueError("Entitled original position missing before ex opening")
                episode = position["researchEpisodeId"]
                if {row["episodeId"] for row in entitlement["lots"]} != {episode}:
                    raise ValueError("Entitlements do not belong to current episode")
                old_lots = position["lots"]
                pure_lots = [{"lotId": row["researchLotId"], "episodeId": episode,
                    "acquiredDate": row["date"], "quantity": row["quantity"], "cost": str(row["cost"]),
                    **({"unlockDate": row["unlockDate"]} if "unlockDate" in row else {}),
                    **({"eventId": row["researchEventId"]} if "researchEventId" in row else {})} for row in old_lots]
                new_lots = grant_lots(entitlement, pure_lots, day=day)
                position["lots"] = [{"date": row["acquiredDate"], "quantity": row["quantity"], "cost": float(D(row["cost"])),
                    "researchLotId": row["lotId"], **({"unlockDate": row["unlockDate"]} if "unlockDate" in row else {}),
                    **({"researchEventId": row["eventId"]} if "eventId" in row else {})} for row in new_lots]
                position["quantity"] = sum(row["quantity"] for row in position["lots"])
                # Total investment/cost is retained.  Only its per-lot share
                # allocation changes; no transfer is booked as a cash purchase.
                if abs(sum(row["cost"] for row in position["lots"]) - position["costBasis"]) > 1e-7:
                    raise ValueError("Share distribution changed total position cost")
                position["realizedPnl"] += float(D(after.get(episode, "0")) - D(before.get(episode, "0")))
                converted = rebase_risk_and_mark({"riskEntryPrice": str(position["riskEntryPrice"]),
                    "closePeak": str(position["closePeak"]), "lastPrice": str(self.state["lastPrices"][event["symbol"]]),
                    "lastPriceDate": self.state["lastPriceDates"][event["symbol"]],
                    "appliedReferenceEvents": position.get("appliedReferenceEvents", [])},
                    self.reference_changes[event["id"]], ex_date=day)
                for key in ("riskEntryPrice", "closePeak"):
                    position[key] = float(D(converted[key]))
                position["appliedReferenceEvents"] = converted["appliedReferenceEvents"]
                self.state["lastPrices"][event["symbol"]] = float(D(converted["lastPrice"]))
            elif position is not None:
                raise ValueError("Held position missing from record snapshot")
            self.applied_action_ids.add(event["id"])
            self.state["processedActions"].append(event["id"])
            self.state["actionLedger"].append({"date": day, "stage": "researchEx", "eventId": event["id"],
                "symbol": event["symbol"], "allocatedGrant": entitlement["allocatedGrant"],
                "cashBook": self.action_book.summary(), "reference": deepcopy(self.reference_changes[event["id"]])})

    def _observe_actions(self, day):
        # Controlled equivalent of the legacy observation seam: declared
        # research references are already applied, every other discontinuity
        # retains the old unaccounted-exposure behavior.
        for symbol in sorted(self.securities):
            bar, index = self._bar(symbol, day)
            if bar is None or not index:
                continue
            reference = self.securities[symbol].get("precloseByDate", {}).get(day)
            previous = self.bars[symbol][index - 1]["close"]
            if reference is None or abs(reference - previous) <= max(.005, previous * 1e-7):
                continue
            observation = {"symbol": symbol, "date": day, "rawPreviousClose": previous, "preclose": reference}
            self.state["corporateActionDates"].append(observation)
            if any(e["symbol"] == symbol and e["id"] in self.applied_action_ids for e in self.actions_by_ex.get(day, [])):
                continue
            if symbol in self.state["positions"]:
                self.state["unaccountedActionExposure"].append({**observation, "quantity": self.state["positions"][symbol]["quantity"]})
                self._decision(day, symbol, "corporateAction", "unaccounted", "referenceChangedWithoutRightsLedger", rawPreviousClose=previous, preclose=reference)
                position = self.state["positions"][symbol]
                position["riskEntryPrice"] *= reference / previous
                position["closePeak"] *= reference / previous
                self._warning(f"{symbol} {day} 存在未解释的参考价变化；该账户不能用于经济收益结论。")

    def _action_asset_adjustment(self):
        return float(D(self.action_book.summary()["netAssetAdjustment"]))

    def _continuity_reference(self, symbol, index):
        """Compare separated bars in like units; never an execution quote.

        If the ex-bar is absent, a later raw bar's predecessor is still in old
        units. Convert that older comparison point without inventing the missing
        ex-close. The inherited execution gate independently blocks a missing
        previous calendar bar, and stale valuation remains explicitly marked.
        """
        row, previous = self.bars[symbol][index], self.bars[symbol][index - 1]
        if row["date"] in self.securities[symbol].get("precloseByDate", {}):
            return self._reference(symbol, index)
        factor = D(1)
        for event in self.distributions:
            if event["symbol"] == symbol and previous["date"] < event["exDate"] <= row["date"]:
                factor *= D(self.reference_changes[event["id"]]["factor"])
        return float(D(str(previous["close"])) * factor)

    def _step_day(self, day):
        action_step_day(self, day)
        daily = self.state["daily"][-1]
        daily["topNByStrategy"] = self.scorer.daily(day)["strategies"]
        daily["targetSelectionPolicy"] = "frozenOriginalRank;fixedCloseBudgetAndQuantity;nextSessionOnly;noBackfill;noAdd"
        daily["fixedSignalOpportunities"] = deepcopy(self._opportunities(day))
        daily["buyAttemptOrder"] = [row["signalId"] for row in self.research_state["opportunities"] if row["attemptDate"] == day]
        daily["researchCashBook"] = self.action_book.summary()

    def _action_diagnostics(self):
        return {"version": CORE_VERSION, "marketUsePermitted": False,
            "definitionDigest": self.action_definition, "policy": deepcopy(self.action_policy),
            "appliedEventIds": sorted(self.applied_action_ids), "sourcePins": deepcopy(self.action_source_pins),
            "cashBook": self.action_book.checkpoint(), "cashSummary": self.action_book.summary(),
            "newMarketAccounts": 0, "economicFitnessForGoalProven": False,
            "limitations": ["synthetic fixtures only; complete market event coverage not implemented",
                "record and ex must be adjacent source sessions; other custody paths rejected",
                "fraction floor/ceil and fixed tax reserve are declared research scenarios",
                "date-only dividends become spendable after pay-date; broker timestamp unverified",
                "pending ex-day buys keep the original close quantity and acquire no prior-record rights",
                "action-aware signal generation and full source reconstruction still required"]}

    def result(self):
        result = super().result()
        result["research"]["actionAccounting"] = self._action_diagnostics()
        result["quality"]["returnType"] = "syntheticActionAndTaxReserveScenario;notMarketEvidence"
        return result

    def checkpoint(self):
        result = super().checkpoint()
        result["actionAccounting"] = self._action_diagnostics()
        result["digest"] = _digest({key: value for key, value in result.items() if key != "digest"})
        return result
