import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("ai_under_test", Path(__file__).with_name("ai_content.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "state.db")
        self.con = sqlite3.connect(self.path)
        self.notice = SimpleNamespace(uid="42", title="测试通知", date="2026-10-20", url="https://www.swpu.edu.cn/gs/info/1/42.htm")
        self.cfg = {"ai": {"enabled": True, "key_file": "/unused"}}
        self.html = '<div class="v_news_content"><p>截止日期：2026年10月30日</p><p>金额100元。</p></div>'
        self.calls = []

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        if url == self.notice.url:
            return self.html.encode(), "utf-8"
        payload = json.loads(kwargs["data"])
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        return json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": '{"keep_ids":[0,1]}'}}]}).encode(), "utf-8"

    def prepare(self, fetch=None):
        return m.prepare_content(self.con, self.notice, self.cfg, fetch or self.fetch, lambda _: "test-key")

    def test_word_css_table_order_images_and_external_attachments(self):
        html = '<nav>导航</nav><div id="vsb_content_42"><style>.MsoNormal{color:red}</style><p class="MsoNormal">第一段 &amp; A</p><script>evil()</script><table><tr><td>姓名</td><td>人数</td></tr><tr><td>甲</td><td>2</td></tr></table><p>最后<img src="x.jpg"/><br/>正文</p></div><div><a href="/download/file.pdf">附件一</a></div>'
        blocks, images, found = m.extract_article(html, self.notice.url)
        self.assertTrue(found)
        self.assertEqual(images, 1)
        self.assertEqual(blocks[:4], ["第一段 & A", "姓名 | 人数", "甲 | 2", "最后\n正文"])
        self.assertIn("https://www.swpu.edu.cn/download/file.pdf", blocks[-1])
        self.assertNotIn("evil", "".join(blocks))
        self.assertNotIn("MsoNormal", "".join(blocks))

    def test_missing_node_never_accepts_navigation_as_body(self):
        self.html = '<html><nav>通知导航：2026</nav><p>maintenance</p></html>'
        result = self.prepare()
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["reason"], "article_body_unavailable")
        self.assertNotIn("导航", result["text"])
        self.assertEqual(len(self.calls), 1)

    def test_unique_originals_must_all_remain(self):
        blocks = ["金额100元", "截止10月30日", "金额100元"]
        self.assertEqual(m.select_blocks('{"keep_ids":[0,1]}', blocks), blocks[:2])
        for result in ('{"keep_ids":[0]}', '{"keep_ids":[1,0]}', '{"keep_ids":[0,0,1]}', '{"keep_ids":[true,1]}', '{"keep_ids":[0,1,9]}', '{"keep_ids":[0,1],"text":"改写"}', 'not json'):
            with self.assertRaises((ValueError, TypeError)):
                m.select_blocks(result, blocks)

    def test_success_cache_survives_restart_and_preserves_dates(self):
        result = self.prepare()
        self.assertEqual(result["status"], "ai")
        self.assertIn("2026年10月30日", result["text"])
        self.assertEqual(len(self.calls), 2)
        self.con.close()
        self.con = sqlite3.connect(self.path)
        again = self.prepare(lambda *a, **k: self.fail("cached article fetched again"))
        self.assertEqual(again, result)
        row = self.con.execute("SELECT html_hash,expires FROM article_content_cache").fetchone()
        self.assertEqual(len(row[0]), 64)
        self.assertEqual(row[1], 0)

    def test_cache_changes_when_model_changes(self):
        self.prepare()
        self.cfg["ai"]["model"] = "alternative-model"
        self.prepare()
        self.assertEqual(len(self.calls), 4)

    def test_model_failures_use_original_and_short_lived_cache(self):
        def fail_api(url, **kwargs):
            if url == self.notice.url:
                return self.html.encode(), "utf-8"
            raise TimeoutError("secret-response-body")
        with patch.object(m.time, "time", return_value=100):
            result = self.prepare(fail_api)
        self.assertEqual(result["status"], "fallback")
        self.assertIn("100元", result["text"])
        self.assertNotIn("secret", json.dumps(result))
        with patch.object(m.time, "time", return_value=101):
            self.assertEqual(self.prepare(lambda *a, **k: self.fail("fallback refetched immediately")), result)
        with patch.object(m.time, "time", return_value=401):
            self.assertEqual(self.prepare()["status"], "ai")

    def test_invalid_or_incomplete_model_json_falls_back_to_original(self):
        for raw in ('not-json', '{"keep_ids":[0]}'):
            with self.subTest(raw=raw):
                self.con.execute("DELETE FROM article_content_cache") if self.con.execute("SELECT name FROM sqlite_master WHERE name='article_content_cache'").fetchone() else None
                self.con.commit()
                def wrong(url, **kwargs):
                    if url == self.notice.url:
                        return self.html.encode(), "utf-8"
                    return json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": raw}}]}).encode(), "utf-8"
                result = self.prepare(wrong)
                self.assertEqual(result["status"], "fallback")
                self.assertIn("金额100元", result["text"])

    def test_fetch_failure_explicit_and_safe(self):
        def failing(*args, **kwargs):
            raise OSError("private-key-must-not-appear")
        result = self.prepare(failing)
        self.assertEqual(result["reason"], "article_fetch_failed")
        self.assertIn("原文", result["text"])
        self.assertNotIn("private-key", str(result))

    def test_image_only_marks_no_ocr_and_skips_model(self):
        self.html = '<div class="v_news_content"><p><img src="notice.jpg"></p></div>'
        result = self.prepare()
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["images"], 1)
        self.assertIn("图片内容未识别", result["text"])
        self.assertEqual(len(self.calls), 1)

    def test_utf8_truncation_keeps_link_footer_and_image_warning(self):
        content = {"text": "公告😀中文" * 1000, "images": 2}
        result = m.render_body(self.notice, content, max_bytes=800)
        self.assertLessEqual(len(result.encode()), 800)
        self.assertIn("正文已节选", result)
        self.assertIn("图片内容未识别", result)
        self.assertNotIn(self.notice.url, result)
        self.assertIn("点击通知查看原文。", result)
        self.assertTrue(result.endswith(m.FOOTER))
        self.assertNotIn("�", result)
        with self.assertRaises(ValueError):
            m.render_body(self.notice, content, max_bytes=10)

    def test_incomplete_finish_reason_falls_back(self):
        def truncated(url, **kwargs):
            if url == self.notice.url:
                return self.html.encode(), "utf-8"
            return json.dumps({"choices": [{"finish_reason": "length", "message": {"content": '{"keep_ids":[0,1]}'}}]}).encode(), "utf-8"
        result = self.prepare(truncated)
        self.assertEqual(result["status"], "fallback")
        self.assertIn("金额100元", result["text"])

    def test_no_external_navigation_pdf_or_missing_body_attachments(self):
        html = '<style><a href="/download/evil.pdf">evil</a></style><nav><a href="/download/nav.pdf">nav</a></nav><a href="/misc.pdf">unrelated</a><div class="v_news_content"><p>正文</p></div><a href="/misc.pdf">footer pdf</a><div class="attachments"><a href="/a.docx">附件</a></div>'
        blocks, _, _ = m.extract_article(html, self.notice.url)
        self.assertEqual(blocks, ["正文", "附件：https://www.swpu.edu.cn/a.docx"])
        blocks, _, found = m.extract_article('<a href="/download/no.pdf">附件</a>', self.notice.url)
        self.assertFalse(found)
        self.assertEqual(blocks, [])

    def test_stray_void_closing_tags_and_word_table_paragraphs(self):
        html = '<div class="v_news_content"><p>开始</p></unknown></img><table><tr><td><p>甲</p><p>乙</p></td><td><p>2</p></td></tr></table><p>末尾</p></div>'
        blocks, _, found = m.extract_article(html, self.notice.url)
        self.assertTrue(found)
        self.assertEqual(blocks[0], "开始")
        self.assertEqual(blocks[-1], "末尾")
        self.assertIn("甲", blocks[1])
        self.assertIn("乙", blocks[1])
        self.assertIn(" | ", blocks[1])
        self.assertIn("2", blocks[1])
        self.assertEqual(blocks[1], "甲 乙 | 2")

    def test_display_removes_multiple_links_but_preserves_labels_contacts_and_dates(self):
        text = ("原文：" + self.notice.url + "\n重复：" + self.notice.url + "\n"
                "附件一：https://www.swpu.edu.cn/download/通知.pdf\n"
                "[附件二](https://example.com/b.docx)\n"
                '<a href="https://example.com/c.xlsx">附件三</a>\n'
                "参阅www.example.com/a.htm联系人张三，截止2026年10月30日。\n"
                "邮箱：office@example.com 电话：13800138000\n"
                "<https://例子.测试/说明>。")
        self.notice.title = "测试通知 https://example.com/title.htm"
        result = m.render_body(self.notice, {"text": text, "images": 0})
        for prefix in ("http://", "https://", "www."):
            self.assertNotIn(prefix, result.lower())
        for value in ("附件一", "附件二", "附件三", "联系人张三", "2026年10月30日", "office@example.com", "13800138000"):
            self.assertIn(value, result)
        self.assertIn("链接与附件请在原文查看", result)
        self.assertTrue(result.endswith("点击通知查看原文。\n" + m.FOOTER))
        self.assertEqual(result.count(m.FOOTER), 1)

    def test_bare_unicode_urls_and_adjacent_prose_remain_safe(self):
        for original, retained in (
                ("见https://example.com/a.htm张三于2026-10-30提交", "张三于2026-10-30提交"),
                ("见https://example.com/通知.pdf李四电话13800138000", "李四电话13800138000"),
                ("见https://例子.测试/通知.pdf王五10月30日提交", "王五10月30日提交"),
                ("见https://www.学校.cn/通知.pdf王五负责", "王五负责"),
                ("网址https://example.com张三负责", "张三负责"),
                ("网址www.example.com截止2026年10月30日", "截止2026年10月30日")):
            with self.subTest(original=original):
                result = m.render_body(self.notice, {"text": original, "images": 0})
                self.assertIn(retained, result)
                self.assertNotIn("https://", result)
                self.assertNotIn("www.", result)

    def test_display_cleanup_applies_to_cached_text_without_mutating_cache(self):
        content = {"text": "附件一：" + self.notice.url + "\n处理日期2026-10-20", "images": 0, "status": "ai"}
        original = content.copy()
        first = m.render_body(self.notice, content)
        second = m.render_body(self.notice, content)
        self.assertEqual(content, original)
        self.assertEqual(first, second)
        self.assertNotIn(self.notice.url, first)
        self.assertIn("处理日期2026-10-20", first)

    def test_config_validation(self):
        m.validate_ai_config({})
        m.validate_ai_config(self.cfg)
        for ai in ({"enabled": "yes"}, {"enabled": True}, {"enabled": True, "key_file": "key", "timeout_seconds": 100}, {"enabled": True, "key_file": "key", "model": ""}):
            with self.assertRaises(ValueError):
                m.validate_ai_config({"ai": ai})


if __name__ == "__main__":
    unittest.main()
