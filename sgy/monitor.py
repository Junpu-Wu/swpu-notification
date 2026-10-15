#!/usr/bin/env python3
"""SWPU notice monitor. Python 3.10+, standard library only."""
import argparse
import contextlib
import datetime as dt
import fcntl
import json
import logging
import os
from pathlib import Path
import random
import re
import signal
import sqlite3
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib import error, parse, request

LOG = logging.getLogger("swpu-monitor")
RNG = random.SystemRandom()
PAGE_URL = "https://www.swpu.edu.cn/sgy/tzgg.htm"


class MonitorError(Exception):
    """Messages are safe diagnostic codes, never URLs or provider responses."""


@dataclass(frozen=True)
class Notice:
    uid: str
    title: str
    date: str
    url: str


def article_id(url):
    u = parse.urlsplit(url)
    if u.scheme != "https" or u.hostname != "www.swpu.edu.cn":
        return None
    match = re.fullmatch(r"/sgy/info/1086/(\d+)\.htm", u.path)
    if match:
        return "1086:" + match[1]
    q = parse.parse_qs(u.query)
    if u.path == "/sgy/acontent.jsp" and q.get("wbtreeid") == ["1086"]:
        value = q.get("wbnewsid", [""])[0]
        if value.isdigit():
            return "1086:" + value
    return None


class NoticeParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.notices = []
        self.next_page = None
        self.li = None
        self.in_date = False
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li":
            self.li = {"date": "", "url": "", "title": ""}
        if tag == "span" and self.li is not None:
            self.in_date = "time" in attrs.get("class", "").split()
        if tag == "a":
            self.anchor = {"url": parse.urljoin(self.base_url, attrs.get("href", "")),
                           "text": "", "title": attrs.get("title", "")}

    def handle_data(self, data):
        if self.in_date and self.li is not None:
            self.li["date"] += data
        if self.anchor is not None:
            self.anchor["text"] += data

    def handle_endtag(self, tag):
        if tag == "span":
            self.in_date = False
        if tag == "a" and self.anchor is not None:
            a = self.anchor
            if self.li is not None and article_id(a["url"]):
                self.li["url"] = a["url"]
                self.li["title"] = (a["title"] or a["text"]).strip()
            u = parse.urlsplit(a["url"])
            if (a["text"].strip() == "下页" and u.scheme == "https"
                    and u.hostname == "www.swpu.edu.cn"
                    and re.fullmatch(r"/sgy/tzgg/\d+\.htm", u.path)):
                self.next_page = a["url"]
            self.anchor = None
        if tag == "li" and self.li is not None:
            row = self.li
            uid = article_id(row["url"])
            date = row["date"].strip()
            if uid and row["title"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                try:
                    dt.date.fromisoformat(date)
                except ValueError:
                    pass
                else:
                    self.notices.append(Notice(uid, row["title"], date, row["url"]))
            self.li = None
            self.in_date = False


def parse_page(html, url):
    parser = NoticeParser(url)
    parser.feed(html)
    unique = {n.uid: n for n in parser.notices}
    if not unique:
        raise MonitorError("page_has_no_valid_notices")
    return list(unique.values()), parser.next_page


def https_url(value):
    u = parse.urlsplit(value)
    return (u.scheme == "https" and bool(u.hostname) and not u.username
            and not u.password and not u.query and not u.fragment)


def load_config(path):
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        lo, hi = cfg["interval_min_seconds"], cfg["interval_max_seconds"]
        if type(lo) is not int or type(hi) is not int or not 30 <= lo <= hi <= 3600:
            raise MonitorError("interval_must_be_between_30_and_3600")
        if cfg.get("page_url", PAGE_URL) != PAGE_URL:
            raise MonitorError("unsupported_page_url")
        ids = set()
        for target in cfg.get("targets", []):
            ident = target["id"]
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", ident) or ident in ids:
                raise MonitorError("invalid_or_duplicate_target_id")
            ids.add(ident)
            if target["type"] not in ("bark", "ntfy"):
                raise MonitorError("unsupported_push_type")
            if not https_url(target["server"]):
                raise MonitorError("push_server_must_be_https_without_credentials")
            if target.get("enabled", False):
                if target["type"] == "bark" and not target.get("key_file"):
                    raise MonitorError("bark_key_file_required")
                if target["type"] == "ntfy" and not target.get("topic_file"):
                    raise MonitorError("ntfy_topic_file_required")
            if type(target.get("since", 0)) not in (int, float):
                raise MonitorError("target_since_must_be_unix_timestamp")
        return cfg
    except MonitorError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise MonitorError("invalid_configuration") from None


def interval(cfg):
    return RNG.randint(cfg["interval_min_seconds"], cfg["interval_max_seconds"])


def safe_error(exc):
    if isinstance(exc, MonitorError):
        return str(exc)
    if isinstance(exc, error.HTTPError):
        return "http_" + str(exc.code)
    return type(exc).__name__


def read_secret(path):
    try:
        p = Path(path)
        if p.stat().st_mode & 0o077:
            raise MonitorError("credential_file_requires_mode_600")
        secret = p.read_text(encoding="utf-8").strip()
        if not secret or any(c.isspace() for c in secret):
            raise MonitorError("credential_empty_or_invalid")
        return secret
    except OSError:
        raise MonitorError("credential_file_unavailable") from None


def http_read(url, *, data=None, headers=None, timeout=10):
    req = request.Request(url, data=data, headers=headers or {})
    with request.urlopen(req, timeout=timeout) as response:
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise MonitorError("response_too_large")
        return body, response.headers.get_content_charset() or "utf-8"


def fetch_page(url, timeout=10):
    body, encoding = http_read(url, timeout=timeout, headers={
        "User-Agent": "SWPU-Notice-Monitor/1.0 (+personal notice subscription)",
        "Cache-Control": "no-cache", "Pragma": "no-cache"})
    try:
        return parse_page(body.decode(encoding), url)
    except (UnicodeError, LookupError):
        raise MonitorError("page_encoding_invalid") from None


def connect(db_path):
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notices(
            uid TEXT PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL,
            url TEXT NOT NULL, first_seen REAL NOT NULL, is_new INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries(
            uid TEXT NOT NULL REFERENCES notices(uid), target TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0, last_error TEXT,
            accepted_at REAL, PRIMARY KEY(uid, target));
    """)
    return con


def meta(con, key):
    row = con.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, str(value)))


def ingest(con, notices, baseline=False):
    added = 0
    now = time.time()
    with con:
        for n in notices:
            cur = con.execute("INSERT OR IGNORE INTO notices VALUES(?,?,?,?,?,?)",
                              (n.uid, n.title, n.date, n.url, now, int(not baseline)))
            added += cur.rowcount
        if baseline:
            set_meta(con, "baseline_at", now)
    return added


def poll(con):
    started = time.monotonic()
    now = time.time()
    with con:
        set_meta(con, "last_attempt_at", now)
    rows, next_page = fetch_page(PAGE_URL)
    if meta(con, "baseline_at") is None:
        count = ingest(con, rows, baseline=True)
        LOG.info("baseline_saved count=%d old_notices_not_sent=true", count)
    else:
        count = 0
        # If the entire first page is new, follow pages until a known article.
        # A pending recovery cursor survives a restart or a failed older-page fetch.
        known = {r[0] for r in con.execute("SELECT uid FROM notices")}
        overlap = any(n.uid in known for n in rows)
        count += ingest(con, rows)
        cursor = meta(con, "recovery_cursor") or (next_page if not overlap else None)
        with con:
            set_meta(con, "recovery_cursor", cursor or "")
        scanned = set()
        while cursor and cursor not in scanned and time.monotonic() - started < 15:
            scanned.add(cursor)
            remaining = max(1, min(10, 20 - (time.monotonic() - started)))
            older, following = fetch_page(cursor, timeout=remaining)
            overlap = any(n.uid in known for n in older)
            count += ingest(con, older)
            cursor = None if overlap else following
            with con:
                set_meta(con, "recovery_cursor", cursor or "")
        if cursor:
            LOG.info("older_pages_recovery_pending=true")
        LOG.info("check_ok first_page_count=%d new_count=%d", len(rows), count)
    with con:
        set_meta(con, "last_success_at", time.time())
        set_meta(con, "last_error", "")


def send_push(target, notice, *, test=False):
    title = "通知监测测试" if test else "石工院新通知"
    if test and notice.title:
        title = "文章直达测试"
        body = "已有通知的链接测试：\n" + notice.title + "\n发布日期：" + notice.date
    elif test:
        body = "这是一条测试通知，点击可打开学院通知页。"
    else:
        body = notice.title + "\n发布日期：" + notice.date
    headers = {"Content-Type": "application/json; charset=utf-8"}
    server = target["server"].rstrip("/")
    if target["type"] == "bark":
        key = read_secret(target["key_file"])
        payload = {"device_key": key, "title": title, "body": body,
                   "url": notice.url, "group": "西南石油大学石工院", "isArchive": "1"}
        endpoint = server + "/push"
    else:
        topic = read_secret(target["topic_file"])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic):
            raise MonitorError("invalid_ntfy_topic")
        payload = {"topic": topic, "title": title, "message": body,
                   "click": notice.url, "priority": 3, "tags": ["mortar_board"]}
        endpoint = server + "/"
        if target.get("token_file"):
            headers["Authorization"] = "Bearer " + read_secret(target["token_file"])
    response, _ = http_read(endpoint, data=json.dumps(payload, ensure_ascii=False).encode(),
                            headers=headers, timeout=10)
    try:
        accepted = json.loads(response)
    except (ValueError, UnicodeError):
        raise MonitorError("push_invalid_json_response") from None
    if not isinstance(accepted, dict):
        raise MonitorError("push_invalid_response")
    if target["type"] == "bark" and accepted.get("code") != 200:
        raise MonitorError("bark_rejected_request")
    if target["type"] == "ntfy" and not (accepted.get("id") and accepted.get("event") == "message"):
        raise MonitorError("ntfy_rejected_request")


def queue_targets(con, targets):
    with con:
        for target in targets:
            con.execute("""INSERT OR IGNORE INTO deliveries(uid,target)
                SELECT uid,? FROM notices WHERE is_new=1 AND first_seen>=?""",
                        (target["id"], target.get("since", 0)))


def deliver_one(con, targets, cfg):
    queue_targets(con, targets)
    by_id = {t["id"]: t for t in targets}
    if not by_id:
        return False
    marks = ",".join("?" for _ in by_id)
    row = con.execute("""SELECT d.*,n.title,n.date,n.url FROM deliveries d
        JOIN notices n USING(uid) WHERE d.state='pending' AND d.next_attempt<=?
        AND d.target IN (""" + marks + ") ORDER BY d.next_attempt,n.first_seen LIMIT 1",
        (time.time(), *by_id)).fetchone()
    if row is None:
        return False
    n = Notice(row["uid"], row["title"], row["date"], row["url"])
    try:
        send_push(by_id[row["target"]], n)
    except Exception as exc:
        code = safe_error(exc)
        with con:
            con.execute("""UPDATE deliveries SET attempts=attempts+1,last_error=?,
                next_attempt=? WHERE uid=? AND target=?""",
                (code, time.time() + RNG.randint(30, 180), n.uid, row["target"]))
        LOG.warning("push_retry target=%s article=%s reason=%s", row["target"], n.uid, code)
    else:
        with con:
            con.execute("""UPDATE deliveries SET state='accepted',attempts=attempts+1,
                accepted_at=?,last_error=NULL WHERE uid=? AND target=?""",
                (time.time(), n.uid, row["target"]))
        LOG.info("push_accepted target=%s article=%s", row["target"], n.uid)
    return True


def delivery_worker(config_path, db_path, stop):
    with contextlib.closing(connect(db_path)) as con:
        while not stop.is_set():
            try:
                cfg = load_config(config_path)
                targets = [t for t in cfg.get("targets", []) if t.get("enabled", False)]
                active = deliver_one(con, targets, cfg)
            except Exception as exc:
                LOG.error("delivery_worker_error reason=%s", safe_error(exc))
                active = False
            stop.wait(0.1 if active else 2)


def status(db_path, cfg):
    if not db_path.exists():
        return {"initialized": False}
    with contextlib.closing(connect(db_path)) as con:
        result = {"initialized": meta(con, "baseline_at") is not None,
                  "interval_seconds": [cfg["interval_min_seconds"], cfg["interval_max_seconds"]],
                  "enabled_targets": [t["id"] for t in cfg.get("targets", []) if t.get("enabled", False)],
                  "notice_count": con.execute("SELECT count(*) FROM notices").fetchone()[0],
                  "new_notice_count": con.execute("SELECT count(*) FROM notices WHERE is_new=1").fetchone()[0],
                  "deliveries": {r[0]: r[1] for r in con.execute("SELECT state,count(*) FROM deliveries GROUP BY state")}}
        for key in ("baseline_at", "last_attempt_at", "last_success_at", "next_check_at", "last_error"):
            value = meta(con, key)
            if value and key.endswith("_at"):
                value = dt.datetime.fromtimestamp(float(value), dt.timezone(dt.timedelta(hours=8))).isoformat()
            result[key] = value
        result["recovering_older_pages"] = bool(meta(con, "recovery_cursor"))
        return result


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-dir", default="./state")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--once", action="store_true", help="One real page check; does not send pushes")
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--test-push", nargs="?", const="all", metavar="TARGET_ID")
    actions.add_argument("--test-latest", nargs="?", const="all", metavar="TARGET_ID",
                         help="Test with the latest article link; does not change saved records")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        db_path = Path(args.state_dir) / "notices.sqlite3"
        if args.status:
            print(json.dumps(status(db_path, cfg), ensure_ascii=False, indent=2))
            return 0
        if args.test_push or args.test_latest:
            selected_target = args.test_push or args.test_latest
            targets = [t for t in cfg.get("targets", []) if t.get("enabled", False)
                       and selected_target in ("all", t["id"])]
            if not targets:
                raise MonitorError("no_matching_enabled_target")
            notice = fetch_page(PAGE_URL)[0][0] if args.test_latest else Notice("test", "", "", PAGE_URL)
            for target in targets:
                send_push(target, notice, test=True)
                LOG.info("test_push_accepted target=%s article=%s", target["id"], notice.uid)
            return 0
        Path(args.state_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(Path(args.state_dir) / "monitor.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise MonitorError("another_monitor_is_running") from None
            stop = threading.Event()
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: stop.set())
            with contextlib.closing(connect(db_path)) as con:
                if not args.once:
                    threading.Thread(target=delivery_worker,
                                     args=(args.config, db_path, stop), daemon=True).start()
                while not stop.is_set():
                    start = time.monotonic()
                    cfg = load_config(args.config)
                    delay = interval(cfg)
                    with con:
                        set_meta(con, "next_check_at", "" if args.once else time.time() + delay)
                    try:
                        poll(con)
                    except Exception as exc:
                        code = safe_error(exc)
                        with con:
                            set_meta(con, "last_error", code)
                        LOG.warning("check_failed reason=%s", code)
                        if args.once:
                            return 1
                    if args.once:
                        return 0
                    LOG.info("next_check_in_seconds=%d", delay)
                    stop.wait(max(0, start + delay - time.monotonic()))
            return 0
    except Exception as exc:
        LOG.error("monitor_stopped reason=%s", safe_error(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
