import importlib.util
import io
import json
import logging
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent / "monitor.py"
spec = importlib.util.spec_from_file_location("monitor", SOURCE)
m = importlib.util.module_from_spec(spec)
sys.modules["monitor"] = m
spec.loader.exec_module(m)


def page(ids, next_page=None, jsp=False):
    rows = []
    for i in ids:
        link = (f"acontent.jsp?urltype=news.NewsContentUrl&amp;wbtreeid=1086&amp;wbnewsid={i}"
                if jsp else f"info/1086/{i}.htm")
        rows.append(f'<li><span class="time">2026-10-14</span><a href="{link}">通知 {i} &amp; 测试</a></li>')
    if next_page:
        rows.append(f'<a href="{next_page}">下页</a>')
    return "<html><body>" + "".join(rows) + "</body></html>"


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = m.connect(self.root / "state.sqlite3")
        self.cfg = {"interval_min_seconds": 30, "interval_max_seconds": 180, "targets": []}

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def rows(self, ids, jsp=False):
        return m.parse_page(page(ids, jsp=jsp), m.PAGE_URL)[0]

    def test_both_url_forms_and_entity_decoding(self):
        regular = self.rows([68787])[0]
        alternate = self.rows([68787], jsp=True)[0]
        self.assertEqual(regular.uid, alternate.uid)
        self.assertIn("& 测试", regular.title)
        self.assertIsNone(m.article_id("https://evil.example/sgy/info/1086/68787.htm"))

    def test_first_run_silent_restart_dedup_and_multiple_same_day(self):
        m.ingest(self.db, self.rows([100, 99]), baseline=True)
        self.assertEqual(m.ingest(self.db, self.rows([102, 101, 100, 99])), 2)
        self.db.close()
        self.db = m.connect(self.root / "state.sqlite3")
        self.assertEqual(m.ingest(self.db, self.rows([102, 101], jsp=True)), 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM notices WHERE is_new=1").fetchone()[0], 2)
        targets = [{"id": "me"}, {"id": "friend"}]
        m.queue_targets(self.db, targets)
        m.queue_targets(self.db, targets)
        self.assertEqual(self.db.execute("SELECT count(*) FROM deliveries").fetchone()[0], 4)

    def test_invalid_page_does_not_initialize_or_clear_state(self):
        with patch.object(m, "fetch_page", side_effect=m.MonitorError("page_has_no_valid_notices")):
            with self.assertRaises(m.MonitorError):
                m.poll(self.db)
        self.assertIsNone(m.meta(self.db, "baseline_at"))
        with self.assertRaises(m.MonitorError):
            m.parse_page("<html>维护中</html>", m.PAGE_URL)
        m.ingest(self.db, self.rows([100]), baseline=True)
        with patch.object(m, "fetch_page", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                m.poll(self.db)
        self.assertEqual(self.db.execute("SELECT count(*) FROM notices").fetchone()[0], 1)

    def test_offline_gap_is_recovered_across_pages(self):
        m.ingest(self.db, self.rows([100, 99]), baseline=True)
        with patch.object(m, "fetch_page", side_effect=[
                (self.rows([104, 103]), "https://www.swpu.edu.cn/sgy/tzgg/25.htm"),
                (self.rows([102, 101, 100]), "https://www.swpu.edu.cn/sgy/tzgg/24.htm")]):
            m.poll(self.db)
        self.assertEqual(self.db.execute("SELECT count(*) FROM notices WHERE is_new=1").fetchone()[0], 4)
        self.assertFalse(m.meta(self.db, "recovery_cursor"))

    def test_failed_older_page_keeps_cursor_and_new_notice(self):
        m.ingest(self.db, self.rows([100]), baseline=True)
        cursor = "https://www.swpu.edu.cn/sgy/tzgg/25.htm"
        with patch.object(m, "fetch_page", side_effect=[(self.rows([103]), cursor), TimeoutError]):
            with self.assertRaises(TimeoutError):
                m.poll(self.db)
        self.assertEqual(m.meta(self.db, "recovery_cursor"), cursor)
        with patch.object(m, "fetch_page", side_effect=[(self.rows([103]), cursor), (self.rows([102, 101, 100]), None)]):
            m.poll(self.db)
        self.assertEqual(self.db.execute("SELECT count(*) FROM notices WHERE is_new=1").fetchone()[0], 3)

    def test_failed_recipient_does_not_repeat_success_for_other_recipient(self):
        m.ingest(self.db, self.rows([100]), baseline=True)
        m.ingest(self.db, self.rows([101]))
        targets = [{"id": "me"}, {"id": "friend"}]
        def send(target, _):
            if target["id"] == "me":
                raise TimeoutError("secret-must-not-appear")
        logs = io.StringIO()
        handler = logging.StreamHandler(logs)
        m.LOG.addHandler(handler)
        try:
            with patch.object(m, "send_push", side_effect=send):
                self.assertTrue(m.deliver_one(self.db, targets, self.cfg))
                self.assertTrue(m.deliver_one(self.db, targets, self.cfg))
        finally:
            m.LOG.removeHandler(handler)
        self.assertNotIn("secret-must-not-appear", logs.getvalue())
        states = dict(self.db.execute("SELECT target,state FROM deliveries"))
        self.assertEqual(states, {"me": "pending", "friend": "accepted"})
        self.db.execute("UPDATE deliveries SET next_attempt=0 WHERE target='me'")
        self.db.commit()
        with patch.object(m, "send_push") as send:
            self.assertTrue(m.deliver_one(self.db, targets, self.cfg))
            self.assertEqual(send.call_args[0][0]["id"], "me")
            self.assertFalse(m.deliver_one(self.db, targets, self.cfg))

    def test_future_only_recipient_skips_existing_backlog(self):
        m.ingest(self.db, self.rows([100]), baseline=True)
        m.ingest(self.db, self.rows([101]))
        m.queue_targets(self.db, [{"id": "friend", "since": m.time.time() + 1}])
        self.assertEqual(self.db.execute("SELECT count(*) FROM deliveries").fetchone()[0], 0)

    def test_interval_bounds_and_invalid_configuration(self):
        draws = [m.interval(self.cfg) for _ in range(10000)]
        self.assertTrue(all(30 <= x <= 180 for x in draws))
        self.assertIn(30, draws)
        self.assertIn(180, draws)
        path = self.root / "config.json"
        for lo, hi in ((29, 180), (30, 3601), (180, 30), (30.0, 180)):
            path.write_text(json.dumps(dict(self.cfg, interval_min_seconds=lo, interval_max_seconds=hi)))
            with self.assertRaises(m.MonitorError):
                m.load_config(path)

    def test_normal_poll_does_not_fetch_archive(self):
        m.ingest(self.db, self.rows([100, 99]), baseline=True)
        with patch.object(m, "fetch_page", return_value=(self.rows([101, 100]), "older")) as fetch:
            m.poll(self.db)
            self.assertEqual(fetch.call_count, 1)


class PushTransportTests(unittest.TestCase):
    def test_article_link_in_regular_and_article_test_notifications(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "secret"
            secret.write_text("synthetic-key")
            secret.chmod(0o600)
            n = m.Notice("1086:68787", "最近一篇通知", "2026-10-12",
                         "https://www.swpu.edu.cn/sgy/info/1086/68787.htm")
            for provider, response in (("bark", b'{"code":200}'),
                                       ("ntfy", b'{"id":"test","event":"message"}')):
                target = {"type": provider, "server": "https://example.test",
                          "key_file": str(secret), "topic_file": str(secret)}
                for test in (False, True):
                    with patch.object(m, "http_read", return_value=(response, "utf-8")) as transport:
                        m.send_push(target, n, test=test)
                        payload = json.loads(transport.call_args.kwargs["data"])
                        self.assertEqual(payload["url" if provider == "bark" else "click"], n.url)
                        self.assertNotEqual(n.url, m.PAGE_URL)
                        self.assertIn(n.title, payload["body" if provider == "bark" else "message"])
                        self.assertEqual(payload["title"], "文章直达测试" if test else "石工院新通知")

    def test_bark_and_ntfy_payloads_over_http(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, body, self.headers.get("Authorization")))
                result = {"code": 200} if self.path == "/push" else {"id": "mock-id", "event": "message"}
                encoded = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                secret = Path(tmp) / "secret"
                secret.write_text("synthetic-key")
                secret.chmod(0o600)
                base = "http://127.0.0.1:" + str(server.server_port)
                n = m.Notice("1086:101", "中文 & 标题", "2026-10-14", m.PAGE_URL)
                m.send_push({"type": "bark", "server": base, "key_file": str(secret)}, n)
                m.send_push({"type": "ntfy", "server": base, "topic_file": str(secret), "token_file": str(secret)}, n)
                self.assertEqual(received[0][1]["device_key"], "synthetic-key")
                self.assertEqual(received[0][1]["url"], m.PAGE_URL)
                self.assertEqual(received[1][1]["topic"], "synthetic-key")
                self.assertEqual(received[1][1]["click"], m.PAGE_URL)
                self.assertEqual(received[1][2], "Bearer synthetic-key")
                secret.chmod(0o644)
                with self.assertRaises(m.MonitorError):
                    m.read_secret(secret)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_provider_rejection_not_counted_as_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "secret"
            secret.write_text("synthetic-key")
            secret.chmod(0o600)
            for provider, response in (("bark", b'{"code":400}'), ("ntfy", b'{"error":"no"}')):
                target = {"type": provider, "server": "https://example.test", "key_file": str(secret), "topic_file": str(secret)}
                with patch.object(m, "http_read", return_value=(response, "utf-8")):
                    with self.assertRaises(m.MonitorError):
                        m.send_push(target, m.Notice("test", "标题", "2026-10-14", m.PAGE_URL))


if __name__ == "__main__":
    unittest.main(verbosity=2)
