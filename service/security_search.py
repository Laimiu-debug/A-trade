"""Read-only stock lookup using the user's chosen Tongdaxin name catalogue."""
from __future__ import annotations

from pathlib import Path
import re

from service import tdx


class SecuritySearch:
    def __init__(self, owner):
        self.owner = owner
        self.cache = None

    def invalidate(self):
        self.cache = None

    def catalogue(self):
        # A selected daily-data directory takes precedence; a standalone MCP
        # installation also has TNF names even before daily files are downloaded.
        source = self.owner._get("path") or self.owner.online.config()["root"]
        if not source:
            return {}, [], False
        root = Path(source)
        signature = []
        warnings = []
        for market in tdx.MARKETS:
            try:
                path = tdx.within(root / "T0002" / "hq_cache" / f"{market}s.tnf", root)
                stat = path.stat()
                signature.append([market, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
            except (ValueError, OSError):
                signature.append([market, None])
        prior = self.cache
        if prior is None or prior.get("source") != source:
            stored = self.owner._get("securityNameCatalogue")
            prior = stored if isinstance(stored, dict) and stored.get("source") == source else None
        if prior and prior.get("signature") == signature:
            self.cache = prior
            return prior["items"], prior.get("warnings", []), False
        catalogue, warnings = tdx.read_name_catalogue(root)
        catalogue = {symbol: value for symbol, value in catalogue.items()
                     if tdx.security_kind(symbol[:2], symbol[2:]) != "other"}
        if not catalogue and prior:
            # Preserve names when the selected disk is temporarily disconnected.
            self.cache = prior
            return prior["items"], warnings + ["证券名称文件暂不可读取，搜索使用此目录此前缓存的名称。"], True
        self.cache = {"source": source, "signature": signature, "items": catalogue, "warnings": warnings}
        with self.owner.db:
            self.owner._set("securityNameCatalogue", self.cache)
        return catalogue, warnings, False

    def items(self, kind):
        catalogue, warnings, cached = self.catalogue()
        items = {}
        for row in self.owner.db.execute("SELECT * FROM securities"):
            if kind and row["kind"] != kind:
                continue
            metadata = catalogue.get(row["symbol"], {})
            items[row["symbol"]] = {"symbol": row["symbol"], "code": row["code"],
                                    "name": metadata.get("name") or row["name"],
                                    "initials": metadata.get("initials", ""), "market": row["market"],
                                    "kind": row["kind"], "latestDate": row["latest_date"]}
        # Valuation and quick selection can analyse named stocks without local
        # bars. Existing unfiltered market-list callers retain their scan scope.
        if kind == "stock":
            for symbol, metadata in catalogue.items():
                if symbol in items or tdx.security_kind(symbol[:2], symbol[2:]) != "stock":
                    continue
                items[symbol] = {"symbol": symbol, "code": symbol[2:], "name": metadata["name"],
                                 "initials": metadata.get("initials", ""), "market": symbol[:2],
                                 "kind": "stock", "latestDate": None}

        return items, warnings, cached

    def resolve(self, question):
        """Resolve explicit names/codes locally; never call a model or download bars."""
        if not isinstance(question, str) or not 1 <= len(question) <= 4000:
            raise ValueError("问题须为 1 至 4000 字")
        text = question.lower()
        items, _, _ = self.items("stock")
        groups, covered = [], []
        for match in re.finditer(r"(?<![a-z0-9])(?:(sh|sz|bj)\s*)?(\d{6})(?!\d)", text):
            market, code = match.groups()
            matches = [row for row in items.values() if row["code"] == code and (not market or row["market"] == market)]
            groups.append({"mention": match.group(), "items": matches})
        # Longest names first, so a short name contained in another name cannot
        # silently select a second security. Identical names remain ambiguous.
        names = sorted({row["name"].lower() for row in items.values()
                        if len(row["name"]) >= 2 and row["name"] != "名称待补全"}, key=lambda name: (-len(name), name))
        for name in names:
            positions = [m.span() for m in re.finditer(re.escape(name), text)]
            accepted = [(a, b) for a, b in positions if not any(a >= c and b <= d for c, d in covered)]
            if not accepted:
                continue
            covered.extend(accepted)
            groups.append({"mention": name, "items": [row for row in items.values() if row["name"].lower() == name]})
        explicit = {group["items"][0]["symbol"] for group in groups if len(group["items"]) == 1}
        targets, ambiguous, unknown = {}, [], []
        for group in groups:
            selected = [row for row in group["items"] if row["symbol"] in explicit]
            if len(selected) == 1:
                group = {**group, "items": selected}
            if len(group["items"]) == 1:
                row = group["items"][0]
                targets[row["symbol"]] = {key: row[key] for key in ("symbol", "code", "name")}
            elif group["items"]:
                ambiguous.append({"mention": group["mention"], "items": [{key: row[key] for key in ("symbol", "code", "name")} for row in group["items"][:8]]})
            else:
                unknown.append(group["mention"])
        if len(targets) > 3 or len(ambiguous) > 3 or len(unknown) > 3:
            raise ValueError("一次最多分析 3 只股票，请缩小提问范围")
        return {"items": list(targets.values()), "ambiguous": ambiguous, "unknown": unknown,
                "scope": "本地证券名称与代码目录；不包含简称猜测或历史名称还原"}

    def search(self, query, *, kind, offset, limit):
        items, warnings, cached = self.items(kind)

        def fields(item):
            return [item[key].lower() for key in ("symbol", "code", "name", "initials")]

        def rank(item):
            values = fields(item)
            priority = 0 if query in values else 1 if any(value.startswith(query) for value in values) else 2
            return priority, item["symbol"]

        matches = [item for item in items.values() if not query or any(query in value for value in fields(item))]
        matches.sort(key=rank)
        return {"items": matches[offset:offset + limit], "total": len(matches), "offset": offset,
                "limit": limit, "cached": cached, "warnings": warnings}
