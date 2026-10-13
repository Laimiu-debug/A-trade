"""Walk-forward capital continuity with explicit, costed policy handoffs.

Fold selections have been frozen from their dated training inputs. Actual
positions, pending rights, cash and trade episodes survive every handoff.
"""
from copy import deepcopy
from .portfolio_engine import PortfolioEngine
from .portfolio_rotation import decide_rotation, ROTATION_MODES
from .portfolio_study import _digest, _slice, _quality_failures, rotation_comparison
from .portfolio_prepared import PreparedCache

VERSION = "continuous-ledger-v1"
ACCOUNTS = ("softRotation", "hardRotation", "equalWeight", "fixed")


class ContinuousRunner:
    def __init__(self, dataset, config, candidates, folds, state=None):
        self.dataset, self.config, self.folds = dataset, config, folds
        self.fingerprint = dataset.get("fingerprint") or _digest(dataset)
        self.candidates = {row["candidateId"]: row["config"] for row in candidates}
        if not folds or any(not fold["selection"].get("candidateId") for fold in folds):
            raise ValueError("连续账户需要每折均有可冻结的训练选择")
        self.signature = _digest({"version": VERSION, "data": _digest(dataset), "config": config,
                                  "selection": [fold["selection"] for fold in folds]})
        self.state = {"signature": self.signature, "accountIndex": 0, "segmentIndex": 0,
                      "active": None, "carry": None, "boundaryPrepared": True, "runs": {},
                      "retrainLog": [], "completedDays": 0, "engineRunCount": 0}
        if state is not None:
            restored = deepcopy(state)
            check = restored.pop("checkpointDigest", None)
            if restored.get("signature") != self.signature or check != _digest(restored):
                raise ValueError("连续账户检查点与冻结选择或数据不一致")
            self.state = restored
        self.engine = None
        self.prepared = PreparedCache()
        self.calendar = dataset.get("calendar") or sorted({bar["date"] for security in dataset["securities"] for bar in security["bars"]})
        self.dates = [day for day in self.calendar if folds[0]["testStart"] <= day <= folds[-1]["testEnd"]]

    def _config(self):
        account = ACCOUNTS[self.state["accountIndex"]]
        segment = self.state["segmentIndex"]
        fold = self.folds[segment if account in ("softRotation", "hardRotation") else 0]
        selected = fold["selection"]["candidateId"]
        config = deepcopy(self.candidates[selected])
        ids = list(config["allocation"]["strategyIds"])
        if account == "fixed":
            fixed_id = fold["training"][selected]["selectedStrategyId"]
            mode, ids = "single", [fixed_id]
        elif account == "equalWeight":
            mode = "equal"
        elif fold["persistence"] and fold["persistence"].get("passed"):
            mode = "soft_rotation" if account == "softRotation" else "hard_rotation"
        else:
            mode = "equal"
        config["allocation"] = {"mode": mode, "strategyIds": ids}
        config["startDate"] = self.folds[segment]["testStart"] if account in ("softRotation", "hardRotation") else self.dates[0]
        config["endDate"] = (self.calendar[self.calendar.index(self.folds[segment + 1]["testStart"]) - 1]
                             if account in ("softRotation", "hardRotation") and segment + 1 < len(self.folds)
                             else self.dates[-1])
        return config

    def _init(self):
        config = self._config()
        self.engine = self.prepared.create(PortfolioEngine, _slice(self.dataset, config["endDate"]), config, state=self.state["active"])
        carry = self.state["carry"]
        if carry and self.state["active"] is None:
            immutable = {"config", "engineVersion", "rulesVersion", "scorerVersion", "dataDigest", "datasetFingerprint",
                         "cursor", "shadowStates", "rotationWeights", "rotationLastAdjustment", "carriedHistoryDays"}
            for key, value in carry.items():
                if key not in immutable:
                    self.engine.state[key] = deepcopy(value)
            self.engine.state["carriedHistoryDays"] = len(carry["curve"])
            ids = config["allocation"]["strategyIds"]
            if set(carry["rotationWeights"]) == set(ids) and self.engine.rotation_enabled:
                self.engine.state["rotationWeights"] = deepcopy(carry["rotationWeights"])
                self.engine.state["rotationLastAdjustment"] = carry["rotationLastAdjustment"]
            self.state["boundaryPrepared"] = False
            self.state["carry"] = None

    def _boundary(self, days):
        engine, s = self.engine, self.engine.state
        day = s["curve"][-1]["date"]
        if engine.shadows:
            engine._advance_shadows(day, limit=days)
            if any(shadow.state["cursor"] < len(shadow.dates) and shadow.dates[shadow.state["cursor"]] <= day for shadow in engine.shadows.values()):
                return False
        scored = engine.scorer.daily(day)
        decision_start = len(s["decisions"])
        engine._risk_exits(day, scored.get("eligibilityBySymbol", {}))
        if engine.rotation_enabled:
            decision = decide_rotation(day, engine.calendar_index[day], engine.config["allocation"]["mode"],
                engine.config["rotation"], s["rotationWeights"], s["rotationLastAdjustment"],
                {key: shadow.state["curve"] for key, shadow in engine.shadows.items()}, scored.get("regime"))
            if decision["status"] == "adjusted":
                s["rotationWeights"] = decision["after"]
                s["rotationLastAdjustment"] = engine.calendar_index[day]
                decision["earliestExecutionDate"] = engine.dates[0]
                s["rotationLog"].append(decision)
        selected, weights, strategy_weights, details, clipped, priorities = engine._select_targets(scored, engine._excluded_today(day) | set(s["quarantined"]))
        equity = s["curve"][-1]["equity"]
        old_target = s["pendingTarget"]
        for symbol in sorted((old_target or {}).get("weights", {})):
            engine._decision(day, symbol, "target", "superseded", "trainingPolicyChanged", priorSignalDate=old_target["signalDate"])
        s["pendingTarget"] = {"signalDate": day, "signalEquity": equity, "weights": weights, "quantities": {},
            "reason": "walkForwardRetrain", "budgets": {symbol: equity * weight for symbol, weight in weights.items()},
            "priorityBySymbol": priorities, "signalDates": {}}
        s["selectedByStrategy"] = selected
        # New parameters are known at this preceding close, so the published
        # next-open intent must match the actual order, not the superseded one.
        s["daily"][-1].update(strategyWeights=strategy_weights, strategyBudgetWeights=strategy_weights,
            targetWeights=deepcopy(weights), targetSignalDate=day, selectedCandidatesByStrategy=details,
            selectedByStrategy=deepcopy(selected), targetPriority=engine._priority_order(weights, priorities),
            targetPriorityBySymbol=deepcopy(priorities), clippedTargets=deepcopy(clipped), policyChange="walkForwardRetrain",
            strategyWeightMeaning="nextOpenTargetBudgetNotRealizedHoldingAttribution" if engine.rotation_enabled else "fixedEntryBudgetNotRealizedHoldingAttribution",
            topNByStrategy={key: [{**deepcopy(row), "rank": i + 1} for i, row in enumerate(rows[:engine.config["topN"]])]
                            for key, rows in scored["strategies"].items()})
        for symbol in weights:
            engine._decision(day, symbol, "target", "queued", "walkForwardRetrain", targetWeight=weights[symbol], signalEquity=equity)
        s["daily"][-1].setdefault("activities", []).extend(deepcopy(s["decisions"][decision_start:]))
        holdings = engine._daily_holdings(day, equity)
        s["daily"][-1].update(holdings=holdings, actualStockWeights={row["symbol"]: row["weight"] for row in holdings},
            positionControl=engine._position_control(day, s["curve"][-1], False),
            nextSessionPlan=engine._next_session_plan(day, holdings))
        self.state["retrainLog"].append({"account": ACCOUNTS[self.state["accountIndex"]], "signalDate": day,
            "firstExecutionDate": engine.dates[0], "fold": self.state["segmentIndex"] + 1,
            "configSignature": _digest(engine.config), "cashCarried": s["cash"],
            "positionsCarried": {symbol: position["quantity"] for symbol, position in s["positions"].items()},
            "pendingRightsCarried": len(s["cashReceivables"]) + len(s["actionPayables"]) + len(s["preExRights"]),
            "mode": engine.config["allocation"]["mode"]})
        self.state["boundaryPrepared"] = True
        return True

    def step(self, days=5):
        if self.state["accountIndex"] == len(ACCOUNTS):
            return True
        if self.engine is None:
            self._init()
        if not self.state["boundaryPrepared"]:
            self._boundary(days)
            self.state["active"] = self.engine.checkpoint()
            return False
        done = self.engine.step(days)
        self.state["active"] = self.engine.checkpoint()
        if done:
            self.state["engineRunCount"] += 1
            self.state["completedDays"] += len(self.engine.dates)
            account = ACCOUNTS[self.state["accountIndex"]]
            segmented = account in ("softRotation", "hardRotation")
            if segmented and self.state["segmentIndex"] + 1 < len(self.folds):
                self.state["carry"] = self.state["active"]
                self.state["segmentIndex"] += 1
            else:
                self.state["runs"][account] = self.engine.result()
                self.state["accountIndex"] += 1
                self.state["segmentIndex"] = 0
            self.state["active"], self.engine = None, None
        return self.state["accountIndex"] == len(ACCOUNTS)

    def checkpoint(self):
        state = deepcopy(self.state)
        state["checkpointDigest"] = _digest(state)
        return state

    @property
    def progress(self):
        current = self.engine.state["cursor"] if self.engine else (self.state.get("active") or {}).get("cursor", 0)
        return {"progress": min(1., (self.state["completedDays"] + current) / (len(self.dates) * len(ACCOUNTS))),
                "phase": "continuous boundary preparation" if not self.state["boundaryPrepared"] else "continuous live ledger"}

    def result(self):
        complete = self.state["accountIndex"] == len(ACCOUNTS)
        failures = sorted({failure for run in self.state["runs"].values()
                           for failure in _quality_failures(run, self.fingerprint)})
        persistence = {"passed": all(fold.get("persistence", {}).get("passed") for fold in self.folds)}
        comparison, gate = rotation_comparison(self.state["runs"], self.config["initialCapital"], persistence,
                                               True, complete, failures)
        return {"status": "complete" if complete else "running",
                "runs": deepcopy(self.state["runs"]), "retrainLog": deepcopy(self.state["retrainLog"]),
                "engineRunCount": self.state["engineRunCount"],
                "comparison": comparison, "rotationGate": gate,
                "progress": self.progress, "protocol": {"capitalResetAtFoldBoundary": False,
                    "pendingRightsAndPositionsCarried": True, "gapsTradedUnderPreviousPolicy": True,
                    "fixedAndEqualParametersFrozenFromFirstTrainingFold": True,
                    "retrainSignalTime": "preceding close; next session open; all fills/costs booked",
                    "persistenceFailurePolicy": "equal allocation until a later fold passes; not certified as rotation value"}}
