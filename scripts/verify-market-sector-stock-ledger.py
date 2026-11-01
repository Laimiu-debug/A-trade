"""Independent fee/cash/episode and new-entry budget/guard arithmetic."""
from collections import defaultdict
from copy import deepcopy
import math


def check(ok, label):
    if not ok:
        raise ValueError(label)


def equal(a, b, label):
    if isinstance(a, dict) and isinstance(b, dict):
        check(a.keys() == b.keys(), label + ":fields")
        for key in a:
            equal(a[key], b[key], label + ":" + str(key))
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        check(len(a) == len(b), label + ":count")
        for i, (x, y) in enumerate(zip(a, b)):
            equal(x, y, label + ":" + str(i))
    elif type(a) in (int, float) and type(b) in (int, float):
        check(math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-7), label + ":number")
    else:
        check(a == b, label + ":value")


def fee(config, amount, side, day):
    tax = config["stampDutyRate"]
    if tax is None:
        tax = .0005 if day >= "2023-08-28" else .001
    return max(config["minCommission"], amount * config["commissionRate"]) + amount * config["transferFeeRate"] + (amount * tax if side == "sell" else 0.)


def lots(symbol):
    return (200, 1) if symbol.startswith("sh68") else (100, 100)


def quantity(value, minimum, increment):
    amount = max(0, math.floor(value / increment + 1e-10) * increment)
    return amount if amount >= minimum else 0


def affordable(config, ceiling, minimum, increment, price, budget, day):
    # Search the registered lot lattice with an explicit independent fee formula.
    low, high = 0, ceiling // increment
    while low < high:
        middle = (low + high + 1) // 2
        amount = middle * increment * price
        if amount + fee(config, amount, "buy", day) <= budget + 1e-8:
            low = middle
        else:
            high = middle - 1
    answer = low * increment
    return answer if answer >= minimum else 0


def audit_ledger_and_entries(result, dataset, system, closing_risk):
    c = result["config"]
    securities = {s["symbol"]: s for s in dataset["securities"]}
    wanted = {r["symbol"] for r in result["research"]["opportunities"]} | {r["symbol"] for r in result["trades"]}
    bars = {s: {b["date"]: b for b in securities[s]["bars"]} for s in wanted}
    dates = dataset["calendar"]
    calendar_index = {day: i for i, day in enumerate(dates)}
    contexts = {d["date"]: d for d in system["daily"]}
    meta = system["metadata"]
    opportunities = {r["signalId"]: r for r in result["research"]["opportunities"]}
    by_day, fills_by_day, plans_by_day = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in result["research"]["opportunities"]:
        by_day[row["signalDate"]].append(row)
    for fill in result["trades"]:
        fills_by_day[fill["date"]].append(fill)
    for plan in result["research"]["system"]["closePlans"]:
        plans_by_day[plan["date"]].append(plan)
    guard_map = {r["signalId"]: r for r in result["research"]["system"]["openingGuards"]}
    check(len(guard_map) == len(result["research"]["system"]["openingGuards"]), "Repeated opening attempt")
    cash, peak, previous_equity = c["initialCapital"], c["initialCapital"], c["initialCapital"]
    held, marks, last_industry, closed = {}, {}, {}, []
    quarantine = set()
    guards_checked, plans_checked = 0, 0
    for daily, curve in zip(result["daily"], result["curve"]):
        day = daily["date"]
        check(day == curve["date"], "Daily/curve date mismatch")
        sold_today = set()
        for fill in fills_by_day[day]:
            symbol, q, price, side = fill["symbol"], fill["quantity"], fill["price"], fill["side"]
            bar = bars[symbol][day]
            equal(fill["openingPrice"], bar["open"], "Source opening price")
            equal(price, bar["open"] * (1 + (1 if side == "buy" else -1)*c["slippageBps"]/10000), "Modeled opening slippage")
            expected_fee = fee(c, q*price, side, day)
            equal(fill["amount"], q*price, "Fill notional")
            equal(fill["fee"], expected_fee, "Independent modeled fill fees")
            before = held.get(symbol, {}).get("quantity", 0)
            equal(fill["quantityBefore"], before, "Opening position quantity")
            if side == "buy":
                check(not before, "Unregistered position addition")
                item, opportunity = meta[fill["signalId"]], opportunities[fill["signalId"]]
                check(day == dates[calendar_index[item["signalDate"]]+1], "Buy is not next independent session")
                check(item["failurePrice"] < bar["open"] <= item["signalClose"]*(1+item["meanNtr20"]), "Filled buy violates opening guard")
                minimum, increment = lots(symbol)
                risk_floor = max(item["failurePrice"], .95*price)
                risk_q = quantity(opportunity["systemRiskBudget"]/(price-risk_floor), minimum, increment)
                mapping = system["sectorMaps"].get(contexts[item["signalDate"]]["snapshotId"], {})
                industry_value = 0.
                for old, position in held.items():
                    code = mapping.get(old, last_industry[old])
                    raw_open = bars[old].get(day, {}).get("open")
                    mark = raw_open if raw_open is not None and raw_open > 0 else marks[old]
                    if code == item["sectorCode"]:
                        industry_value += position["quantity"]*mark
                room = max(0., .4*opportunity["systemEquity"]-industry_value)
                sector_q = quantity(room/price, minimum, increment)
                allowed = min(opportunity["plannedQuantity"], risk_q, sector_q)
                check(q <= allowed, "Filled quantity exceeds frozen risk/industry/planned limit")
                guard = guard_map[fill["signalId"]]
                for key, value in {"riskQuantity":risk_q,"sectorQuantity":sector_q,"allowedQuantity":allowed,"industryOpeningValue":industry_value,
                        "industryOpeningRoom":room,"effectiveFailureFloor":risk_floor,"modeledPrice":price}.items():
                    equal(guard[key], value, "Independent opening guard:"+key)
                check(q*price + expected_fee <= opportunity["budget"] + 1e-7, "Opening spent more than reserved budget")
                cash -= q*price + expected_fee
                held[symbol] = {"quantity":q,"cost":q*price+expected_fee,"invested":q*price+expected_fee,"realized":0.,"entryDate":day}
                marks[symbol], last_industry[symbol] = price, item["sectorCode"]
                guards_checked += 1
            else:
                check(symbol in held and held[symbol]["entryDate"] < day and 0 < q <= before, "Illegal sell population/clock")
                position = held[symbol]
                cost = position["cost"]*q/before
                realized = q*price-expected_fee-cost
                equal(fill["realizedPnl"], realized, "Independent sell realized PnL")
                cash += q*price-expected_fee
                position["quantity"] -= q
                position["cost"] -= cost
                position["realized"] += realized
                if not position["quantity"]:
                    closed.append({"symbol":symbol,"entryDate":position["entryDate"],"exitDate":day,"pnlAmount":position["realized"],
                        "investedAmount":position["invested"],"returnPct":position["realized"]/position["invested"]*100,
                        "holdingDays":calendar_index[day]-calendar_index[position["entryDate"]]+1})
                    del held[symbol]
                sold_today.add(symbol)
            equal(fill["quantityAfter"], held.get(symbol, {}).get("quantity",0), "Closing fill quantity")
            equal(fill["cashAfter"], cash, "Independent sequential fill cash")
        for symbol in held:
            bar = bars[symbol].get(day)
            if bar is not None and bar["volume"] > 0:
                marks[symbol] = bar["close"]
        value = sum(p["quantity"]*marks[s] for s,p in held.items())
        equity = cash + value
        peak = max(peak,equity)
        for key, expected in {"cash":cash,"marketValue":value,"equity":equity,"drawdownPct":(1-equity/peak)*100,
                "dailyReturn":equity/previous_equity-1}.items():
            equal(curve[key], expected, "Every source-marked daily curve:"+key)
        previous_equity = equity
        mapping = system["sectorMaps"].get(contexts[day]["snapshotId"], {})
        for symbol in held:
            if symbol in mapping:
                last_industry[symbol] = mapping[symbol]
        pending = closing_risk[day]["pending"]
        free = max(0,c["maxPositions"]-sum(s not in pending for s in held))
        industry_values = defaultdict(float)
        for symbol, position in held.items():
            industry_values[mapping.get(symbol,last_industry[symbol])] += position["quantity"]*marks[symbol]
        for decision in result["decisions"]:
            if decision["date"] == day and (decision.get("status") == "quarantined" or decision.get("reason") == "rawPriceDiscontinuity"):
                quarantine.add(decision["symbol"])
        cash_left = max(0.,cash)
        observed_plans = {r["signalId"]:r for r in plans_by_day[day]}
        check(len(observed_plans)==len(by_day[day]), "Every frozen opportunity must have a plan/rejection")
        for opportunity in by_day[day]:
            identifier,symbol = opportunity["signalId"],opportunity["symbol"]
            item, observed = meta[identifier],observed_plans[identifier]
            expected = {"date":day,"signalId":identifier,"equity":equity,"cashBeforeReservation":cash_left,"sectorCode":item["sectorCode"],
                "industryValueBefore":industry_values[item["sectorCode"]],"riskBudget":.01*equity,"failurePrice":item["failurePrice"]}
            for key,value in expected.items():equal(observed[key],value,"Independent close plan:"+key)
            reason = "pendingForcedExit" if symbol in pending else "alreadyHeldNoAdd" if symbol in held else "soldTodayOrQuarantined" if symbol in sold_today|quarantine else None
            if reason is None and not free:reason="plannedSlotsOccupied"
            # Other source-history eligibility rejections remain audited by the
            # inherited engine and explicit opportunity receipts, not this book.
            if observed["reason"] in ("notHistoricalMember","delisted","notListed","historicalST","missingOrSuspendedSignalClose","signalHistoryUnknown"):
                check(observed["status"]=="notPlanned", "Historical rejection changed execution status")
                continue
            if reason is not None:
                equal(observed["reason"],reason,"Independent close exclusion")
                continue
            free-=1
            minimum,increment=lots(symbol)
            price=item["signalClose"]*(1+c["slippageBps"]/10000)
            room=max(0.,.4*equity-industry_values[item["sectorCode"]])
            risk_q=quantity(.01*equity/(price-item["failurePrice"]),minimum,increment)
            budget=min(.2*equity,room,cash_left,risk_q*price)
            ceiling=min(risk_q,quantity(budget/price,minimum,increment))
            planned=affordable(c,ceiling,minimum,increment,price,budget,day)
            reserve_fee=fee(c,planned*price,"buy",day)
            next_day=dates[calendar_index[day]+1] if calendar_index[day]+1<len(dates) else None
            status="censored" if next_day is None or next_day>c["endDate"] else "notPlanned" if not planned else "planned"
            if status=="planned":
                cash_left-=planned*price+reserve_fee
                industry_values[item["sectorCode"]]+=planned*price
            for key,value in {"sectorRoom":room,"reservePrice":price,"budget":budget,"riskQuantity":risk_q,"plannedQuantity":planned,
                    "reserveFee":reserve_fee,"cashAfterReservation":cash_left,"status":status}.items():
                equal(observed[key],value,"Independent close sizing:"+key)
            plans_checked+=1
    equal(result["closedTrades"],closed,"Independent completely closed episodes")
    return {"passed":True,"sourceMarkedCurveDays":len(result["curve"]),"fillsChecked":len(result["trades"]),
        "closedEpisodesChecked":len(closed),"newEntryCloseSizingChecks":plans_checked,"filledOpeningGuardChecks":guards_checked,
        "allFillFeesCashAndEpisodePnlReplayed":True,"allObservedPriceRiskAndIndustryBudgetCapsRecomputed":True,
        "inheritedHistoryGateReceiptScopeDisclosed":True,"secondExecutionEngine":False}
