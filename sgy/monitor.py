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
import socket
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib import error, parse, request
from ai_content import validate_ai_config, prepare_content, render_body

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
            if target["type"] not in ("bark", "ntfy", "wxpusher"):
                raise MonitorError("unsupported_push_type")
            if not https_url(target["server"]):
                raise MonitorError("push_server_must_be_https_without_credentials")
            if target.get("enabled", False):
                if target["type"] == "bark" and not target.get("key_file"):
                    raise MonitorError("bark_key_file_required")
                if target["type"] == "wxpusher" and not target.get("spt_file"):
                    raise MonitorError("wxpusher_spt_file_required")
                if target["type"] == "ntfy" and not target.get("topic_file"):
                    raise MonitorError("ntfy_topic_file_required")
            if type(target.get("since", 0)) not in (int, float):
                raise MonitorError("target_since_must_be_unix_timestamp")
        allowlist = cfg.get("delivery_allowlist")
        if allowlist is not None:
            if (not isinstance(allowlist, list) or any(not isinstance(i, str) for i in allowlist)
                    or len(set(allowlist)) != len(allowlist) or not set(allowlist) <= ids):
                raise MonitorError("invalid_delivery_allowlist")
        expected = cfg.get("expected_delivery_targets")
        if expected is not None:
            if (not isinstance(expected, list) or not expected
                    or any(not isinstance(i, str) for i in expected)
                    or len(set(expected)) != len(expected)
                    or set(expected) != {t["id"] for t in allowed_targets(cfg)}):
                raise MonitorError("delivery_target_set_mismatch")
        validate_ai_config(cfg)
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
    deadline = time.monotonic() + timeout
    req = request.Request(url, data=data, headers=headers or {})
    with request.urlopen(req, timeout=timeout) as response:
        chunks, size = [], 0
        sock = response.fp.raw._sock if response.fp is not None else None
        while True:
            if response.isclosed() or response.length == 0:
                return b"".join(chunks), response.headers.get_content_charset() or "utf-8"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MonitorError("http_deadline_exceeded")
            # read1 makes one buffered/socket read; a keepalive stream cannot reset
            # the overall deadline with periodic whitespace.
            if sock is not None:
                sock.settimeout(remaining)
            try:
                chunk = response.read1(min(65536, 1024 * 1024 + 1 - size))
            except socket.timeout:
                raise MonitorError("http_deadline_exceeded") from None
            size += len(chunk)
            if size > 1024 * 1024:
                raise MonitorError("response_too_large")
            if not chunk:
                return b"".join(chunks), response.headers.get_content_charset() or "utf-8"
            chunks.append(chunk)


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
    con.executescript("""
        CREATE TABLE IF NOT EXISTS safety_quarantine(
            uid TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS delivery_attempt_log(
            id INTEGER PRIMARY KEY, target TEXT NOT NULL, uid TEXT NOT NULL,
            attempted_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS attempt_target_time ON delivery_attempt_log(target,attempted_at);
    """)
    return con


def meta(con, key):
    row = con.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, str(value)))


SAFETY_VERSION = "1"
MAX_CANDIDATES = 5
MAX_PAGES = 3
MAX_AGE_DAYS = 3
MAX_PENDING_ARTICLES = 10
MAX_ATTEMPTS = 5
TARGET_GAP_SECONDS = 60
TARGET_HOURLY_ATTEMPTS = 6
CST = dt.timezone(dt.timedelta(hours=8))


def today():
    return dt.datetime.fromtimestamp(time.time(), CST).date()


def valid_date(value):
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except (TypeError, ValueError):
        return False


def ensure_safety(con):
    if meta(con, "safety_version") == SAFETY_VERSION:
        return
    # Upgrade before starting the worker. Legacy pending work is never approved
    # implicitly; retain it as evidence instead of replaying it on deployment.
    with con:
        con.execute("""INSERT OR IGNORE INTO safety_quarantine(uid,reason,created_at)
            SELECT uid,'pre_upgrade_pending',? FROM deliveries WHERE state='pending'""", (time.time(),))
        con.execute("UPDATE deliveries SET state='quarantined',last_error='pre_upgrade_pending' WHERE state='pending'")
        con.execute("UPDATE notices SET is_new=0")
        dates = [r[0] for r in con.execute("SELECT date FROM notices")
                 if valid_date(r[0]) and r[0] <= today().isoformat()]
        set_meta(con, "publication_watermark", max(dates, default=""))
        set_meta(con, "recovery_cursor", "")
        set_meta(con, "safety_installed_at", time.time())
        set_meta(con, "safety_version", SAFETY_VERSION)


def quarantine(con, uids, reason):
    for uid in set(uids):
        con.execute("INSERT OR IGNORE INTO safety_quarantine VALUES(?,?,?)", (uid, reason, time.time()))
        con.execute("UPDATE notices SET is_new=0 WHERE uid=?", (uid,))
        con.execute("UPDATE deliveries SET state='quarantined',last_error=? WHERE uid=? AND state='pending'", (reason, uid))


def ingest(con, notices, baseline=False, *, quarantine_reason=None):
    ensure_safety(con)
    known = {r[0] for r in con.execute("SELECT uid FROM notices")}
    unseen = {n.uid: n for n in notices if n.uid not in known}
    watermark = meta(con, "publication_watermark") or ""
    floor = max(watermark, (today() - dt.timedelta(days=MAX_AGE_DAYS)).isoformat())
    candidates = [n for n in unseen.values() if valid_date(n.date) and floor <= n.date <= today().isoformat()]
    invalid = [n for n in unseen.values() if not valid_date(n.date) or n.date > today().isoformat()]
    reason = quarantine_reason or ("invalid_or_future_date" if invalid else None)
    if not baseline and len(candidates) > MAX_CANDIDATES:
        reason = reason or "candidate_batch_limit"
    reason = reason or meta(con, "delivery_pause_reason") or None
    candidate_ids = {n.uid for n in candidates} if not baseline and not reason else set()
    now = time.time()
    with con:
        for n in unseen.values():
            con.execute("INSERT OR IGNORE INTO notices VALUES(?,?,?,?,?,?)",
                        (n.uid, n.title, n.date, n.url, now, int(n.uid in candidate_ids)))
        if reason:
            quarantine(con, [n.uid for n in candidates + invalid], reason)
            set_meta(con, "delivery_pause_reason", reason)
        dates = [n.date for n in notices if valid_date(n.date) and n.date <= today().isoformat()]
        set_meta(con, "publication_watermark", max([watermark, *dates]))
        if baseline:
            set_meta(con, "baseline_at", now)
    return len(unseen)


def poll(con):
    ensure_safety(con)
    started = time.monotonic()
    with con:
        set_meta(con, "last_attempt_at", time.time())
    rows, next_page = fetch_page(PAGE_URL)
    if meta(con, "baseline_at") is None:
        count = ingest(con, rows, baseline=True)
        LOG.info("baseline_saved count=%d old_notices_not_sent=true", count)
    else:
        known = {r[0] for r in con.execute("SELECT uid FROM notices")}
        watermark = meta(con, "publication_watermark") or ""
        floor = max(watermark, (today() - dt.timedelta(days=MAX_AGE_DAYS)).isoformat())
        batch, page_rows, scanned = list(rows), rows, {PAGE_URL}
        reason = None
        # Stage the complete bounded batch before any INSERT. A failed page
        # fetch cannot expose half a batch to the independent delivery thread.
        while next_page:
            overlap = any(n.uid in known and n.date >= floor for n in page_rows)
            dates = [n.date for n in page_rows if valid_date(n.date)]
            if overlap or (dates and max(dates) < floor):
                break
            if next_page in scanned or len(scanned) >= MAX_PAGES or time.monotonic() - started >= 15:
                reason = "archive_scan_limit"
                break
            scanned.add(next_page)
            remaining = max(1, min(10, 20 - (time.monotonic() - started)))
            page_rows, next_page = fetch_page(next_page, timeout=remaining)
            batch.extend(page_rows)
        count = ingest(con, batch, quarantine_reason=reason)
        LOG.info("check_ok first_page_count=%d stored_count=%d scanned_pages=%d", len(rows), count, len(scanned))
    with con:
        set_meta(con, "recovery_cursor", "")
        set_meta(con, "last_success_at", time.time())
        set_meta(con, "last_error", meta(con, "delivery_pause_reason") or "")
    if meta(con, "delivery_pause_reason"):
        LOG.error("delivery_safety_paused reason=%s", meta(con, "delivery_pause_reason"))


def restrict_targets(targets, cfg):
    allowlist = cfg.get("delivery_allowlist")
    return [t for t in targets if allowlist is None or t["id"] in allowlist]


def allowed_targets(cfg):
    return restrict_targets([t for t in cfg.get("targets", []) if t.get("enabled", False)], cfg)


def send_push(target, notice, *, test=False, content=None, cfg=None):
    # Enforce the trial scope even when a caller bypasses the queue or CLI.
    if cfg is not None and not restrict_targets([target], cfg):
        raise MonitorError("target_excluded_by_delivery_allowlist")
    # Test deliveries use the same source title as regular notifications.
    title = "石工院新通知"
    if content is None:
        content = {"text": "正文请查看原文。" if notice.title else "这是一条通知通道测试。", "images": 0}
    body = render_body(notice, content)
    headers = {"Content-Type": "application/json; charset=utf-8"}
    server = target["server"].rstrip("/")
    if target["type"] == "bark":
        key = read_secret(target["key_file"])
        payload = {"device_key": key, "title": title, "body": body,
                   "url": notice.url, "group": "西南石油大学石工院", "isArchive": "1"}
        endpoint = server + "/push"
    elif target["type"] == "wxpusher":
        spt = read_secret(target["spt_file"])
        if not re.fullmatch(r"SPT_[A-Za-z0-9_-]{8,128}", spt):
            raise MonitorError("invalid_wxpusher_spt")
        payload = {"sptList": [spt], "summary": title,
                   "content": body,
                   "contentType": 1, "url": notice.url}
        endpoint = server + "/api/send/message/simple-push"
    else:
        topic = read_secret(target["topic_file"])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic):
            raise MonitorError("invalid_ntfy_topic")
        payload = {"topic": topic, "title": title, "message": body,
                   "click": notice.url, "priority": 3, "tags": ["mortar_board"]}
        endpoint = server + "/"
        if target.get("token_file"):
            headers["Authorization"] = "Bearer " + read_secret(target["token_file"])
    # Reserve space for Bark's APNs wrapper. Measure escaped JSON, not characters.
    body_field = {"bark": "body", "ntfy": "message", "wxpusher": "content"}[target["type"]]
    limit = 3500 if target["type"] in ("bark", "ntfy") else 39000
    metadata = dict(payload, **{body_field: ""})
    budget = min(len(body.encode("utf-8")), limit - len(json.dumps(metadata, ensure_ascii=False).encode()) - 128)
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    while len(encoded) > limit:
        if budget <= 0:
            raise MonitorError("push_payload_exceeds_limit")
        try:
            payload[body_field] = render_body(notice, content, max_bytes=budget)
        except ValueError:
            raise MonitorError("push_footer_exceeds_limit") from None
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        if len(encoded) > limit:
            budget -= max(32, len(encoded) - limit)
    response, _ = http_read(endpoint, data=encoded, headers=headers, timeout=10)
    try:
        accepted = json.loads(response)
    except (ValueError, UnicodeError):
        raise MonitorError("push_invalid_json_response") from None
    if not isinstance(accepted, dict):
        raise MonitorError("push_invalid_response")
    if target["type"] == "wxpusher":
        if accepted.get("code") != 1000 or accepted.get("success") is False:
            raise MonitorError("wxpusher_rejected_request")
        results = accepted.get("data")
        if isinstance(results, list) and any(isinstance(r, dict) and r.get("code", 1000) != 1000 for r in results):
            raise MonitorError("wxpusher_recipient_rejected")
    if target["type"] == "bark" and accepted.get("code") != 200:
        raise MonitorError("bark_rejected_request")
    if target["type"] == "ntfy" and not (accepted.get("id") and accepted.get("event") == "message"):
        raise MonitorError("ntfy_rejected_request")


def queue_targets(con, targets):
    ensure_safety(con)
    if meta(con, "delivery_pause_reason"):
        return
    with con:
        # Expired pending work remains auditable but can no longer drain after
        # a long outage. Classification and dispatch use the same age boundary.
        expired = [r[0] for r in con.execute("SELECT uid FROM notices WHERE is_new=1 AND date<?",
                   ((today() - dt.timedelta(days=MAX_AGE_DAYS)).isoformat(),))]
        quarantine(con, expired, "delivery_window_expired")
        for target in targets:
            con.execute("""INSERT OR IGNORE INTO deliveries(uid,target)
                SELECT uid,? FROM notices WHERE is_new=1 AND first_seen>=?""",
                        (target["id"], target.get("since", 0)))
        pending = [r[0] for r in con.execute("SELECT DISTINCT uid FROM deliveries WHERE state='pending'")]
        if len(pending) > MAX_PENDING_ARTICLES:
            quarantine(con, pending, "pending_article_limit")
            set_meta(con, "delivery_pause_reason", "pending_article_limit")


def rate_allowed_targets(con, targets):
    now = time.time()
    return [t for t in targets if con.execute("""SELECT count(*) FROM delivery_attempt_log
        WHERE target=? AND attempted_at>?""", (t["id"], now - 3600)).fetchone()[0] < TARGET_HOURLY_ATTEMPTS
        and not con.execute("SELECT 1 FROM delivery_attempt_log WHERE target=? AND attempted_at>? LIMIT 1",
                            (t["id"], now - TARGET_GAP_SECONDS)).fetchone()]


def deliver_one(con, targets, cfg):
    targets = restrict_targets(targets, cfg)
    queue_targets(con, targets)
    if meta(con, "delivery_pause_reason"):
        return False
    targets = rate_allowed_targets(con, targets)
    by_id = {t["id"]: t for t in targets}
    if not by_id:
        return False
    marks = ",".join("?" for _ in by_id)
    row = con.execute("""SELECT d.*,n.title,n.date,n.url FROM deliveries d
        JOIN notices n USING(uid) WHERE d.state='pending' AND n.is_new=1 AND d.attempts<5 AND d.next_attempt<=?
        AND d.target IN (""" + marks + ") AND (" + " OR ".join(
            "(d.target=? AND n.first_seen>=?)" for _ in by_id) + ") ORDER BY d.next_attempt,n.first_seen LIMIT 1",
        (time.time(), *by_id, *(value for t in by_id.values() for value in (t["id"], t.get("since", 0))))).fetchone()
    if row is None:
        return False
    n = Notice(row["uid"], row["title"], row["date"], row["url"])
    # Persist the reservation before any provider or AI request. Restarting
    # cannot reset rate limits, including ambiguous/failed HTTP requests.
    with con:
        con.execute("INSERT INTO delivery_attempt_log(target,uid,attempted_at) VALUES(?,?,?)",
                    (row["target"], n.uid, time.time()))
    try:
        content = prepare_content(con, n, cfg, http_read, read_secret)
        LOG.info("article_content article=%s mode=%s reason=%s", n.uid, content["status"], content["reason"])
        send_push(by_id[row["target"]], n, cfg=cfg, content=content)
    except Exception as exc:
        code = safe_error(exc)
        with con:
            con.execute("""UPDATE deliveries SET attempts=attempts+1,last_error=?,
                next_attempt=? WHERE uid=? AND target=?""",
                (code, time.time() + RNG.randint(30, 180), n.uid, row["target"]))
            if row["attempts"] + 1 >= MAX_ATTEMPTS:
                con.execute("UPDATE deliveries SET state='quarantined',last_error='retry_limit' WHERE uid=? AND target=?",
                            (n.uid, row["target"]))
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
                targets = allowed_targets(cfg)
                active = deliver_one(con, targets, cfg)
            except Exception as exc:
                LOG.error("delivery_worker_error reason=%s", safe_error(exc))
                active = False
            stop.wait(2)


def status(db_path, cfg):
    if not db_path.exists():
        return {"initialized": False}
    with contextlib.closing(connect(db_path)) as con:
        result = {"initialized": meta(con, "baseline_at") is not None,
                  "interval_seconds": [cfg["interval_min_seconds"], cfg["interval_max_seconds"]],
                  "enabled_targets": [t["id"] for t in cfg.get("targets", []) if t.get("enabled", False)],
                  "active_delivery_targets": [t["id"] for t in allowed_targets(cfg)],
                  "ai_enabled": bool(cfg.get("ai", {}).get("enabled")),
                  "ai_model": cfg.get("ai", {}).get("model") if cfg.get("ai", {}).get("enabled") else None,
                  "notice_count": con.execute("SELECT count(*) FROM notices").fetchone()[0],
                  "new_notice_count": con.execute("SELECT count(*) FROM notices WHERE is_new=1").fetchone()[0],
                  "safety_version": meta(con, "safety_version"),
                  "delivery_pause_reason": meta(con, "delivery_pause_reason"),
                  "publication_watermark": meta(con, "publication_watermark"),
                  "quarantined_notices": con.execute("SELECT count(*) FROM safety_quarantine").fetchone()[0],
                  "expected_delivery_targets": cfg.get("expected_delivery_targets"),
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
            targets = [t for t in allowed_targets(cfg) if selected_target in ("all", t["id"])]
            if not targets:
                raise MonitorError("no_matching_enabled_target")
            notice = fetch_page(PAGE_URL)[0][0] if args.test_latest else Notice("test", "", "", PAGE_URL)
            content = None
            if args.test_latest:
                Path(args.state_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
                with contextlib.closing(connect(db_path)) as con:
                    content = prepare_content(con, notice, cfg, http_read, read_secret)
                LOG.info("test_article_content article=%s mode=%s reason=%s", notice.uid, content["status"], content["reason"])
            for target in targets:
                send_push(target, notice, test=True, cfg=cfg, content=content)
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
                ensure_safety(con)
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
