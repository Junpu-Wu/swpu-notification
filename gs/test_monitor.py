import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("monitor", HERE / "monitor.py")
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


HTML = """
<html><body><table>
<tr><td><a href="info/1088/8700.htm" title="静态文章">静态文章</a></td>
<td><span class="timestyle200922">2026/10/08&nbsp;</span></td></tr>
<tr><td><a href="content.jsp?urltype=news.NewsContentUrl&amp;wbtreeid=1088&amp;wbnewsid=8730"
title="动态文章">动态文章</a></td><td><span class="timestyle200922">2026/10/09&nbsp;</span></td></tr>
</table><a href="tzgg/14.htm">下页</a></body></html>
"""


class MonitorTests(unittest.TestCase):
    def test_real_page_shapes_and_pagination(self):
        rows, following = monitor.parse_page(HTML, monitor.PAGE_URL)
        self.assertEqual([n.uid for n in rows], ["gs:1088:8700", "gs:1088:8730"])
        self.assertEqual([n.date for n in rows], ["2026-10-08", "2026-10-09"])
        self.assertEqual(following, "https://www.swpu.edu.cn/gs/tzgg/14.htm")
        self.assertIn("content.jsp", rows[1].url)

    def test_wrong_site_or_category_is_rejected(self):
        self.assertIsNone(monitor.article_id("https://example.com/gs/info/1088/1.htm"))
        self.assertIsNone(monitor.article_id("https://www.swpu.edu.cn/gs/info/1086/1.htm"))

    def test_thirty_to_sixty_minute_config_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"page_url": monitor.PAGE_URL,
                                        "interval_min_seconds": 1800,
                                        "interval_max_seconds": 3600,
                                        "targets": []}), encoding="utf-8")
            cfg = monitor.load_config(path)
            self.assertEqual((cfg["interval_min_seconds"], cfg["interval_max_seconds"]),
                             (1800, 3600))

    def test_baseline_and_restart_do_not_create_deliveries(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite3"
            notices, _ = monitor.parse_page(HTML, monitor.PAGE_URL)
            with monitor.contextlib.closing(monitor.connect(db)) as con:
                self.assertEqual(monitor.ingest(con, notices, baseline=True), 2)
                self.assertEqual(monitor.ingest(con, notices), 0)
                monitor.queue_targets(con, [{"id": "phone", "since": 0}])
                self.assertEqual(con.execute("SELECT count(*) FROM deliveries").fetchone()[0], 0)

    def test_new_article_is_queued_once_and_retry_is_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite3"
            target = {"id": "phone", "type": "bark", "enabled": True,
                      "server": "https://api.day.app", "key_file": "/unused"}
            cfg = {"interval_min_seconds": 1800, "interval_max_seconds": 3600}
            old = monitor.Notice("gs:1088:1", "旧", "2026-10-01",
                                 "https://www.swpu.edu.cn/gs/info/1088/1.htm")
            new = monitor.Notice("gs:1088:2", "新", "2026-10-02",
                                 "https://www.swpu.edu.cn/gs/info/1088/2.htm")
            with monitor.contextlib.closing(monitor.connect(db)) as con:
                monitor.ingest(con, [old], baseline=True)
                monitor.ingest(con, [new])
                started = time.time()
                with mock.patch.object(monitor, "send_push", side_effect=OSError("offline")):
                    self.assertTrue(monitor.deliver_one(con, [target], cfg))
                row = con.execute("SELECT state,attempts,last_error,next_attempt FROM deliveries").fetchone()
                self.assertEqual((row[0], row[1], row[2]), ("pending", 1, "OSError"))
                self.assertGreaterEqual(row[3], started + 30)
                self.assertLessEqual(row[3], time.time() + 180)
                monitor.queue_targets(con, [target])
                self.assertEqual(con.execute("SELECT count(*) FROM deliveries").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
