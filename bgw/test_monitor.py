import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("monitor", HERE / "monitor.py")
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


HTML = """
<html><body><ul>
<li id="line_u2_0"><a href="../info/1312/42168.htm" target="_blank">置顶旧公告<span class="fl_right">2026-06-16</span></a></li>
<li id="line_u2_1"><a href="../info/1312/45878.htm" title="文艺作品征集通知全文">缩略标题<span class="fl_right">2026-10-19</span></a></li>
<li id="line_u2_2"><a href="../Office/Xbgw_contentx.jsp?urltype=news.NewsContentUrl&wbnewsid=45848&wbtreeid=1312">动态文章<span class="fl_right"><em>2026-10-16</em></span></a></li>
<li id="line_u2_3"><a href="../Office/Xbgw_contentx.jsp?wbtreeid=1312&amp;wbnewsid=45878&amp;urltype=news.NewsContentUrl">同一文章另一链接<span class="fl_right">2026-10-19</span></a></li>
</ul><a href="zytzgg/131.htm">下页</a></body></html>
"""


def notice_row(href, date="2026-10-19", title="公告"):
    return '<li><a href="' + href + '">' + title + '<span class="fl_right">' + date + '</span></a></li>'


class MonitorTests(unittest.TestCase):
    def setUp(self):
        clock = mock.patch.object(monitor, 'today', return_value=monitor.dt.date(2026, 10, 20))
        clock.start()
        self.addCleanup(clock.stop)

    def test_real_page_shapes_deduplication_and_pagination(self):
        rows, following = monitor.parse_page(HTML, monitor.PAGE_URL)
        self.assertEqual([n.uid for n in rows], ["bgw:1312:42168", "bgw:1312:45878", "bgw:1312:45848"])
        self.assertEqual([n.date for n in rows], ["2026-06-16", "2026-10-19", "2026-10-16"])
        self.assertEqual([n.title for n in rows], ["置顶旧公告", "文艺作品征集通知全文", "动态文章"])
        self.assertEqual(following, "https://www.swpu.edu.cn/bgw/zytzgg/131.htm")
        self.assertEqual(rows[1].url, "https://www.swpu.edu.cn/info/1312/45878.htm")
        self.assertIn("Office/Xbgw_contentx.jsp", rows[2].url)
        self.assertNotIn(rows[0].date, rows[0].title)

    def test_static_dynamic_identity_and_query_order(self):
        expected = "bgw:1312:45878"
        urls = ["https://www.swpu.edu.cn/info/1312/45878.htm",
                "https://www.swpu.edu.cn/Office/Xbgw_contentx.jsp?urltype=news.NewsContentUrl&wbnewsid=45878&wbtreeid=1312",
                "https://www.swpu.edu.cn/Office/Xbgw_contentx.jsp?wbtreeid=1312&wbnewsid=45878&urltype=news.NewsContentUrl"]
        self.assertEqual([monitor.article_id(url) for url in urls], [expected] * 3)
        html = "".join(notice_row(url, title=str(i)) for i, url in enumerate(urls))
        rows, _ = monitor.parse_page(html, monitor.PAGE_URL)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "0")
        self.assertEqual(rows[0].url, urls[0])

    def test_external_other_paths_categories_credentials_and_ports_are_rejected(self):
        invalid = ["http://www.swpu.edu.cn/info/1312/1.htm",
                   "https://example.com/info/1312/1.htm",
                   "https://www.swpu.edu.cn.evil.test/info/1312/1.htm",
                   "https://www.swpu.edu.cn/gs/info/1312/1.htm",
                   "https://www.swpu.edu.cn/bgw/info/1312/1.htm",
                   "https://www.swpu.edu.cn/info/1088/1.htm",
                   "https://name@www.swpu.edu.cn/info/1312/1.htm",
                   "https://name:pass@www.swpu.edu.cn/info/1312/1.htm",
                   "https://www.swpu.edu.cn:443/info/1312/1.htm",
                   "https://www.swpu.edu.cn:bad/info/1312/1.htm",
                   "https://www.swpu.edu.cn/info/1312/1.htm?x=1",
                   "https://www.swpu.edu.cn/info/1312/1.htm#body",
                   "https://www.swpu.edu.cn/Office/other.jsp?wbtreeid=1312&wbnewsid=1",
                   "https://www.swpu.edu.cn/Office/Xbgw_contentx.jsp?wbtreeid=1088&wbnewsid=1",
                   "https://www.swpu.edu.cn/Office/Xbgw_contentx.jsp?wbtreeid=1312&wbnewsid=1&wbnewsid=2",
                   "https://www.swpu.edu.cn/Office/Xbgw_contentx.jsp?wbtreeid=1312&wbtreeid=1088&wbnewsid=1",
                   "https://www.swpu.edu.cn/Office/Xbgw_contentx.jsp?wbtreeid=1312&wbnewsid=abc",
                   "https://www.swpu.edu.cn/Office/Xbgw_contentx.jsp?wbtreeid=1312&wbnewsid=",
                   "https://[bad"]
        for url in invalid:
            with self.subTest(url=url):
                self.assertIsNone(monitor.article_id(url))

    def test_invalid_dates_or_missing_date_are_not_notices(self):
        for date in ("2026-02-30", "2026-13-01", "2026/10/19", "26-10-19", "", "2026-10-19 later"):
            with self.subTest(date=date), self.assertRaises(monitor.MonitorError):
                monitor.parse_page(notice_row("../info/1312/1.htm", date), monitor.PAGE_URL)
        rows, _ = monitor.parse_page(notice_row("../info/1312/1.htm", " 2026-10-19 "), monitor.PAGE_URL)
        self.assertEqual(rows[0].date, "2026-10-19")

    def test_nested_date_markup_and_title_whitespace(self):
        html = '<li><a href="../info/1312/1.htm">第一段 <b>标题</b>\n最后<span class="other fl_right"><i>2026-10-19</i></span></a></li>'
        rows, _ = monitor.parse_page(html, monitor.PAGE_URL)
        self.assertEqual(rows[0].title, "第一段 标题 最后")
        self.assertEqual(rows[0].date, "2026-10-19")

    def test_pagination_is_same_site_and_expected_directory_only(self):
        base = notice_row("../info/1312/1.htm")
        invalid = ["https://evil.test/bgw/zytzgg/131.htm", "https://www.swpu.edu.cn:443/bgw/zytzgg/131.htm",
                   "https://name@www.swpu.edu.cn/bgw/zytzgg/131.htm", "../gs/tzgg/14.htm",
                   "zytzgg/131.htm?x=1", "zytzgg/131.htm#x", "zytzgg/not-page.htm"]
        for href in invalid:
            with self.subTest(href=href):
                _, following = monitor.parse_page(base + '<a href="' + href + '">下页</a>', monitor.PAGE_URL)
                self.assertIsNone(following)
        html = notice_row("../../info/1312/2.htm") + '<a href="130.htm">下页</a>'
        rows, following = monitor.parse_page(html, "https://www.swpu.edu.cn/bgw/zytzgg/131.htm")
        self.assertEqual(rows[0].uid, "bgw:1312:2")
        self.assertEqual(following, "https://www.swpu.edu.cn/bgw/zytzgg/130.htm")

    def test_latest_uses_publication_date_and_preserves_same_day_page_order(self):
        rows, _ = monitor.parse_page(HTML, monitor.PAGE_URL)
        self.assertEqual(monitor.latest_notice(rows).uid, "bgw:1312:45878")
        tied = monitor.Notice("bgw:1312:99999", "同日另一条", "2026-10-19", "https://www.swpu.edu.cn/info/1312/99999.htm")
        self.assertEqual(monitor.latest_notice(rows + [tied]).uid, "bgw:1312:45878")
        with self.assertRaises(monitor.MonitorError):
            monitor.latest_notice([])

    def test_latest_cli_uses_latest_date_with_scope(self):
        rows, _ = monitor.parse_page(HTML, monitor.PAGE_URL)
        cfg = {"ai": {"enabled": True}, "delivery_allowlist": ["my-iphone"],
               "targets": [{"id": "my-iphone", "enabled": True}, {"id": "friend", "enabled": True}]}
        content = {"text": "模拟正文", "status": "ai", "reason": "ok", "images": 0}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(monitor, "load_config", return_value=cfg), mock.patch.object(monitor, "fetch_page", return_value=(rows, None)), mock.patch.object(monitor, "prepare_content", return_value=content), mock.patch.object(monitor, "send_push") as send, mock.patch("sys.argv", ["monitor.py", "--config", "unused", "--state-dir", tmp, "--test-latest"]):
            self.assertEqual(monitor.main(), 0)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[0]["id"], "my-iphone")
            self.assertEqual(send.call_args.args[1].uid, "bgw:1312:45878")

    def test_thirty_to_sixty_minute_config_is_valid_and_gs_url_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = {"page_url": monitor.PAGE_URL, "interval_min_seconds": 1800, "interval_max_seconds": 3600, "targets": []}
            path.write_text(json.dumps(cfg), encoding="utf-8")
            parsed = monitor.load_config(path)
            self.assertEqual((parsed["interval_min_seconds"], parsed["interval_max_seconds"]), (1800, 3600))
            cfg["page_url"] = "https://www.swpu.edu.cn/gs/tzgg.htm"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with self.assertRaises(monitor.MonitorError):
                monitor.load_config(path)

    def test_baseline_and_restart_do_not_create_deliveries(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite3"
            notices, _ = monitor.parse_page(HTML, monitor.PAGE_URL)
            with monitor.contextlib.closing(monitor.connect(db)) as con, mock.patch.object(monitor, "fetch_page", return_value=(notices, None)):
                monitor.poll(con)
                self.assertIsNotNone(monitor.meta(con, "baseline_at"))
                self.assertEqual(con.execute("SELECT count(*) FROM notices").fetchone()[0], 3)
                monitor.poll(con)
                monitor.queue_targets(con, [{"id": "phone", "since": 0}])
                self.assertEqual(con.execute("SELECT count(*) FROM deliveries").fetchone()[0], 0)
            with monitor.contextlib.closing(monitor.connect(db)) as con:
                self.assertIsNotNone(monitor.meta(con, "baseline_at"))
                self.assertEqual(monitor.ingest(con, notices), 0)
                monitor.queue_targets(con, [{"id": "phone", "since": 0}])
                self.assertEqual(con.execute("SELECT count(*) FROM deliveries").fetchone()[0], 0)

    def test_new_article_is_queued_once_and_retry_is_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite3"
            target = {"id": "phone", "type": "bark", "enabled": True, "server": "https://api.day.app", "key_file": "/unused"}
            cfg = {"interval_min_seconds": 1800, "interval_max_seconds": 3600}
            old = monitor.Notice("bgw:1312:1", "旧", "2026-10-19", "https://www.swpu.edu.cn/info/1312/1.htm")
            new = monitor.Notice("bgw:1312:2", "新", "2026-10-20", "https://www.swpu.edu.cn/info/1312/2.htm")
            with monitor.contextlib.closing(monitor.connect(db)) as con:
                monitor.ingest(con, [old], baseline=True)
                monitor.ingest(con, [new])
                started = time.time()
                with mock.patch.object(monitor, "prepare_content", return_value={"text": "模拟正文", "status": "fallback", "reason": "test", "images": 0}), mock.patch.object(monitor, "send_push", side_effect=OSError("offline")):
                    self.assertTrue(monitor.deliver_one(con, [target], cfg))
                row = con.execute("SELECT state,attempts,last_error,next_attempt FROM deliveries").fetchone()
                self.assertEqual((row[0], row[1], row[2]), ("pending", 1, "OSError"))
                self.assertGreaterEqual(row[3], started + 30)
                self.assertLessEqual(row[3], time.time() + 180)
                monitor.queue_targets(con, [target])
                self.assertEqual(con.execute("SELECT count(*) FROM deliveries").fetchone()[0], 1)

    def test_known_pinned_notice_does_not_hide_gap_after_long_downtime(self):
        pin = monitor.Notice("bgw:1312:1", "旧置顶", "2026-06-16", "https://www.swpu.edu.cn/info/1312/1.htm")
        old = monitor.Notice("bgw:1312:2", "旧末尾", "2026-09-01", "https://www.swpu.edu.cn/info/1312/2.htm")
        latest = monitor.Notice("bgw:1312:5", "最新", "2026-10-19", "https://www.swpu.edu.cn/info/1312/5.htm")
        missing = monitor.Notice("bgw:1312:4", "离线期间文章", "2026-10-18", "https://www.swpu.edu.cn/info/1312/4.htm")
        next_page = "https://www.swpu.edu.cn/bgw/zytzgg/131.htm"
        with tempfile.TemporaryDirectory() as tmp, monitor.contextlib.closing(monitor.connect(Path(tmp) / "state.sqlite3")) as con:
            monitor.ingest(con, [pin, old], baseline=True)
            with mock.patch.object(monitor, "fetch_page", side_effect=[([pin, latest], next_page), ([missing, old], None)]) as fetch:
                monitor.poll(con)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(fetch.call_args.args[0], next_page)
            self.assertEqual(con.execute("SELECT is_new FROM notices WHERE uid=?", (missing.uid,)).fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM notices WHERE is_new=1").fetchone()[0], 2)
            self.assertFalse(monitor.meta(con, "recovery_cursor"))

    def test_failed_older_page_exposes_no_partial_batch_and_retries_from_homepage(self):
        pin = monitor.Notice("bgw:1312:1", "旧置顶", "2026-06-16", "https://www.swpu.edu.cn/info/1312/1.htm")
        old = monitor.Notice("bgw:1312:2", "旧末尾", "2026-09-01", "https://www.swpu.edu.cn/info/1312/2.htm")
        latest = monitor.Notice("bgw:1312:5", "最新", "2026-10-19", "https://www.swpu.edu.cn/info/1312/5.htm")
        missing = monitor.Notice("bgw:1312:4", "离线期间文章", "2026-10-18", "https://www.swpu.edu.cn/info/1312/4.htm")
        next_page = "https://www.swpu.edu.cn/bgw/zytzgg/131.htm"
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite3"
            with monitor.contextlib.closing(monitor.connect(db)) as con:
                monitor.ingest(con, [pin, old], baseline=True)
                with mock.patch.object(monitor, "fetch_page", side_effect=[([pin, latest], next_page), OSError("offline")]):
                    with self.assertRaises(OSError):
                        monitor.poll(con)
                self.assertFalse(monitor.meta(con, "recovery_cursor"))
                self.assertIsNone(con.execute("SELECT uid FROM notices WHERE uid=?", (latest.uid,)).fetchone())
            with monitor.contextlib.closing(monitor.connect(db)) as con:
                # The current homepage now overlaps, but the saved cursor remains.
                with mock.patch.object(monitor, "fetch_page", side_effect=[([pin, latest], next_page), ([missing, old], None)]) as fetch:
                    monitor.poll(con)
                self.assertEqual(fetch.call_count, 2)
                self.assertEqual(con.execute("SELECT is_new FROM notices WHERE uid=?", (missing.uid,)).fetchone()[0], 1)
                self.assertFalse(monitor.meta(con, "recovery_cursor"))

    def test_bark_group_and_ai_test_title_are_distinct(self):
        target = {"id": "my-iphone", "type": "bark", "server": "https://api.day.app", "key_file": "unused"}
        notice = monitor.Notice("bgw:1312:1", "公告标题", "2026-10-19", "https://www.swpu.edu.cn/info/1312/1.htm")
        with mock.patch.object(monitor, "read_secret", return_value="synthetic"), mock.patch.object(monitor, "http_read", return_value=(b'{"code":200}', "utf-8")) as call:
            monitor.send_push(target, notice, test=True, cfg={"ai": {"enabled": True}}, content={"text": "正文", "images": 0})
            payload = json.loads(call.call_args.kwargs["data"])
            self.assertEqual(payload["group"], "西南石油大学重要通知")
            self.assertEqual(payload["title"], "学校重要通知")
            self.assertTrue(payload["body"].endswith("点击通知查看原文。\n内容含AI处理，请关注原文。"))
            self.assertNotIn(notice.url, payload["body"])


if __name__ == "__main__":
    unittest.main()
