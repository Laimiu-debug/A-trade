#!/usr/bin/env python3
"""High-speed, resumable bulk archiver for Xueqiu Official Column (e.g. 顽主杯实盘大赛).

Features:
- Pulls full timeline index (`statuses/original/timeline.json`) back to `--since` (default 2022-01-01).
- Auto-categorizes every article into `special` (专场/交割单/冠军/榜眼/探花/访谈/闭幕/总结/伤情报告/复盘),
  `signup` (报名/花名册/通知), or `daily` (每日赛况).
- Exports clean Excel-ready `articles.csv` + `articles.json`.
- Downloads full article body text (`content.txt`) + original lossless images concurrently with
  automatic resume (skips completed folders) and a disk-space safety brake (`--min-free-gb`).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import html
import io
import json
import random
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
from lxml import html as lxml_html
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_DIR = REPO_ROOT / "artifacts" / "wechat-jiaogedan"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

SPECIAL_KEYWORDS = (
    "专场",
    "交割单",
    "专访",
    "我要问大佬",
    "冠军",
    "榜眼",
    "探花",
    "三冠王",
    "双冠王",
    "闭幕",
    "总结",
    "伤情报告",
    "盘点",
    "心得",
    "感悟",
    "列传",
    "乌江望月",
    "如晗",
    "松松",
    "山顶",
    "董事长",
    "前三甲",
    "情绪周期",
    "腰斩鞭尸",
    "启示录",
    "翻倍",
    "风停的日子练好桨",
    "两年百倍",
    "40万做到",
    "最锋利的盾",
    "这个市场没有神",
    "机会留给有子弹的人",
    "克制与重复",
    "绝境重生",
    "让交易更纯粹",
    "十年磨一剑",
    "杀伐果断",
    "打板转型",
    "坚定主线",
    "择时",
    "侥幸踏上",
    "我宁愿像只蟑螂",
    "格局大佬",
)

SIGNUP_KEYWORDS = (
    "报名贴",
    "报名帖",
    "欢迎报名",
    "花名册",
    "明日开赛",
    "明天开赛",
    "求才子赐教",
    "段友投稿",
)

CHROME_ASSET_IDS = {
    "18cc98da6c19cdc03fdb7c76.png",
    "19057a761e3255983fe63001.png",
    "19a0498f258369083fcf4ac6.png",
    "18a444678641ba633fea68a0.png",
    "1989d348a7013f343fe480f1.png",
    "19518c8a3fa323bb3fcabb09.png",
}


def sanitize_filename(name: str, max_len: int = 56) -> str:
    cleaned = html.unescape(name or "").strip()
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "untitled"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .")
    return cleaned


def format_ms(ms: int | float | None) -> tuple[str, str]:
    if not ms:
        return "0000-00-00", ""
    sec = float(ms) / 1000.0 if float(ms) > 1e11 else float(ms)
    d = dt.datetime.fromtimestamp(sec)
    return d.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d %H:%M:%S")


def parse_since_ms(since_str: str) -> int:
    return int(dt.datetime.strptime(since_str, "%Y-%m-%d").timestamp() * 1000)


def classify_article(title: str, desc: str) -> str:
    t = title or ""
    if any(k in t for k in SIGNUP_KEYWORDS):
        return "signup"
    if any(k in t for k in SPECIAL_KEYWORDS):
        return "special"
    # Check description for strong interview / settlement-statement markers
    d = desc or ""
    if any(k in d for k in ("交割单", "参赛总结", "比赛感想", "作者：", "作者 ", "专访")):
        return "special"
    return "daily"


def parse_pic_field(pic_str: str | None) -> list[str]:
    if not pic_str:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in str(pic_str).split(","):
        u = part.strip()
        if not u:
            continue
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("http://"):
            u = "https://" + u[len("http://") :]
        if not u.startswith("https://"):
            continue
        # Strip thumbnail suffix like !thumb.jpg / !800.jpg / !custom.jpg
        raw_u = re.sub(r"![A-Za-z0-9_.-]+$", "", u)
        fname = raw_u.rsplit("/", 1)[-1]
        if fname in CHROME_ASSET_IDS:
            continue
        if raw_u not in seen:
            seen.add(raw_u)
            out.append(raw_u)
    return out


async def init_xueqiu_session(http: aiohttp.ClientSession, user_id: str) -> None:
    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": f"https://xueqiu.com/u/{user_id}",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    async with http.get("https://xueqiu.com/", headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        await resp.read()


async def cmd_fetch_index(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir) if args.out_dir else (DEFAULT_BASE_DIR / "index" / f"{sanitize_filename(args.label)}_xueqiu_{args.since[:4]}_now")
    out_dir.mkdir(parents=True, exist_ok=True)

    since_ms = parse_since_ms(args.since) if args.since else 0
    articles_by_id: dict[str, dict[str, Any]] = {}
    json_path = out_dir / "articles.json"

    if json_path.exists() and not args.reset:
        existing = json.loads(json_path.read_text(encoding="utf-8"))
        for a in existing.get("articles") or []:
            articles_by_id[str(a["id"])] = a
        print(f"Loaded {len(articles_by_id)} cached articles from {json_path}", flush=True)

    base_headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": f"https://xueqiu.com/u/{args.user_id}",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }

    async with aiohttp.ClientSession() as http:
        await init_xueqiu_session(http, args.user_id)
        max_page = args.max_pages

        for page in range(args.start_page, max_page + 1):
            if page > args.start_page:
                await asyncio.sleep(random.uniform(args.delay_min, args.delay_max))

            url = f"https://www.xueqiu.com/statuses/original/timeline.json?user_id={args.user_id}&page={page}"
            data: dict[str, Any] | None = None
            for attempt in range(3):
                try:
                    async with http.get(url, headers=base_headers, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                        if resp.status == 200:
                            txt = await resp.text()
                            if txt.startswith("{"):
                                data = json.loads(txt)
                                break
                        print(f"  page {page} attempt {attempt+1} status={resp.status}", flush=True)
                except Exception as exc:
                    print(f"  page {page} attempt {attempt+1} err={exc}", flush=True)
                await asyncio.sleep(2.5 * (attempt + 1))
                await init_xueqiu_session(http, args.user_id)

            if not data:
                print(f"Stopping at page {page}: could not fetch JSON.", file=sys.stderr, flush=True)
                break

            api_max_page = int(data.get("maxPage") or max_page)
            items = data.get("list") or []
            if not items:
                print(f"Page {page}: empty list, done.", flush=True)
                break

            kept = 0
            older = 0
            newest_d = ""
            oldest_d = ""

            for item in items:
                created_ms = int(item.get("created_at") or 0)
                pub_date, pub_time = format_ms(created_ms)
                if not newest_d or pub_date > newest_d:
                    newest_d = pub_date
                if not oldest_d or pub_date < oldest_d:
                    oldest_d = pub_date
                if since_ms and created_ms and created_ms < since_ms:
                    older += 1
                    continue
                kept += 1
                aid = str(item.get("id"))
                title = html.unescape(str(item.get("title") or "")).strip()
                desc = html.unescape(re.sub(r"<[^>]+>", " ", str(item.get("description") or ""))).strip()
                desc = re.sub(r"\s+", " ", desc)
                imgs = parse_pic_field(item.get("pic"))
                target = str(item.get("target") or f"/{args.user_id}/{aid}")
                full_url = f"https://xueqiu.com{target}" if target.startswith("/") else target
                cat = classify_article(title, desc)
                articles_by_id[aid] = {
                    "id": aid,
                    "publish_date": pub_date,
                    "publish_time": pub_time,
                    "created_at": created_ms,
                    "title": title,
                    "category": cat,
                    "view_count": int(item.get("view_count") or 0),
                    "pic_count": len(imgs),
                    "url": full_url,
                    "description": desc,
                    "images": imgs,
                }

            print(
                f"Page {page}/{min(max_page, api_max_page)}: got={len(items)} kept={kept} older={older} "
                f"range=[{oldest_d}..{newest_d}] unique={len(articles_by_id)}",
                flush=True,
            )

            if since_ms and kept == 0 and older == len(items):
                print(f"All items on page {page} are older than {args.since}, stopping.", flush=True)
                break
            if page >= api_max_page:
                break

    sorted_articles = sorted(
        articles_by_id.values(),
        key=lambda a: (int(a.get("created_at") or 0), str(a.get("id") or "")),
        reverse=True,
    )
    for seq, a in enumerate(sorted_articles, start=1):
        a["seq"] = seq

    # Stats by category
    cat_stats: dict[str, dict[str, int]] = {}
    for a in sorted_articles:
        c = a["category"]
        s = cat_stats.setdefault(c, {"articles": 0, "images": 0})
        s["articles"] += 1
        s["images"] += int(a["pic_count"])

    total_imgs = sum(int(a["pic_count"]) for a in sorted_articles)
    payload = {
        "meta": {
            "label": args.label,
            "user_id": args.user_id,
            "since": args.since,
            "total_articles": len(sorted_articles),
            "total_images": total_imgs,
            "category_stats": cat_stats,
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
        "articles": sorted_articles,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = out_dir / "articles.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["seq", "publish_date", "category", "pic_count", "view_count", "title", "id", "url", "description"],
        )
        w.writeheader()
        for a in sorted_articles:
            w.writerow({k: a.get(k, "") for k in w.fieldnames})

    print("\n=== INDEX SUMMARY ===", flush=True)
    print(f"Total articles (>= {args.since}): {len(sorted_articles)}", flush=True)
    print(f"Total images across all articles: {total_imgs}", flush=True)
    for c, st in sorted(cat_stats.items()):
        print(f"  category={c:8s}: articles={st['articles']:4d}, images={st['images']:6d}", flush=True)
    print(f"Saved JSON: {json_path}", flush=True)
    print(f"Saved CSV : {csv_path}", flush=True)
    return 0


def save_image_bytes(
    raw_bytes: bytes,
    out_dir: Path,
    index: int,
    min_width: int,
    min_height: int,
    jpeg_quality: int | None,
) -> dict[str, Any]:
    if raw_bytes[:5].lstrip().startswith((b"<svg", b"<?xml")):
        return {"status": "skipped", "reason": "svg"}
    try:
        with Image.open(io.BytesIO(raw_bytes)) as im:
            fmt = (im.format or "").upper()
            width, height = im.size
            if getattr(im, "is_animated", False) or fmt == "GIF":
                return {"status": "skipped", "reason": "gif", "width": width, "height": height}
            if width < min_width or height < min_height:
                return {"status": "skipped", "reason": f"too_small({width}x{height})", "width": width, "height": height}

            if jpeg_quality is not None and fmt == "PNG" and len(raw_bytes) > 250_000:
                # Optional compact high-quality JPEG re-encode for daily scoreboards to save disk space
                filename = f"{index:03d}.jpg"
                out_path = out_dir / filename
                im.convert("RGB").save(out_path, format="JPEG", quality=jpeg_quality, optimize=True)
                saved_fmt = f"JPEG_Q{jpeg_quality}"
            elif fmt not in ("PNG", "JPEG"):
                filename = f"{index:03d}.png"
                out_path = out_dir / filename
                conv = im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB")
                conv.save(out_path, format="PNG")
                saved_fmt = "PNG"
            else:
                ext = ".jpg" if fmt == "JPEG" else ".png"
                filename = f"{index:03d}{ext}"
                out_path = out_dir / filename
                out_path.write_bytes(raw_bytes)
                saved_fmt = fmt

            return {
                "status": "saved",
                "file": filename,
                "width": width,
                "height": height,
                "orig_format": fmt,
                "saved_format": saved_fmt,
                "bytes": out_path.stat().st_size,
            }
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


async def fetch_one_image(
    http: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    raw_url: str,
    use_800: bool,
) -> tuple[str, bytes | None, str]:
    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": "https://xueqiu.com/",
    }
    primary = f"{raw_url}!800.jpg" if use_800 else raw_url
    fallback = raw_url if use_800 else f"{raw_url}!800.jpg"

    async with sem:
        for u in (primary, fallback):
            for attempt in range(2):
                try:
                    async with http.get(u, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            if data:
                                return u, data, "OK"
                except Exception:
                    pass
                if attempt == 0:
                    await asyncio.sleep(0.5)
    return raw_url, None, "download_failed"


async def fetch_article_full_text(http: aiohttp.ClientSession, url: str) -> str:
    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": "https://xueqiu.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with http.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return ""
            page_html = await resp.text(errors="replace")
            doc = lxml_html.fromstring(page_html)
            for xp in (
                '//*[contains(@class,"article__bd__detail")]',
                '//article',
            ):
                nodes = doc.xpath(xp)
                if nodes:
                    lines = [ln.strip() for ln in nodes[0].itertext() if ln and ln.strip()]
                    if lines:
                        return "\n".join(lines)
    except Exception:
        pass
    return ""


async def cmd_download(args: argparse.Namespace) -> int:
    index_data = json.loads(Path(args.index).read_text(encoding="utf-8"))
    articles: list[dict[str, Any]] = index_data.get("articles") or []

    if args.category != "all":
        wanted = set(args.category.split(","))
        articles = [a for a in articles if a.get("category") in wanted]

    if args.since:
        articles = [a for a in articles if (a.get("publish_date") or "") >= args.since]
    if args.until:
        articles = [a for a in articles if (a.get("publish_date") or "") <= args.until]
    if args.include:
        pats = [p.strip().lower() for p in args.include if p.strip()]
        articles = [
            a
            for a in articles
            if any(p in (a.get("title", "") + " " + a.get("description", "")).lower() for p in pats)
        ]

    # Always prioritize special (专场/交割单/冠军/闭幕) before daily & signup!
    priority_order = {"special": 0, "daily": 1, "signup": 2}
    articles.sort(
        key=lambda a: (
            priority_order.get(a.get("category", "daily"), 1) if args.prioritize_special else 0,
            -int(a.get("created_at") or 0),
        )
    )

    if args.limit:
        articles = articles[: args.limit]

    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.workers)

    done_count = 0
    skipped_existing = 0
    total_saved_imgs = 0
    total_saved_bytes = 0
    stopped_low_disk = False

    connector = aiohttp.TCPConnector(limit=args.workers * 2)
    async with aiohttp.ClientSession(connector=connector) as http:
        if args.fetch_full_text:
            await init_xueqiu_session(http, str(index_data.get("meta", {}).get("user_id") or "1997137383"))

        for i, art in enumerate(articles, start=1):
            # Disk space safety check before every article
            free_gb = shutil.disk_usage(out_root.anchor or str(out_root)).free / 1e9
            if free_gb < args.min_free_gb:
                print(
                    f"\n[SAFETY BRAKE] Free disk space ({free_gb:.2f} GB) dropped below --min-free-gb={args.min_free_gb:.2f} GB. Stopping cleanly.",
                    file=sys.stderr,
                    flush=True,
                )
                stopped_low_disk = True
                break

            cat = art.get("category", "daily")
            pub_date = art.get("publish_date") or "0000-00-00"
            title = art.get("title") or f"post_{art.get('id')}"
            aid = str(art.get("id"))
            cat_folder = {"special": "01_专场与交割单", "daily": "02_每日赛况", "signup": "03_报名与公告"}.get(cat, "02_每日赛况")
            folder_name = f"{pub_date}_{sanitize_filename(title, max_len=48)}_{aid}"
            art_dir = out_root / cat_folder / folder_name
            meta_path = art_dir / "meta.json"

            if meta_path.exists() and not args.force:
                try:
                    existing = json.loads(meta_path.read_text(encoding="utf-8"))
                    if existing.get("status") == "OK":
                        skipped_existing += 1
                        if i <= 10 or i % 25 == 0 or i == len(articles):
                            print(f"[{i}/{len(articles)}] skip_done | {cat} | {pub_date} | {title[:36]}", flush=True)
                        continue
                except Exception:
                    pass

            art_dir.mkdir(parents=True, exist_ok=True)
            text_content = art.get("description") or ""
            if args.fetch_full_text and (cat == "special" or args.full_text_all):
                full_txt = await fetch_article_full_text(http, art.get("url", ""))
                if full_txt and len(full_txt) > len(text_content):
                    text_content = full_txt
            (art_dir / "content.txt").write_text(
                f"{title}\n发布时间: {art.get('publish_time', pub_date)}\n链接: {art.get('url', '')}\n\n{text_content}\n",
                encoding="utf-8",
            )

            imgs = art.get("images") or []
            use_800 = bool(args.daily_800 and cat != "special")
            jpeg_q = args.daily_jpeg_quality if (args.daily_jpeg_quality and cat != "special") else None

            if args.text_only:
                saved_count = 0
                skipped_count = 0
                error_count = 0
                art_bytes = 0
                img_records: list[dict[str, Any]] = []
            else:
                tasks = [fetch_one_image(http, sem, u, use_800=use_800) for u in imgs]
                fetched = await asyncio.gather(*tasks)

                saved_count = 0
                skipped_count = 0
                error_count = 0
                art_bytes = 0
                img_records = []

                for used_url, raw_bytes, st in fetched:
                    if not raw_bytes:
                        error_count += 1
                        img_records.append({"url": used_url, "status": "error", "reason": st})
                        continue
                    rec = save_image_bytes(
                        raw_bytes=raw_bytes,
                        out_dir=art_dir,
                        index=saved_count + 1,
                        min_width=args.min_width,
                        min_height=args.min_height,
                        jpeg_quality=jpeg_q,
                    )
                    rec["url"] = used_url
                    img_records.append(rec)
                    if rec["status"] == "saved":
                        saved_count += 1
                        art_bytes += int(rec.get("bytes") or 0)
                    elif rec["status"] == "skipped":
                        skipped_count += 1
                    else:
                        error_count += 1

            meta_out = {
                "status": "OK",
                "id": aid,
                "category": cat,
                "title": title,
                "publish_date": pub_date,
                "publish_time": art.get("publish_time", ""),
                "view_count": art.get("view_count", 0),
                "url": art.get("url", ""),
                "total_img_urls": len(imgs),
                "saved_images": saved_count,
                "skipped_images": skipped_count,
                "error_images": error_count,
                "bytes": art_bytes,
                "downloaded_at": dt.datetime.now().isoformat(timespec="seconds"),
                "images": img_records,
            }
            meta_path.write_text(json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")

            done_count += 1
            total_saved_imgs += saved_count
            total_saved_bytes += art_bytes

            if done_count <= 15 or done_count % 10 == 0 or i == len(articles):
                print(
                    f"[{i}/{len(articles)}] OK | {cat:7s} | {pub_date} | {title[:32]} | "
                    f"imgs={saved_count}/{len(imgs)} err={error_count} | "
                    f"batch={total_saved_bytes/1e6:.1f}MB | free={free_gb:.2f}GB",
                    flush=True,
                )

    print(
        f"\n=== DOWNLOAD COMPLETE ===\n"
        f"Newly downloaded articles: {done_count} (skipped already done: {skipped_existing})\n"
        f"Newly saved images: {total_saved_imgs} ({total_saved_bytes/1e6:.1f} MB)\n"
        f"Output root: {out_root}\n"
        f"Stopped due to low disk: {stopped_low_disk}",
        flush=True,
    )
    return 2 if stopped_low_disk else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Xueqiu official column bulk indexer & image archiver.")
    sub = p.add_subparsers(dest="command", required=True)

    p_idx = sub.add_parser("fetch-index", help="Fetch full timeline index back to --since.")
    p_idx.add_argument("--user-id", default="1997137383", help="Xueqiu user_id (default: 顽主杯实盘大赛 1997137383).")
    p_idx.add_argument("--label", default="顽主杯实盘大赛")
    p_idx.add_argument("--since", default="2022-01-01")
    p_idx.add_argument("--start-page", type=int, default=1)
    p_idx.add_argument("--max-pages", type=int, default=50)
    p_idx.add_argument("--delay-min", type=float, default=0.8)
    p_idx.add_argument("--delay-max", type=float, default=1.4)
    p_idx.add_argument("--out-dir")
    p_idx.add_argument("--reset", action="store_true")

    p_dl = sub.add_parser("download", help="Bulk download articles & images from an index JSON.")
    p_dl.add_argument("--index", required=True, help="Path to articles.json.")
    p_dl.add_argument("--out-dir", required=True, help="Destination directory.")
    p_dl.add_argument("--category", default="all", help="all, or comma-separated subset of special,daily,signup.")
    p_dl.add_argument("--since")
    p_dl.add_argument("--until")
    p_dl.add_argument("--include", nargs="*")
    p_dl.add_argument("--limit", type=int)
    p_dl.add_argument("--workers", type=int, default=10)
    p_dl.add_argument("--min-width", type=int, default=180)
    p_dl.add_argument("--min-height", type=int, default=100)
    p_dl.add_argument("--min-free-gb", type=float, default=2.0, help="Safety stop if disk free GB falls below this.")
    p_dl.add_argument("--prioritize-special", action="store_true", default=True)
    p_dl.add_argument("--daily-800", action="store_true", help="Use !800.jpg for non-special daily posts to save disk space.")
    p_dl.add_argument("--daily-jpeg-quality", type=int, help="Optional JPEG quality (e.g. 85) for large daily PNGs.")
    p_dl.add_argument("--fetch-full-text", action="store_true", help="Also fetch full article body text for special posts.")
    p_dl.add_argument("--full-text-all", action="store_true", help="Fetch full body text for all categories.")
    p_dl.add_argument("--text-only", action="store_true")
    p_dl.add_argument("--force", action="store_true")

    return p


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    if args.command == "fetch-index":
        return asyncio.run(cmd_fetch_index(args))
    if args.command == "download":
        return asyncio.run(cmd_download(args))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
