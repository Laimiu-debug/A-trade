"""Dated cash and share rights. Prices stay raw; cash is not available early.

Cash dividends use explicitly supplied NET cash, a declared research tax model.
No automatic inference of an individual's deferred dividend income tax is made.
"""
from copy import deepcopy
from datetime import date
import math


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("权益事件日期须为 YYYY-MM-DD")
    return value


def _number(value, key):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1e8:
        raise ValueError(f"权益事件 {key} 须为非负有限数值")
    return float(value)


def normalize_actions(raw):
    if not isinstance(raw, list):
        raise ValueError("corporateActions 须为列表")
    result, identities = [], set()
    keys = {"id", "type", "announcementDate", "recordDate", "exDate", "cashPayDate", "shareListingDate",
            "cashNetPerShare", "bonusSharesPerShare", "rightsSharesPerShare", "rightsPrice", "rightsPayDate",
            "source", "cashTaxModel"}
    for item in raw:
        if not isinstance(item, dict) or set(item) - keys:
            raise ValueError("权益事件包含未知字段")
        ident = item.get("id")
        if not isinstance(ident, str) or not ident or len(ident) > 100 or ident in identities:
            raise ValueError("权益事件 id 不可为空或重复")
        identities.add(ident)
        kind = item.get("type", "distribution")
        if kind not in ("distribution", "cash_settlement", "rights_refund"):
            raise ValueError("权益事件类型只支持 distribution/cash_settlement/rights_refund")
        row = {"id": ident, "type": kind, "source": item.get("source")}
        if not isinstance(row["source"], str) or not row["source"].strip():
            raise ValueError("权益事件须明确提供公告或数据来源")
        for key in ("announcementDate", "recordDate", "exDate"):
            row[key] = _day(item.get(key))
        if not row["announcementDate"] <= row["recordDate"] < row["exDate"]:
            raise ValueError("须满足公告日 <= 登记日 < 除权日")
        for key in ("cashNetPerShare", "bonusSharesPerShare", "rightsSharesPerShare", "rightsPrice"):
            row[key] = _number(item.get(key, 0), key)
        if not any(row[key] for key in ("cashNetPerShare", "bonusSharesPerShare", "rightsSharesPerShare")):
            raise ValueError("权益事件不能没有现金、股份或配股权益")
        row["cashTaxModel"] = item.get("cashTaxModel", "declared_net")
        if row["cashTaxModel"] != "declared_net":
            raise ValueError("现金权益须明确使用外部提供的税后金额 declared_net")
        for key, required in (("cashPayDate", row["cashNetPerShare"] > 0 or kind == "rights_refund"),
                              ("shareListingDate", kind != "rights_refund" and row["bonusSharesPerShare"] + row["rightsSharesPerShare"] > 0),
                              ("rightsPayDate", row["rightsSharesPerShare"] > 0)):
            row[key] = _day(item.get(key)) if required else None
            if required and key != "rightsPayDate" and row[key] < row["exDate"]:
                raise ValueError(f"{key} 不得早于除权日")
        if row["rightsSharesPerShare"] and (row["rightsPrice"] <= 0 or row["rightsPayDate"] <= row["recordDate"] or (
                row["shareListingDate"] is not None and row["rightsPayDate"] > row["shareListingDate"])):
            raise ValueError("配股须有正认购价，付款日不得晚于股份可交易日")
        if kind == "cash_settlement" and (not row["cashNetPerShare"] or row["bonusSharesPerShare"] or row["rightsSharesPerShare"]):
            raise ValueError("现金退市结算只允许税后现金")
        if kind == "rights_refund" and (not row["rightsSharesPerShare"] or row["cashNetPerShare"] or row["bonusSharesPerShare"] or row["rightsPayDate"] >= row["exDate"]):
            raise ValueError("配股失败退款须有除权/结果日前的认购付款，以及其后的退款到账日期")
        result.append(row)
    return sorted(result, key=lambda row: (row["exDate"], row["id"]))


def build_schedule(securities):
    schedule = {}
    for symbol, security in securities.items():
        raw = security.get("corporateActions", [])
        if security.get("corporateActionsComplete") is True:
            actions = normalize_actions(raw)
        else:
            actions = []
            for item in raw:
                try:
                    actions.extend(normalize_actions([item]))
                except (ValueError, TypeError):
                    pass  # Legacy adjustment clues are not cash/share instructions.
        for action in actions:
            row = {**action, "symbol": symbol, "eventKey": symbol + ":" + action["id"]}
            for stage, day in (("record", row["recordDate"]), ("ex", row["exDate"])):
                schedule.setdefault(day, []).append((stage, row))
            if row["rightsSharesPerShare"] and row["rightsPayDate"] < row["exDate"]:
                schedule.setdefault(row["rightsPayDate"], []).append(("subscription", row))
    return schedule


def _log(engine, day, row, stage, **values):
    engine.state["actionLedger"].append({"date": day, "symbol": row["symbol"], "eventId": row["id"],
                                          "stage": stage, "source": row["source"], **values})


def capture_rights(engine, day):
    for stage, row in engine.action_schedule.get(day, []):
        if stage != "record":
            continue
        position = engine.state["positions"].get(row["symbol"])
        eligible_lots = [lot for lot in position["lots"] if lot.get("unlockDate", lot["date"]) <= day] if position else []
        quantity = sum(lot["quantity"] for lot in eligible_lots)
        engine.state["actionEntitlements"][row["eventKey"]] = {"quantity": quantity,
            "recordDate": day, "lots": deepcopy(eligible_lots)}
        _log(engine, day, row, "record", entitledQuantity=quantity)


def apply_actions(engine, day):
    s = engine.state
    for stage, row in engine.action_schedule.get(day, []):
        if stage == "subscription":
            quantity = s["actionEntitlements"].get(row["eventKey"], {}).get("quantity", 0) * row["rightsSharesPerShare"]
            integral = abs(quantity - round(quantity)) <= 1e-8
            amount = round(quantity) * row["rightsPrice"] if integral else 0.
            subscribed = integral and quantity > 0 and engine.config["rightsPolicy"] == "subscribe" and s["cash"] - s["reservedActionCash"] >= amount
            s["preExRights"][row["eventKey"]] = {"amount": amount if subscribed else 0., "quantity": round(quantity) if subscribed else 0,
                                                "symbol": row["symbol"], "paymentDate": day}
            if subscribed:
                s["cash"] -= amount
            _log(engine, day, row, "subscriptionPaid" if subscribed else "subscriptionDeclined", amount=amount if subscribed else 0.)
            continue
        if stage != "ex" or row["eventKey"] in s["processedActions"]:
            continue
        entitled = s["actionEntitlements"].get(row["eventKey"], {}).get("quantity", 0)
        symbol = row["symbol"]
        position = s["positions"].get(symbol)
        prepaid = s["preExRights"].pop(row["eventKey"], None)
        if row["type"] == "rights_refund":
            refund = (prepaid or {}).get("amount", 0.)
            if refund:
                s["cashReceivables"].append({"eventKey": row["eventKey"], "symbol": symbol, "amount": refund, "payDate": row["cashPayDate"]})
            s["processedActions"].append(row["eventKey"])
            _log(engine, day, row, "refundReceivable", amount=refund)
            continue
        bonus = entitled * row["bonusSharesPerShare"]
        rights = entitled * row["rightsSharesPerShare"]
        if abs(bonus - round(bonus)) > 1e-8 or abs(rights - round(rights)) > 1e-8:
            if prepaid:
                s["preExRights"][row["eventKey"]] = prepaid
            s["unaccountedActionExposure"].append({"symbol": symbol, "date": day, "eventId": row["id"], "reason": "fractionalShareAllocationNotSupplied"})
            s["processedActions"].append(row["eventKey"])
            _log(engine, day, row, "unsupported", reason="fractionalShareAllocationNotSupplied")
            continue
        bonus, rights = round(bonus), round(rights)
        cash = entitled * row["cashNetPerShare"]
        if row["type"] == "cash_settlement" and position:
            cash = position["quantity"] * row["cashNetPerShare"]
            pnl = cash - position["costBasis"]
            s["closedTrades"].append({"symbol": symbol, "entryDate": position["entryDate"], "exitDate": day,
                "pnlAmount": position["realizedPnl"] + pnl, "investedAmount": position["investedAmount"],
                "returnPct": (position["realizedPnl"] + pnl) / position["investedAmount"] * 100 if position["investedAmount"] > 0 else None,
                "holdingDays": engine.calendar_index[day] - engine.calendar_index[position["entryDate"]] + 1, "exitType": "declaredNetCashSettlement"})
            del s["positions"][symbol]
            position = None
        elif cash and position:
            position["realizedPnl"] += cash
        if cash:
            s["cashReceivables"].append({"eventKey": row["eventKey"], "symbol": symbol,
                                        "amount": cash, "payDate": row["cashPayDate"]})
        subscribe = bool((prepaid or {}).get("quantity")) if prepaid is not None else bool(rights and engine.config["rightsPolicy"] == "subscribe"
                         and s["cash"] - s["reservedActionCash"] >= rights * row["rightsPrice"] - 1e-8)
        payment = ((prepaid or {}).get("amount", 0.) if prepaid is not None else rights * row["rightsPrice"]) if subscribe else 0.
        added = bonus + (rights if subscribe else 0)
        if added and position is None:
            reference = engine.securities[symbol].get("precloseByDate", {}).get(day)
            bar, _ = engine._bar(symbol, day)
            price = reference or (bar or {}).get("close", row["rightsPrice"] or 1.)
            position = {"symbol": symbol, "quantity": 0, "costBasis": 0., "lots": [], "entryDate": day,
                        "entryCursor": s["cursor"], "riskEntryPrice": price, "closePeak": price,
                        "realizedPnl": 0., "investedAmount": 0.}
            s["positions"][symbol] = position
        if added and position:
            # Bonus basis is redistributed across original/new shares. Rights
            # add committed subscription cost, even before payment is due.
            old_quantity = position["quantity"]
            bonus_basis = position["costBasis"] * bonus / (old_quantity + bonus) if bonus else 0.
            if bonus_basis:
                factor = old_quantity / (old_quantity + bonus)
                for lot in position["lots"]:
                    lot["cost"] *= factor
            position["lots"].append({"date": position["entryDate"], "unlockDate": row["shareListingDate"],
                                     "quantity": added, "cost": bonus_basis + payment})
            position["quantity"] += added
            position["costBasis"] += payment
            position["investedAmount"] += payment
        if payment and prepaid is None:
            s["actionPayables"].append({"eventKey": row["eventKey"], "symbol": symbol,
                                       "amount": payment, "payDate": row["rightsPayDate"]})
            s["reservedActionCash"] += payment
        reference = engine.securities[symbol].get("precloseByDate", {}).get(day)
        bar, index = engine._bar(symbol, day)
        previous = engine.bars[symbol][index - 1]["close"] if index else None
        if position and reference and previous:
            position["riskEntryPrice"] *= reference / previous
            position["closePeak"] *= reference / previous
            s["lastPrices"][symbol], s["lastPriceDates"][symbol] = reference, day
        if s["pendingTarget"] and symbol in s["pendingTarget"]["weights"]:
            # Frozen pre-action quantities are invalid; wait for a new close.
            for key in ("weights", "quantities", "budgets", "priorityBySymbol", "signalDates"):
                s["pendingTarget"].get(key, {}).pop(symbol, None)
            if position:
                s["pendingTarget"]["weights"][symbol] = 0.
                s["pendingTarget"]["quantities"][symbol] = position["quantity"]
                s["pendingTarget"]["budgets"][symbol] = 0.
                s["pendingTarget"]["priorityBySymbol"][symbol] = {"budgetWeight": 0., "weightedScore": 0.}
        s["processedActions"].append(row["eventKey"])
        _log(engine, day, row, "ex", entitledQuantity=entitled, cashReceivable=cash,
             bonusShares=bonus, rightsShares=rights if subscribe else 0, subscriptionPayable=payment,
             rightsOutcome="subscribed" if subscribe else "skippedInsufficientCash" if rights and engine.config["rightsPolicy"] == "subscribe" else "declined" if rights else "notApplicable")
    for key, sign in (("cashReceivables", 1), ("actionPayables", -1)):
        remaining = []
        for item in s[key]:
            if item["payDate"] <= day:
                s["cash"] += sign * item["amount"]
                if sign < 0:
                    s["reservedActionCash"] -= item["amount"]
                s["actionLedger"].append({"date": day, "symbol": item["symbol"], "eventId": item["eventKey"],
                                         "stage": "cashPaid" if sign < 0 else "cashReceived", "amount": item["amount"]})
            else:
                remaining.append(item)
        s[key] = remaining


def asset_adjustment(state):
    return (sum(item["amount"] for item in state["cashReceivables"]) - sum(item["amount"] for item in state["actionPayables"])
            + sum(item["amount"] for item in state["preExRights"].values()))
