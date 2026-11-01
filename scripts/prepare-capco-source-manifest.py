"""Extract literal JSON article bodies and observed PDF links without JS eval."""
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/market-sector-stock-system-v1/capco-intake-v1"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items, self.active, self.text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.active, self.text = dict(attrs).get("href"), []

    def handle_data(self, value):
        if self.active is not None:
            self.text.append(value)

    def handle_endtag(self, tag):
        if tag == "a" and self.active is not None:
            self.items.append({"url": self.active.strip(), "label": "".join(self.text).strip()})
            self.active = None


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    receipt = json.loads((OUT / "articles/receipt.json").read_text(encoding="utf-8-sig"))
    if not receipt["allSixArchived"]:
        raise ValueError("All six observed source articles required")
    rows = []
    for item in receipt["receipts"]:
        source = ROOT / item["path"]
        if sha(source) != item["sha256"]:
            raise ValueError("Article source changed")
        literal = source.read_text(encoding="utf-8-sig").strip()
        prefix = "export const allData = "
        if not literal.startswith(prefix):
            raise ValueError("Expected a literal JSON article, not executable source")
        article = json.loads(literal[len(prefix):].rstrip(";"))
        if article["date"] != item["publicationDate"] or article["title"] != item["title"] or article["source"] != "中国上市公司协会":
            raise ValueError("Independent article date/title and catalogue mismatch")
        parser = Links()
        parser.feed(article["text"])
        attachments = [l for l in parser.items if l["url"].lower().endswith(".pdf") and "行业分类结果" in l["label"]]
        if len(attachments) != 2:
            raise ValueError("Both independently sorted industry documents required")
        for attachment in attachments:
            if not attachment["url"].startswith("https://sp.capco.org.cn:82/"):
                raise ValueError("Unexpected non-observed primary attachment origin")
            attachment["sortOrder"] = "stockCode" if "按股票代码排序" in attachment["label"] else "industry" if "按行业排序" in attachment["label"] else None
        if {a["sortOrder"] for a in attachments} != {"stockCode", "industry"}:
            raise ValueError("Distinct stock-code and industry sort documents required")
        rows.append({"title": article["title"], "publicationDate": article["date"],
            "article": {"path": item["path"], "sha256": item["sha256"], "url": item["url"]},
            "availableToDecision": "first independent-calendar trading session strictly after publicationDate; no same-day use", "attachments": attachments})
    value = {"schema": "capco-observed-snapshot-pdf-manifest-v1", "snapshots": rows,
        "articlesReceipt": {"path": (OUT / "articles/receipt.json").relative_to(ROOT).as_posix(), "sha256": sha(OUT / "articles/receipt.json")},
        "sourceCodeExecuted": False, "currentSnapshotBackfilled": False, "newMarketAccounts": 0,
        "historicalRevisionLimit": "Current official migrated publication archive; original release dates and dated document titles verified, original-time byte archives unavailable."}
    with (OUT / "pdf-manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"snapshots": len(rows), "pdfs": sum(len(r['attachments']) for r in rows), "dates": [r['publicationDate'] for r in rows]}))


if __name__ == "__main__":
    main()
