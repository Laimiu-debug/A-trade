#!/usr/bin/env python3
"""WeChat Official Account historical article indexer & settlement-statement image downloader.

Uses the authenticated WeChat MP backend API (`mp.weixin.qq.com`) via a user's own
subscription account login (browser QR scan captured over localhost CDP on an isolated
browser profile) to search/list historical articles and batch-download original images.

All session files, article indexes, and downloaded images are stored under
`artifacts/wechat-jiaogedan/` (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import html
import io
import json
import os
import random
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp
from lxml import html as lxml_html
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = REPO_ROOT / "artifacts" / "wechat-jiaogedan"
SESSION_PATH = BASE_DIR / "session.json"
PROFILE_DIR = BASE_DIR / "browser_profile"
INDEX_DIR = BASE_DIR / "index"
DOWNLOAD_DIR = BASE_DIR / "downloads"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def ensure_dirs() -> None:
    for d in (BASE_DIR, PROFILE_DIR, INDEX_DIR, DOWNLOAD_DIR):
        d.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str, max_len: int = 64) -> str:
    cleaned = html.unescape(name or "").strip()
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "untitled"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .")
    return cleaned


def parse_since_ts(since_str: str) -> int:
    parsed = dt.datetime.strptime(since_str, "%Y-%m-%d")
    return int(parsed.timestamp())


def format_ts(ts: int | float | None) -> str:
    if not ts:
        return ""
    try:
        return dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return ""


def format_date(ts: int | float | None) -> str:
    if not ts:
        return "0000-00-00"
    try:
        return dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except (OSError, ValueError, OverflowError):
        return "0000-00-00"


def normalize_article_url(url: str) -> str:
    if not url:
        return ""
    u = html.unescape(url).strip()
    if u.startswith("http://mp.weixin.qq.com/"):
        u = "https://" + u[len("http://") :]
    return u


def article_dedup_key(item: dict[str, Any]) -> str:
    aid = str(item.get("aid") or "").strip()
    if aid:
        return f"aid:{aid}"
    link = normalize_article_url(str(item.get("link") or ""))
    if link:
        parsed = urlparse(link)
        qs = parse_qs(parsed.query)
        if "__biz" in qs and "mid" in qs and "idx" in qs:
            return f"biz:{qs['__biz'][0]}:{qs['mid'][0]}:{qs['idx'][0]}"
        return f"url:{link.split('#')[0]}"
    return f"title:{item.get('title','')}:{item.get('update_time','')}"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_browser_exe(preferred: str | None = None) -> str:
    if preferred and Path(preferred).exists():
        return preferred
    for path in BROWSER_CANDIDATES:
        if Path(path).exists():
            return path
    raise FileNotFoundError(
        "Could not find Microsoft Edge or Google Chrome. Pass --browser <path>."
    )


def load_session() -> dict[str, Any]:
    if not SESSION_PATH.exists():
        raise FileNotFoundError(
            f"Session file not found at {SESSION_PATH}. Run `login` first."
        )
    data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    if not data.get("token") or not data.get("cookie"):
        raise ValueError(f"Invalid session data in {SESSION_PATH}. Run `login` again.")
    return data


def save_session(token: str, cookie: str, user_agent: str, extra: dict[str, Any] | None = None) -> None:
    ensure_dirs()
    payload: dict[str, Any] = {
        "token": token,
        "cookie": cookie,
        "user_agent": user_agent or DEFAULT_UA,
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if extra:
        payload.update(extra)
    SESSION_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def mp_headers(session: dict[str, Any]) -> dict[str, str]:
    return {
        "User-Agent": session.get("user_agent") or DEFAULT_UA,
        "Cookie": session["cookie"],
        "Referer": f"https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit&isNew=1&type=10&token={session['token']}&lang=zh_CN",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }


async def cdp_send(ws: aiohttp.ClientWebSocketResponse, msg_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    await ws.send_json({"id": msg_id, "method": method, "params": params or {}})
    while True:
        msg = await ws.receive(timeout=15)
        if msg.type == aiohttp.WSMsgType.TEXT:
            data = json.loads(msg.data)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(f"CDP error on {method}: {data['error']}")
                return data.get("result", {})
        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            raise RuntimeError(f"CDP websocket closed while waiting for {method}")


async def cmd_login(args: argparse.Namespace) -> int:
    ensure_dirs()
    if args.token and args.cookie:
        save_session(args.token.strip(), args.cookie.strip(), args.user_agent or DEFAULT_UA, {"source": "manual"})
        print(f"Saved manual session to {SESSION_PATH}")
        return 0

    browser_exe = find_browser_exe(args.browser)
    port = find_free_port()
    cmd = [
        browser_exe,
        f"--user-data-dir={PROFILE_DIR}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1100,820",
        "https://mp.weixin.qq.com/",
    ]
    print(f"Launching browser for WeChat MP login: {browser_exe}", flush=True)
    print("Please scan the QR code on https://mp.weixin.qq.com/ with WeChat and select your subscription account.", flush=True)

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + args.timeout
    token: str | None = None
    target_ws_url: str | None = None
    final_url: str = ""

    try:
        async with aiohttp.ClientSession() as http:
            # Wait for DevTools endpoint to become ready
            cdp_ready = False
            while time.time() < deadline:
                if proc.poll() is not None:
                    print("Browser window was closed before login completed.", file=sys.stderr)
                    return 1
                try:
                    async with http.get(f"http://127.0.0.1:{port}/json/list", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        if resp.status == 200:
                            cdp_ready = True
                            targets = await resp.json()
                            for t in targets:
                                if t.get("type") != "page":
                                    continue
                                url = t.get("url", "")
                                if "mp.weixin.qq.com" in url:
                                    m = re.search(r"[?&]token=(\d+)", url)
                                    if m:
                                        token = m.group(1)
                                        target_ws_url = t.get("webSocketDebuggerUrl")
                                        final_url = url
                                        break
                except Exception:
                    pass
                if token and target_ws_url:
                    break
                await asyncio.sleep(1.0)

            if not token or not target_ws_url:
                if not cdp_ready:
                    print("Could not connect to browser DevTools port.", file=sys.stderr)
                else:
                    print(f"Timed out after {args.timeout}s waiting for WeChat MP login.", file=sys.stderr)
                return 1

            # Give cookies a moment to settle after redirect
            await asyncio.sleep(1.0)

            async with http.ws_connect(target_ws_url, max_msg_size=8 * 1024 * 1024) as ws:
                cookie_res = await cdp_send(
                    ws,
                    1,
                    "Network.getCookies",
                    {"urls": ["https://mp.weixin.qq.com", "https://weixin.qq.com"]},
                )
                ua_res = await cdp_send(
                    ws,
                    2,
                    "Runtime.evaluate",
                    {"expression": "navigator.userAgent", "returnByValue": True},
                )

            cookies = cookie_res.get("cookies", [])
            mp_cookies = [
                f"{c['name']}={c['value']}"
                for c in cookies
                if "weixin.qq.com" in c.get("domain", "")
            ]
            cookie_header = "; ".join(mp_cookies)
            ua = ua_res.get("result", {}).get("value") or DEFAULT_UA

            save_session(
                token=token,
                cookie=cookie_header,
                user_agent=ua,
                extra={
                    "source": "browser_cdp",
                    "cookie_count": len(mp_cookies),
                    "home_url": final_url.split("token=")[0] + "token=***",
                },
            )
            print(f"LOGIN_SUCCESS cookie_count={len(mp_cookies)} token_len={len(token)} saved={SESSION_PATH}", flush=True)
            return 0
    finally:
        if not args.keep_browser and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


async def api_search_biz(http: aiohttp.ClientSession, session: dict[str, Any], query: str, count: int = 10) -> list[dict[str, Any]]:
    url = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
    params = {
        "action": "search_biz",
        "begin": "0",
        "count": str(count),
        "query": query,
        "token": session["token"],
        "lang": "zh_CN",
        "f": "json",
        "ajax": "1",
    }
    async with http.get(url, headers=mp_headers(session), params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        text = await resp.text()
        data = json.loads(text)
    ret = data.get("base_resp", {}).get("ret")
    if ret != 0:
        raise RuntimeError(f"searchbiz API error: {data.get('base_resp')}")
    return data.get("list", [])


def parse_appmsgpublish_response(data: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    """Parse `/cgi-bin/appmsgpublish` JSON response.

    Returns `(articles, total_publish_count, raw_publish_items_on_page)`.
    """
    publish_page_raw = data.get("publish_page")
    if not publish_page_raw:
        return [], 0, 0
    publish_page = json.loads(publish_page_raw) if isinstance(publish_page_raw, str) else publish_page_raw
    total_count = int(publish_page.get("total_count", 0) or 0)
    publish_list = publish_page.get("publish_list") or publish_page.get("item") or []
    articles: list[dict[str, Any]] = []
    for pub in publish_list:
        info_raw = pub.get("publish_info")
        if info_raw:
            info = json.loads(info_raw) if isinstance(info_raw, str) else info_raw
        else:
            info = pub
        sent_time = (info.get("sent_info") or {}).get("time") or 0
        for item in info.get("appmsgex") or []:
            ts = item.get("update_time") or item.get("create_time") or sent_time or 0
            articles.append(
                {
                    "aid": str(item.get("aid") or ""),
                    "title": html.unescape(str(item.get("title") or "")),
                    "link": normalize_article_url(str(item.get("link") or "")),
                    "digest": html.unescape(str(item.get("digest") or "")),
                    "cover": str(item.get("cover") or ""),
                    "itemidx": item.get("itemidx", 1),
                    "create_time": int(item.get("create_time") or ts or 0),
                    "update_time": int(ts or 0),
                    "publish_date": format_date(ts),
                    "publish_time": format_ts(ts),
                    "is_deleted": bool(item.get("is_deleted", False)),
                }
            )
    return articles, total_count, len(publish_list)


def parse_appmsg_response(data: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    """Parse `/cgi-bin/appmsg` JSON response."""
    total_count = int(data.get("app_msg_cnt", 0) or 0)
    raw_list = data.get("app_msg_list") or []
    articles: list[dict[str, Any]] = []
    for item in raw_list:
        ts = item.get("update_time") or item.get("create_time") or 0
        articles.append(
            {
                "aid": str(item.get("aid") or ""),
                "title": html.unescape(str(item.get("title") or "")),
                "link": normalize_article_url(str(item.get("link") or "")),
                "digest": html.unescape(str(item.get("digest") or "")),
                "cover": str(item.get("cover") or ""),
                "itemidx": item.get("itemidx", 1),
                "create_time": int(item.get("create_time") or ts or 0),
                "update_time": int(ts or 0),
                "publish_date": format_date(ts),
                "publish_time": format_ts(ts),
                "is_deleted": False,
            }
        )
    return articles, total_count, len(raw_list)


async def api_fetch_page(
    http: aiohttp.ClientSession,
    session: dict[str, Any],
    fakeid: str,
    begin: int,
    count: int,
    query: str = "",
    endpoint: str = "appmsgpublish",
) -> tuple[list[dict[str, Any]], int, int, dict[str, Any]]:
    if endpoint == "appmsgpublish":
        url = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish"
        is_search = bool(query)
        params = {
            "sub": "search" if is_search else "list",
            "search_field": "7" if is_search else "null",
            "begin": str(begin),
            "count": str(count),
            "query": query,
            "fakeid": fakeid,
            "type": "101_1",
            "free_publish_type": "1",
            "sub_action": "list_ex",
            "token": session["token"],
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
        }
    else:
        url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
        params = {
            "action": "list_ex",
            "begin": str(begin),
            "count": str(count),
            "fakeid": fakeid,
            "type": "9",
            "query": query,
            "token": session["token"],
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
        }

    async with http.get(url, headers=mp_headers(session), params=params, timeout=aiohttp.ClientTimeout(total=25)) as resp:
        text = await resp.text()
        data = json.loads(text)

    base_resp = data.get("base_resp") or {}
    ret = base_resp.get("ret")
    if ret != 0:
        raise RuntimeError(f"{endpoint} API error ret={ret} err_msg={base_resp.get('err_msg')}")

    if endpoint == "appmsgpublish":
        articles, total, page_units = parse_appmsgpublish_response(data)
    else:
        articles, total, page_units = parse_appmsg_response(data)
    return articles, total, page_units, data


def save_article_index(account_dir: Path, meta: dict[str, Any], articles_by_key: dict[str, dict[str, Any]]) -> None:
    account_dir.mkdir(parents=True, exist_ok=True)
    sorted_articles = sorted(
        articles_by_key.values(),
        key=lambda x: (int(x.get("update_time") or 0), x.get("title") or ""),
        reverse=True,
    )
    for idx, art in enumerate(sorted_articles, start=1):
        art["seq"] = idx

    payload = {
        "meta": meta,
        "count": len(sorted_articles),
        "articles": sorted_articles,
    }
    (account_dir / "articles.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    csv_path = account_dir / "articles.csv"
    fieldnames = [
        "seq",
        "publish_date",
        "publish_time",
        "title",
        "matched_queries",
        "digest",
        "link",
        "aid",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for art in sorted_articles:
            writer.writerow(
                {
                    "seq": art["seq"],
                    "publish_date": art.get("publish_date", ""),
                    "publish_time": art.get("publish_time", ""),
                    "title": art.get("title", ""),
                    "matched_queries": "|".join(art.get("matched_queries") or []),
                    "digest": art.get("digest", ""),
                    "link": art.get("link", ""),
                    "aid": art.get("aid", ""),
                }
            )


async def cmd_search_biz(args: argparse.Namespace) -> int:
    session = load_session()
    async with aiohttp.ClientSession() as http:
        results = await api_search_biz(http, session, args.query, count=args.count)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


async def cmd_fetch_index(args: argparse.Namespace) -> int:
    ensure_dirs()
    session = load_session()
    since_ts = parse_since_ts(args.since) if args.since else 0

    account_name = args.account
    account_dir = INDEX_DIR / sanitize_filename(account_name)
    account_dir.mkdir(parents=True, exist_ok=True)
    index_json_path = account_dir / "articles.json"

    articles_by_key: dict[str, dict[str, Any]] = {}
    meta: dict[str, Any] = {
        "account_name": account_name,
        "since": args.since,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }

    if index_json_path.exists() and not args.reset:
        existing = json.loads(index_json_path.read_text(encoding="utf-8"))
        meta.update(existing.get("meta") or {})
        for art in existing.get("articles") or []:
            articles_by_key[article_dedup_key(art)] = art
        print(f"Loaded {len(articles_by_key)} existing indexed articles for {account_name}", flush=True)

    async with aiohttp.ClientSession() as http:
        fakeid = args.fakeid or meta.get("fakeid")
        if not fakeid:
            biz_list = await api_search_biz(http, session, account_name, count=10)
            if not biz_list:
                print(f"No Official Account found matching: {account_name}", file=sys.stderr)
                return 1
            chosen = next((b for b in biz_list if b.get("nickname") == account_name), biz_list[0])
            fakeid = chosen["fakeid"]
            meta["fakeid"] = fakeid
            meta["nickname"] = chosen.get("nickname", account_name)
            meta["alias"] = chosen.get("alias", "")
            meta["signature"] = chosen.get("signature", "")
            print(
                f"Matched account: nickname={meta['nickname']} alias={meta['alias']} fakeid={fakeid}",
                flush=True,
            )
            if len(biz_list) > 1 and chosen.get("nickname") != account_name:
                print("Other candidates:", [b.get("nickname") for b in biz_list[:5]], flush=True)

        endpoint = args.endpoint
        queries: list[str] = []
        if args.mode in ("search", "both"):
            queries.extend([q.strip() for q in (args.keywords or []) if q.strip()])
        if args.mode in ("full", "both"):
            queries.append("")

        if not queries:
            print("No queries to run. Pass --keywords or --mode full.", file=sys.stderr)
            return 1

        request_count = 0
        rate_limited = False

        for q in queries:
            q_label = q if q else "<ALL>"
            begin = args.start_begin if (not q and args.start_begin) else 0
            page_size = args.page_size
            pages_fetched = 0
            print(f"\n--- Fetching [{meta.get('nickname', account_name)}] query={q_label} endpoint={endpoint} ---", flush=True)

            while pages_fetched < args.max_pages:
                if request_count > 0:
                    delay = random.uniform(args.delay_min, args.delay_max)
                    await asyncio.sleep(delay)
                request_count += 1

                try:
                    page_articles, total_count, page_units, _raw = await api_fetch_page(
                        http,
                        session,
                        fakeid=fakeid,
                        begin=begin,
                        count=page_size,
                        query=q,
                        endpoint=endpoint,
                    )
                except RuntimeError as exc:
                    err_str = str(exc)
                    # Auto-fallback from appmsgpublish to appmsg on first page if needed
                    if pages_fetched == 0 and endpoint == "appmsgpublish" and "200013" not in err_str and "200003" not in err_str:
                        print(f"appmsgpublish failed ({err_str}), falling back to appmsg...", flush=True)
                        endpoint = "appmsg"
                        try:
                            page_articles, total_count, page_units, _raw = await api_fetch_page(
                                http,
                                session,
                                fakeid=fakeid,
                                begin=begin,
                                count=min(page_size, 5),
                                query=q,
                                endpoint=endpoint,
                            )
                        except RuntimeError as exc2:
                            print(f"Stopped on query={q_label} begin={begin}: {exc2}", file=sys.stderr, flush=True)
                            if "200013" in str(exc2):
                                rate_limited = True
                            break
                    else:
                        print(f"Stopped on query={q_label} begin={begin}: {exc}", file=sys.stderr, flush=True)
                        if "200013" in err_str:
                            rate_limited = True
                        break

                if page_units == 0 and not page_articles:
                    print(f"  begin={begin}: empty page (total_count={total_count}), done.", flush=True)
                    break

                kept_in_range = 0
                older_count = 0
                newest_date = ""
                oldest_date = ""

                for art in page_articles:
                    ts = int(art.get("update_time") or 0)
                    d = art.get("publish_date", "")
                    if d and (not newest_date or d > newest_date):
                        newest_date = d
                    if d and (not oldest_date or d < oldest_date):
                        oldest_date = d
                    if since_ts and ts and ts < since_ts:
                        older_count += 1
                        continue
                    kept_in_range += 1
                    key = article_dedup_key(art)
                    if key in articles_by_key:
                        existing_art = articles_by_key[key]
                        mq = set(existing_art.get("matched_queries") or [])
                        if q:
                            mq.add(q)
                        existing_art["matched_queries"] = sorted(mq)
                    else:
                        art["matched_queries"] = [q] if q else []
                        articles_by_key[key] = art

                pages_fetched += 1
                print(
                    f"  page {pages_fetched} begin={begin} total={total_count} "
                    f"got={len(page_articles)} kept={kept_in_range} older={older_count} "
                    f"range=[{oldest_date}..{newest_date}] unique_total={len(articles_by_key)}",
                    flush=True,
                )

                meta["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
                if not q:
                    meta["full_last_begin"] = begin + page_units
                    meta["full_total_count"] = total_count
                save_article_index(account_dir, meta, articles_by_key)

                if since_ts and page_articles and kept_in_range == 0 and older_count == len(page_articles):
                    print(f"  All articles on page older than {args.since}, stopping pagination for {q_label}.", flush=True)
                    break

                step = page_units if page_units > 0 else page_size
                begin += step
                if total_count and begin >= total_count:
                    print(f"  Reached total_count={total_count}, done with {q_label}.", flush=True)
                    break

            if rate_limited:
                print("Hit WeChat frequency limit (ret=200013). Saved all progress so far.", file=sys.stderr, flush=True)
                break

    save_article_index(account_dir, meta, articles_by_key)
    print(f"\nSaved {len(articles_by_key)} unique articles to {account_dir / 'articles.csv'}", flush=True)
    return 2 if rate_limited else 0


def extract_article_meta_from_html(page_html: str) -> tuple[str, str, int]:
    """Extract (title, publish_date, publish_ts) directly from WeChat article HTML."""
    title = ""
    for pat in (
        r'var\s+msg_title\s*=\s*[\'"]([^\'"]+)[\'"]',
        r'property="og:title"\s+content="([^"]+)"',
        r'content="([^"]+)"\s+property="og:title"',
    ):
        m = re.search(pat, page_html)
        if m:
            title = html.unescape(m.group(1)).strip()
            break
    if not title:
        try:
            doc = lxml_html.fromstring(page_html)
            nodes = doc.xpath('//*[@id="activity-name"]')
            if nodes:
                title = "".join(nodes[0].itertext()).strip()
        except Exception:
            pass

    pub_ts = 0
    for pat in (
        r'var\s+ct\s*=\s*[\'"]?(\d{9,11})[\'"]?',
        r'create_time\s*:\s*JsDecode\([\'"](\d{9,11})[\'"]\)',
        r'create_time\s*:\s*[\'"]?(\d{9,11})[\'"]?',
    ):
        m = re.search(pat, page_html)
        if m:
            pub_ts = int(m.group(1))
            break

    return title, format_date(pub_ts) if pub_ts else "0000-00-00", pub_ts


def extract_images_and_text(page_html: str) -> tuple[list[str], str, str]:
    """Extract ordered image URLs, plain text, and status from WeChat article HTML.

    Handles both regular long-form articles (`#js_content`) and WeChat image posts
    ("小绿书" `window.picture_page_info_list`).
    """
    if "环境异常" in page_html and "完成验证" in page_html:
        return [], "", "VERIFICATION_REQUIRED"
    if "该内容已被发布者删除" in page_html or "此内容因违规无法查看" in page_html:
        return [], "", "DELETED_OR_UNAVAILABLE"

    urls: list[str] = []
    seen: set[str] = set()

    def add_url(raw: str) -> None:
        if not raw:
            return
        u = html.unescape(raw).strip()
        u = u.replace(r"\x26amp;", "&").replace(r"\x26", "&").replace(r"\/", "/")
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("http://"):
            u = "https://" + u[len("http://") :]
        if not u.startswith("https://"):
            return
        # Skip non-content avatars/tiny static assets
        if "res.wx.qq.com" in u:
            return
        if u not in seen:
            seen.add(u)
            urls.append(u)

    doc = lxml_html.fromstring(page_html)
    content_nodes = doc.xpath('//*[@id="js_content"]')
    plain_text = ""
    if content_nodes:
        content_el = content_nodes[0]
        plain_text = "\n".join(
            line.strip() for line in content_el.itertext() if line and line.strip()
        )
        for img in content_el.xpath(".//img"):
            src = img.get("data-src") or img.get("src") or ""
            add_url(src)

    # Also support WeChat image-share posts ("小绿书" picture_page_info_list)
    pic_list_match = re.search(
        r"picture_page_info_list\s*=\s*\[(.*?)\]\.slice",
        page_html,
        flags=re.DOTALL,
    )
    if pic_list_match:
        block = pic_list_match.group(1)
        for m in re.finditer(r"cdn_url\s*:\s*['\"]([^'\"]+)['\"]", block):
            add_url(m.group(1))
        if not plain_text:
            desc_nodes = doc.xpath('//*[@id="js_image_desc"]')
            if desc_nodes:
                plain_text = "\n".join(
                    line.strip() for line in desc_nodes[0].itertext() if line and line.strip()
                )

    return urls, plain_text, "OK"


def upgrade_wechat_image_url(url: str) -> str:
    """Prefer original lossless resolution when downloading mmbiz.qpic.cn images."""
    # Replace /640? with /0? only if wx_fmt isn't svg/gif
    if "mmbiz.qpic.cn" in url and "/640?" in url:
        return url.replace("/640?", "/0?", 1)
    return url


def process_and_save_image(
    raw_bytes: bytes,
    out_dir: Path,
    index: int,
    min_width: int,
    min_height: int,
    convert_webp_to_png: bool,
    skip_gif: bool,
) -> dict[str, Any]:
    if raw_bytes[:5].lstrip().startswith((b"<svg", b"<?xml")):
        return {"status": "skipped", "reason": "svg"}
    try:
        with Image.open(io.BytesIO(raw_bytes)) as im:
            fmt = (im.format or "").upper()
            width, height = im.size
            is_animated = getattr(im, "is_animated", False)
            if skip_gif and (fmt == "GIF" or is_animated):
                return {"status": "skipped", "reason": "gif", "width": width, "height": height}
            if width < min_width or height < min_height:
                return {
                    "status": "skipped",
                    "reason": f"too_small({width}x{height})",
                    "width": width,
                    "height": height,
                }

            if convert_webp_to_png and fmt not in ("PNG", "JPEG"):
                filename = f"{index:03d}.png"
                out_path = out_dir / filename
                converted = im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB")
                converted.save(out_path, format="PNG")
                saved_fmt = "PNG"
            else:
                ext = ".jpg" if fmt == "JPEG" else (".png" if fmt == "PNG" else f".{fmt.lower() or 'bin'}")
                filename = f"{index:03d}{ext}"
                out_path = out_dir / filename
                out_path.write_bytes(raw_bytes)
                saved_fmt = fmt or "RAW"

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


async def download_single_article(
    http: aiohttp.ClientSession,
    article: dict[str, Any],
    account_out_dir: Path,
    min_width: int,
    min_height: int,
    convert_webp_to_png: bool,
    skip_gif: bool,
    force: bool,
) -> dict[str, Any]:
    title = (article.get("title") or "").strip()
    pub_date = (article.get("publish_date") or "").strip() or (
        format_date(article.get("update_time")) if article.get("update_time") else ""
    )
    link = normalize_article_url(article.get("link") or "")

    # Fast skip if metadata was already known and folder is complete
    if title and pub_date and pub_date != "0000-00-00" and not force:
        pre_dir = account_out_dir / f"{pub_date}_{sanitize_filename(title, max_len=55)}"
        pre_meta = pre_dir / "meta.json"
        if pre_meta.exists():
            existing = json.loads(pre_meta.read_text(encoding="utf-8"))
            if existing.get("status") == "OK":
                return {
                    "title": title,
                    "publish_date": pub_date,
                    "status": "already_done",
                    "dir": str(pre_dir),
                    "saved_images": existing.get("saved_images", 0),
                }

    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": "https://mp.weixin.qq.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    async with http.get(link, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        page_html = await resp.text(errors="replace")

    img_urls, plain_text, status = extract_images_and_text(page_html)
    if status != "OK":
        return {"title": title, "status": status, "link": link}

    html_title, html_date, html_ts = extract_article_meta_from_html(page_html)
    if not title:
        title = html_title or "untitled"
    if not pub_date or pub_date == "0000-00-00":
        pub_date = html_date or "0000-00-00"
    if not article.get("publish_time") and html_ts:
        article["publish_time"] = format_ts(html_ts)

    folder_name = f"{pub_date}_{sanitize_filename(title, max_len=55)}"
    out_dir = account_out_dir / folder_name
    meta_path = out_dir / "meta.json"

    if meta_path.exists() and not force:
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
        if existing.get("status") == "OK":
            return {
                "title": title,
                "publish_date": pub_date,
                "status": "already_done",
                "dir": str(out_dir),
                "saved_images": existing.get("saved_images", 0),
            }

    out_dir.mkdir(parents=True, exist_ok=True)
    if plain_text:
        (out_dir / "content.txt").write_text(plain_text, encoding="utf-8")

    saved_count = 0
    skipped_count = 0
    error_count = 0
    img_records: list[dict[str, Any]] = []

    img_headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": "https://mp.weixin.qq.com/",
    }

    for raw_url in img_urls:
        fetch_url = upgrade_wechat_image_url(raw_url)
        raw_bytes: bytes | None = None
        for attempt_url in (fetch_url, raw_url) if fetch_url != raw_url else (raw_url,):
            try:
                async with http.get(attempt_url, headers=img_headers, timeout=aiohttp.ClientTimeout(total=30)) as iresp:
                    if iresp.status == 200:
                        raw_bytes = await iresp.read()
                        break
            except Exception:
                continue

        if not raw_bytes:
            error_count += 1
            img_records.append({"url": raw_url, "status": "error", "reason": "download_failed"})
            continue

        rec = process_and_save_image(
            raw_bytes=raw_bytes,
            out_dir=out_dir,
            index=saved_count + 1,
            min_width=min_width,
            min_height=min_height,
            convert_webp_to_png=convert_webp_to_png,
            skip_gif=skip_gif,
        )
        rec["url"] = raw_url
        img_records.append(rec)
        if rec["status"] == "saved":
            saved_count += 1
        elif rec["status"] == "skipped":
            skipped_count += 1
        else:
            error_count += 1

    meta_out = {
        "status": "OK",
        "title": title,
        "publish_date": pub_date,
        "publish_time": article.get("publish_time", ""),
        "link": link,
        "digest": article.get("digest", ""),
        "total_img_urls": len(img_urls),
        "saved_images": saved_count,
        "skipped_images": skipped_count,
        "error_images": error_count,
        "downloaded_at": dt.datetime.now().isoformat(timespec="seconds"),
        "images": img_records,
    }
    meta_path.write_text(json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "title": title,
        "publish_date": pub_date,
        "status": "OK",
        "dir": str(out_dir),
        "saved_images": saved_count,
        "skipped_images": skipped_count,
        "error_images": error_count,
    }


async def cmd_download_images(args: argparse.Namespace) -> int:
    ensure_dirs()
    account_name = args.account
    articles: list[dict[str, Any]] = []

    if args.urls_file:
        for line in Path(args.urls_file).read_text(encoding="utf-8").splitlines():
            u = line.strip()
            if u and u.startswith("http"):
                articles.append({"title": "", "link": u, "publish_date": ""})
    else:
        index_path = Path(args.index_file) if args.index_file else (INDEX_DIR / sanitize_filename(account_name) / "articles.json")
        if not index_path.exists():
            print(f"Index file not found: {index_path}", file=sys.stderr)
            return 1
        data = json.loads(index_path.read_text(encoding="utf-8"))
        articles = data.get("articles") or []

    # Optional filters
    if args.seqs:
        wanted_seqs = set()
        for part in args.seqs.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                wanted_seqs.update(range(int(a), int(b) + 1))
            elif part:
                wanted_seqs.add(int(part))
        articles = [a for a in articles if a.get("seq") in wanted_seqs]

    if args.include:
        patterns = [p.strip().lower() for p in args.include if p.strip()]
        articles = [
            a
            for a in articles
            if any(p in (a.get("title", "") + " " + a.get("digest", "")).lower() for p in patterns)
        ]

    if args.exclude:
        ex_patterns = [p.strip().lower() for p in args.exclude if p.strip()]
        articles = [
            a
            for a in articles
            if not any(p in (a.get("title", "")).lower() for p in ex_patterns)
        ]

    if args.since:
        articles = [a for a in articles if (a.get("publish_date") or "") >= args.since]

    if args.limit:
        articles = articles[: args.limit]

    if not articles:
        print("No articles matched the filter criteria.")
        return 0

    account_out_dir = DOWNLOAD_DIR / sanitize_filename(account_name)
    account_out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading images for {len(articles)} articles into {account_out_dir}...", flush=True)
    summary: list[dict[str, Any]] = []

    async with aiohttp.ClientSession() as http:
        for i, art in enumerate(articles, start=1):
            res = await download_single_article(
                http=http,
                article=art,
                account_out_dir=account_out_dir,
                min_width=args.min_width,
                min_height=args.min_height,
                convert_webp_to_png=not args.keep_webp,
                skip_gif=not args.include_gif,
                force=args.force,
            )
            summary.append(res)
            print(
                f"[{i}/{len(articles)}] {res.get('status')} | {art.get('publish_date','')} | "
                f"{art.get('title','')[:45]} | saved={res.get('saved_images', 0)} skipped={res.get('skipped_images', 0)}",
                flush=True,
            )
            if res.get("status") == "VERIFICATION_REQUIRED":
                print("WeChat returned a human-verification page. Stopping cleanly (will not bypass).", file=sys.stderr, flush=True)
                break
            if res.get("status") != "already_done" and i < len(articles):
                await asyncio.sleep(random.uniform(args.delay_min, args.delay_max))

    summary_path = account_out_dir / "download_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    total_saved = sum(int(r.get("saved_images") or 0) for r in summary)
    print(f"\nDone! Total images saved/present: {total_saved}. Summary: {summary_path}", flush=True)
    return 0


async def cmd_fetch_album(args: argparse.Namespace) -> int:
    """Fetch all articles in a public WeChat Official Account Album (合集)."""
    ensure_dirs()
    parsed = urlparse(args.url)
    qs = parse_qs(parsed.query)
    biz = args.biz or (qs.get("__biz") or [""])[0]
    album_id = args.album_id or (qs.get("album_id") or [""])[0]
    if not biz or not album_id:
        print("Could not parse __biz and album_id from album URL.", file=sys.stderr)
        return 1

    since_ts = parse_since_ts(args.since) if args.since else 0
    account_name = args.account
    account_dir = INDEX_DIR / sanitize_filename(account_name)
    account_dir.mkdir(parents=True, exist_ok=True)
    index_json_path = account_dir / "articles.json"

    articles_by_key: dict[str, dict[str, Any]] = {}
    meta: dict[str, Any] = {
        "account_name": account_name,
        "biz": biz,
        "album_id": album_id,
        "since": args.since,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if index_json_path.exists() and not args.reset:
        existing = json.loads(index_json_path.read_text(encoding="utf-8"))
        meta.update(existing.get("meta") or {})
        for art in existing.get("articles") or []:
            articles_by_key[article_dedup_key(art)] = art

    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": "https://mp.weixin.qq.com/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    begin_msgid = ""
    begin_itemidx = ""
    page = 0

    async with aiohttp.ClientSession() as http:
        while page < args.max_pages:
            if page > 0:
                await asyncio.sleep(random.uniform(args.delay_min, args.delay_max))
            params: dict[str, str] = {
                "action": "getalbum",
                "__biz": biz,
                "album_id": album_id,
                "count": str(args.page_size),
                "is_reverse": "0",
                "f": "json",
            }
            if begin_msgid:
                params["begin_msgid"] = begin_msgid
                params["begin_itemidx"] = begin_itemidx or "1"

            async with http.get(
                "https://mp.weixin.qq.com/mp/appmsgalbum",
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=25),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)

            base_resp = data.get("base_resp") or {}
            if base_resp.get("ret", 0) != 0:
                print(f"Album API error: {base_resp}", file=sys.stderr)
                break

            album_resp = data.get("getalbum_resp") or {}
            raw_list = album_resp.get("article_list") or []
            if isinstance(raw_list, dict):
                raw_list = [raw_list]
            if not raw_list:
                print("Album pagination finished (empty page).", flush=True)
                break

            kept = 0
            older = 0
            for item in raw_list:
                ts = int(item.get("create_time") or 0)
                if since_ts and ts and ts < since_ts:
                    older += 1
                    continue
                kept += 1
                art = {
                    "aid": f"{item.get('msgid', '')}_{item.get('itemidx', '1')}",
                    "title": html.unescape(str(item.get("title") or "")),
                    "link": normalize_article_url(str(item.get("url") or "")),
                    "digest": "",
                    "cover": str(item.get("cover_img_1_1") or ""),
                    "itemidx": int(item.get("itemidx") or 1),
                    "create_time": ts,
                    "update_time": ts,
                    "publish_date": format_date(ts),
                    "publish_time": format_ts(ts),
                    "matched_queries": [f"album:{album_id}"],
                }
                articles_by_key[article_dedup_key(art)] = art

            page += 1
            last = raw_list[-1]
            begin_msgid = str(last.get("msgid") or "")
            begin_itemidx = str(last.get("itemidx") or "1")
            continue_flag = str(album_resp.get("continue_flag") or "0")
            print(
                f"Album page {page}: got={len(raw_list)} kept={kept} older={older} "
                f"continue_flag={continue_flag} unique_total={len(articles_by_key)}",
                flush=True,
            )
            save_article_index(account_dir, meta, articles_by_key)

            if continue_flag != "1" or not begin_msgid:
                break
            if since_ts and kept == 0 and older == len(raw_list):
                break

    save_article_index(account_dir, meta, articles_by_key)
    print(f"Saved {len(articles_by_key)} articles to {account_dir / 'articles.csv'}", flush=True)
    return 0


async def cmd_inspect_urls(args: argparse.Namespace) -> int:
    """Inspect WeChat article URLs for embedded album IDs, linked WeChat articles, and image counts."""
    ensure_dirs()
    urls = [
        line.strip()
        for line in Path(args.urls_file).read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("http")
    ]
    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": "https://mp.weixin.qq.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    results: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as http:
        for i, u in enumerate(urls, start=1):
            if i > 1:
                await asyncio.sleep(random.uniform(1.0, 2.0))
            norm_u = normalize_article_url(u)
            parsed = urlparse(norm_u)
            qs = parse_qs(parsed.query)
            biz = (qs.get("__biz") or [""])[0]
            try:
                async with http.get(norm_u, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                    page_html = await resp.text(errors="replace")
                title, pub_date, _ts = extract_article_meta_from_html(page_html)
                img_urls, plain_text, status = extract_images_and_text(page_html)
                nick_m = re.search(r'var\s+nickname\s*=\s*htmlDecode\("([^"]+)"\)', page_html) or re.search(
                    r'id="js_name"[^>]*>(.*?)<', page_html, re.S
                )
                nickname = html.unescape(nick_m.group(1)).strip() if nick_m else ""
                # Extract album IDs & titles
                album_ids = sorted(set(re.findall(r'album_id["\':=\s\\]+(\d{10,})', page_html)))
                album_titles = [
                    html.unescape(re.sub(r"\s+", " ", t)).strip()
                    for t in re.findall(r'album__label-title[^>]*>(.*?)<', page_html, re.S)
                    if t.strip()
                ]
                # Extract embedded mp.weixin.qq.com/s links inside #js_content
                linked_articles: list[dict[str, str]] = []
                if status == "OK":
                    doc = lxml_html.fromstring(page_html)
                    seen_links: set[str] = set()
                    for a in doc.xpath('//*[@id="js_content"]//a[@href]'):
                        href = normalize_article_url(a.get("href") or "")
                        if "mp.weixin.qq.com/s" in href or "mp.weixin.qq.com/mp/appmsgalbum" in href:
                            if href not in seen_links:
                                seen_links.add(href)
                                linked_articles.append(
                                    {"text": "".join(a.itertext()).strip()[:80], "href": href}
                                )
                rec = {
                    "status": status,
                    "nickname": nickname,
                    "biz": biz,
                    "publish_date": pub_date,
                    "title": title,
                    "url": norm_u,
                    "image_count": len(img_urls),
                    "album_ids": album_ids,
                    "album_titles": album_titles,
                    "album_urls": [
                        f"https://mp.weixin.qq.com/mp/appmsgalbum?__biz={biz}&action=getalbum&album_id={aid}"
                        for aid in album_ids
                        if biz
                    ],
                    "linked_articles": linked_articles,
                    "text_preview": plain_text[:240].replace("\n", " "),
                }
                results.append(rec)
                print(
                    f"[{i}/{len(urls)}] {status} | {pub_date} | {nickname} | {title[:35]} | "
                    f"imgs={len(img_urls)} | albums={album_ids} {album_titles} | links={len(linked_articles)}",
                    flush=True,
                )
                if status == "VERIFICATION_REQUIRED":
                    break
            except Exception as exc:
                results.append({"status": "ERROR", "url": norm_u, "error": str(exc)})
                print(f"[{i}/{len(urls)}] ERROR | {norm_u[:60]} | {exc}", flush=True)

    out_path = Path(args.out) if args.out else (BASE_DIR / "inspect_results.json")
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved inspect report to {out_path}", flush=True)
    return 0


async def cmd_download_manifest(args: argparse.Namespace) -> int:
    """Download original images from a pre-extracted JSON manifest (e.g., Xueqiu column mirror)."""
    ensure_dirs()
    entries = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    account_out_dir = DOWNLOAD_DIR / sanitize_filename(args.account)
    account_out_dir.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, Any]] = []

    async with aiohttp.ClientSession() as http:
        for idx, item in enumerate(entries, start=1):
            title = item.get("title") or "untitled"
            pub_date = item.get("publish_date") or "0000-00-00"
            folder_name = f"{pub_date}_{sanitize_filename(title, max_len=55)}"
            out_dir = account_out_dir / folder_name
            meta_path = out_dir / "meta.json"
            if meta_path.exists() and not args.force:
                existing = json.loads(meta_path.read_text(encoding="utf-8"))
                if existing.get("status") == "OK":
                    summary.append({"title": title, "publish_date": pub_date, "status": "already_done", "dir": str(out_dir), "saved_images": existing.get("saved_images", 0)})
                    print(f"[{idx}/{len(entries)}] already_done | {pub_date} | {title} | saved={existing.get('saved_images', 0)}", flush=True)
                    continue

            out_dir.mkdir(parents=True, exist_ok=True)
            if item.get("text"):
                (out_dir / "content.txt").write_text(item["text"], encoding="utf-8")

            img_headers = {
                "User-Agent": DEFAULT_UA,
                "Referer": item.get("referer") or "https://xueqiu.com/",
            }
            img_urls = item.get("images") or []
            saved_count = 0
            skipped_count = 0
            error_count = 0
            img_records: list[dict[str, Any]] = []

            for raw_url in img_urls:
                # Strip Xueqiu thumbnail suffix (!800.jpg / !custom.jpg) first for lossless original
                full_url = re.sub(r"![A-Za-z0-9_.-]+$", "", raw_url)
                attempts = [full_url, raw_url] if full_url != raw_url else [raw_url]
                raw_bytes: bytes | None = None
                used_url = raw_url
                for u in attempts:
                    try:
                        async with http.get(u, headers=img_headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                            if resp.status == 200:
                                raw_bytes = await resp.read()
                                used_url = u
                                break
                    except Exception:
                        continue
                if not raw_bytes:
                    error_count += 1
                    img_records.append({"url": raw_url, "status": "error", "reason": "download_failed"})
                    continue

                rec = process_and_save_image(
                    raw_bytes=raw_bytes,
                    out_dir=out_dir,
                    index=saved_count + 1,
                    min_width=args.min_width,
                    min_height=args.min_height,
                    convert_webp_to_png=not args.keep_webp,
                    skip_gif=not args.include_gif,
                )
                rec["url"] = used_url
                img_records.append(rec)
                if rec["status"] == "saved":
                    saved_count += 1
                elif rec["status"] == "skipped":
                    skipped_count += 1
                else:
                    error_count += 1

            meta_out = {
                "status": "OK",
                "id": item.get("id", ""),
                "title": title,
                "publish_date": pub_date,
                "publish_time": item.get("publish_time", ""),
                "link": item.get("link", ""),
                "total_img_urls": len(img_urls),
                "saved_images": saved_count,
                "skipped_images": skipped_count,
                "error_images": error_count,
                "downloaded_at": dt.datetime.now().isoformat(timespec="seconds"),
                "images": img_records,
            }
            meta_path.write_text(json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")
            rec_sum = {
                "title": title,
                "publish_date": pub_date,
                "status": "OK",
                "dir": str(out_dir),
                "saved_images": saved_count,
                "skipped_images": skipped_count,
                "error_images": error_count,
            }
            summary.append(rec_sum)
            print(
                f"[{idx}/{len(entries)}] OK | {pub_date} | {title} | saved={saved_count} skipped={skipped_count} err={error_count}",
                flush=True,
            )

    summary_path = account_out_dir / "manifest_download_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    total_saved = sum(int(r.get("saved_images") or 0) for r in summary)
    print(f"\nDone! Total manifest images saved: {total_saved}. Summary: {summary_path}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="WeChat Official Account article indexer & image downloader.")
    sub = p.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="Log in to mp.weixin.qq.com via browser QR scan and capture session.")
    p_login.add_argument("--browser", help="Path to Edge/Chrome executable.")
    p_login.add_argument("--timeout", type=int, default=240, help="Seconds to wait for QR scan login.")
    p_login.add_argument("--keep-browser", action="store_true", help="Do not close browser after login.")
    p_login.add_argument("--token", help="Manually supply MP token.")
    p_login.add_argument("--cookie", help="Manually supply MP cookie header.")
    p_login.add_argument("--user-agent", help="Optional User-Agent when supplying manual credentials.")

    p_biz = sub.add_parser("search-biz", help="Search Official Accounts by name.")
    p_biz.add_argument("--query", required=True, help="Official Account name to search.")
    p_biz.add_argument("--count", type=int, default=10)

    p_idx = sub.add_parser("fetch-index", help="Fetch historical articles for an Official Account.")
    p_idx.add_argument("--account", required=True, help="Official Account name.")
    p_idx.add_argument("--fakeid", help="Optional explicit fakeid.")
    p_idx.add_argument("--since", default="2022-01-01", help="Earliest publish date (YYYY-MM-DD).")
    p_idx.add_argument("--mode", choices=("search", "full", "both"), default="search")
    p_idx.add_argument("--keywords", nargs="*", help="Keywords for search mode.")
    p_idx.add_argument("--endpoint", choices=("appmsgpublish", "appmsg"), default="appmsgpublish")
    p_idx.add_argument("--page-size", type=int, default=10)
    p_idx.add_argument("--start-begin", type=int, default=0)
    p_idx.add_argument("--max-pages", type=int, default=30)
    p_idx.add_argument("--delay-min", type=float, default=3.5)
    p_idx.add_argument("--delay-max", type=float, default=6.5)
    p_idx.add_argument("--reset", action="store_true", help="Discard previous cached index for this account.")

    p_alb = sub.add_parser("fetch-album", help="Fetch all articles from a public WeChat Album (合集) URL.")
    p_alb.add_argument("--account", required=True, help="Account label for saving index.")
    p_alb.add_argument("--url", required=True, help="WeChat album URL (mp.weixin.qq.com/mp/appmsgalbum?...)")
    p_alb.add_argument("--biz", help="Optional explicit __biz.")
    p_alb.add_argument("--album-id", help="Optional explicit album_id.")
    p_alb.add_argument("--since", default="2022-01-01", help="Earliest publish date (YYYY-MM-DD).")
    p_alb.add_argument("--page-size", type=int, default=20)
    p_alb.add_argument("--max-pages", type=int, default=50)
    p_alb.add_argument("--delay-min", type=float, default=1.5)
    p_alb.add_argument("--delay-max", type=float, default=3.0)
    p_alb.add_argument("--reset", action="store_true")

    p_ins = sub.add_parser("inspect-urls", help="Inspect WeChat article URLs for albums, links, and image counts.")
    p_ins.add_argument("--urls-file", required=True, help="Text file with one article URL per line.")
    p_ins.add_argument("--out", help="Output JSON report path.")

    p_man = sub.add_parser("download-manifest", help="Download original images from a JSON manifest file.")
    p_man.add_argument("--account", required=True, help="Target output folder label.")
    p_man.add_argument("--manifest", required=True, help="Path to JSON manifest file.")
    p_man.add_argument("--min-width", type=int, default=220)
    p_man.add_argument("--min-height", type=int, default=120)
    p_man.add_argument("--keep-webp", action="store_true")
    p_man.add_argument("--include-gif", action="store_true")
    p_man.add_argument("--force", action="store_true")

    p_dl = sub.add_parser("download-images", help="Download original images from indexed articles.")
    p_dl.add_argument("--account", required=True, help="Official Account name.")
    p_dl.add_argument("--index-file", help="Explicit path to articles.json.")
    p_dl.add_argument("--urls-file", help="Plain text file with one article URL per line.")
    p_dl.add_argument("--seqs", help="Comma-separated seq numbers or ranges from articles.csv (e.g. 1,3,5-10).")
    p_dl.add_argument("--include", nargs="*", help="Only download articles whose title/digest contains any keyword.")
    p_dl.add_argument("--exclude", nargs="*", help="Skip articles whose title contains any keyword.")
    p_dl.add_argument("--since", help="Filter publish_date >= YYYY-MM-DD.")
    p_dl.add_argument("--limit", type=int, help="Max articles to download.")
    p_dl.add_argument("--min-width", type=int, default=220)
    p_dl.add_argument("--min-height", type=int, default=120)
    p_dl.add_argument("--keep-webp", action="store_true", help="Keep webp files instead of converting to PNG.")
    p_dl.add_argument("--include-gif", action="store_true", help="Include GIF animations.")
    p_dl.add_argument("--delay-min", type=float, default=1.2)
    p_dl.add_argument("--delay-max", type=float, default=2.5)
    p_dl.add_argument("--force", action="store_true", help="Re-download already completed articles.")

    return p


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "login":
        return asyncio.run(cmd_login(args))
    if args.command == "search-biz":
        return asyncio.run(cmd_search_biz(args))
    if args.command == "fetch-index":
        return asyncio.run(cmd_fetch_index(args))
    if args.command == "fetch-album":
        return asyncio.run(cmd_fetch_album(args))
    if args.command == "inspect-urls":
        return asyncio.run(cmd_inspect_urls(args))
    if args.command == "download-manifest":
        return asyncio.run(cmd_download_manifest(args))
    if args.command == "download-images":
        return asyncio.run(cmd_download_images(args))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
