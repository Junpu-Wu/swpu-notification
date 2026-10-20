import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor as m
from ai_content import FOOTER

SOURCE_TITLE = "学校重要通知"


class PushFormatTests(unittest.TestCase):
    def test_all_channels_have_normal_source_title_and_one_click_link(self):
        n = m.Notice("test", "课程考试通知 https://title.example.test/42", "2026-10-20", m.PAGE_URL)
        content = {"text": "考试时间：2026年10月26日9:00。\n原文：" + n.url + "\n重复：" + n.url
                   + "\n附件： https://attachments.example.test/notice.pdf\n更多：www.example.test/path", "images": 0}
        providers = (("bark", b'{"code":200}', "body", "url", "title"),
                     ("ntfy", b'{"id":"test","event":"message"}', "message", "click", "title"),
                     ("wxpusher", b'{"code":1000,"data":[{"code":1000}]}', "content", "url", "summary"))
        for provider, response, body_key, url_key, title_key in providers:
            target = {"id": "phone", "type": provider, "server": "https://push.example.test",
                      "key_file": "unused", "topic_file": "unused", "spt_file": "unused"}
            for is_test in (False, True):
                for ai_enabled in (False, True):
                    with self.subTest(provider=provider, test=is_test, ai=ai_enabled), patch.object(m, "read_secret", return_value="SPT_synthetic_key_only"), patch.object(m, "http_read", return_value=(response, "utf-8")) as call:
                        m.send_push(target, n, test=is_test, cfg={"delivery_allowlist": ["phone"], "ai": {"enabled": ai_enabled}}, content=content)
                        payload = json.loads(call.call_args.kwargs["data"])
                        body = payload[body_key]
                        self.assertEqual(payload[title_key], SOURCE_TITLE)
                        self.assertNotIn("测试", payload[title_key])
                        self.assertNotIn("AI", payload[title_key])
                        self.assertEqual(payload[url_key], n.url)
                        self.assertEqual(json.dumps(payload, ensure_ascii=False).count(n.url), 1)
                        self.assertNotRegex(body, re.compile(r"https?://|www\.", re.I))
                        self.assertNotIn(n.url, body)
                        self.assertTrue(body.startswith("课程考试通知"))
                        self.assertIn("2026年10月26日9:00", body)
                        self.assertTrue(body.endswith("点击通知查看原文。\n" + FOOTER))

    def test_long_content_each_transport_keeps_budget_one_url_and_footer(self):
        n = m.Notice("long", "考试通知🙂" * 15, "2026-10-20", m.PAGE_URL)
        content = {"text": "考试时间 2026-10-26 9:00。\\“🙂”\n" * 5000, "images": 1}
        providers = (("bark", b'{"code":200}', "body", "url", 3500),
                     ("ntfy", b'{"id":"test","event":"message"}', "message", "click", 3500),
                     ("wxpusher", b'{"code":1000,"data":[{"code":1000}]}', "content", "url", 39000))
        for provider, response, body_key, url_key, budget in providers:
            for is_test in (False, True):
                target = {"type": provider, "server": "https://push.example.test", "key_file": "unused", "topic_file": "unused", "spt_file": "unused"}
                with self.subTest(provider=provider, test=is_test), patch.object(m, "read_secret", return_value="SPT_synthetic_key_only"), patch.object(m, "http_read", return_value=(response, "utf-8")) as call:
                    m.send_push(target, n, test=is_test, content=content)
                    encoded = call.call_args.kwargs["data"]
                    payload = json.loads(encoded)
                    self.assertLessEqual(len(encoded), budget)
                    self.assertEqual(payload[url_key], n.url)
                    self.assertEqual(json.dumps(payload, ensure_ascii=False).count(n.url), 1)
                    self.assertNotRegex(payload[body_key], re.compile(r"https?://|www\.", re.I))
                    self.assertTrue(payload[body_key].endswith("点击通知查看原文。\n" + FOOTER))
                    self.assertIn("正文已节选", payload[body_key])
                    self.assertIn("1 张图片", payload[body_key])

    def test_testing_still_rejects_recipient_outside_scope_before_io(self):
        notice = m.Notice("scope", "公告", "2026-10-20", m.PAGE_URL)
        cfg = {"delivery_allowlist": ["my-iphone"]}
        with patch.object(m, "read_secret") as secret, patch.object(m, "http_read") as call:
            for is_test in (False, True):
                with self.subTest(test=is_test), self.assertRaises(m.MonitorError):
                    m.send_push({"id": "friend"}, notice, test=is_test, cfg=cfg)
            secret.assert_not_called()
            call.assert_not_called()

    def test_temporary_explicit_all_recipient_scope_does_not_queue_backlog(self):
        ids = ["my-iphone", "classmate-iphone", "my-wxpusher", "wxpusher-client", "bark-client"]
        targets = [{"id": ident, "enabled": True} for ident in ids]
        targets.append({"id": "disabled-ntfy", "enabled": False})
        cfg = {"targets": targets, "delivery_allowlist": ids}
        notice = m.Notice("latest", "已有公告", "2026-10-20", m.PAGE_URL)
        content = {"text": "模拟正文", "status": "ai", "reason": "ok", "images": 0}
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, "load_config", return_value=cfg), patch.object(m, "fetch_page", return_value=([notice], None)), patch.object(m, "prepare_content", return_value=content), patch.object(m, "send_push") as send, patch("sys.argv", ["monitor.py", "--config", "unused", "--state-dir", tmp, "--test-latest"]):
            self.assertEqual(m.main(), 0)
            self.assertEqual([call.args[0]["id"] for call in send.call_args_list], ids)
            self.assertTrue(all(call.kwargs["test"] for call in send.call_args_list))
            with m.contextlib.closing(m.connect(Path(tmp) / "notices.sqlite3")) as con:
                self.assertEqual(con.execute("SELECT count(*) FROM notices").fetchone()[0], 0)
                self.assertEqual(con.execute("SELECT count(*) FROM deliveries").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
