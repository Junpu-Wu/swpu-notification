import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor as m
from ai_content import FOOTER


class DeliveryScopeTests(unittest.TestCase):
    def test_existing_pending_and_new_queue_obey_scope_and_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = m.connect(Path(tmp) / 'state.sqlite3')
            try:
                n = m.Notice('new', '新公告', '2026-10-20', m.PAGE_URL)
                m.ingest(con, [n])
                targets = [{'id': 'my-iphone', 'enabled': True}, {'id': 'friend', 'enabled': True}]
                m.queue_targets(con, targets)
                cfg = {'delivery_allowlist': ['my-iphone']}
                with patch.object(m, 'prepare_content', return_value={'text': '模拟正文', 'status': 'fallback', 'reason': 'test', 'images': 0}), patch.object(m, 'send_push') as send:
                    self.assertTrue(m.deliver_one(con, targets, cfg))
                    self.assertEqual(send.call_args.args[0]['id'], 'my-iphone')
                    self.assertFalse(m.deliver_one(con, targets, cfg))
                self.assertEqual(con.execute("SELECT state FROM deliveries WHERE target='friend'").fetchone()[0], 'pending')
                targets[1]['since'] = m.time.time() + 1
                with patch.object(m, 'send_push') as send:
                    self.assertFalse(m.deliver_one(con, targets, {}))
                    send.assert_not_called()
            finally:
                con.close()

    def test_test_all_and_explicit_id_are_restricted(self):
        cfg = {'targets': [{'id': 'my-iphone', 'enabled': True}, {'id': 'friend', 'enabled': True}],
               'delivery_allowlist': ['my-iphone']}
        n = m.Notice('existing', '已有公告', '2026-10-20', m.PAGE_URL)
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'load_config', return_value=cfg), patch.object(m, 'fetch_page', return_value=([n], None)), patch.object(m, 'prepare_content', return_value={'text': '模拟正文', 'status': 'fallback', 'reason': 'test', 'images': 0}), patch.object(m, 'send_push') as send:
            with patch('sys.argv', ['monitor.py', '--config', 'unused', '--state-dir', tmp, '--test-latest']):
                self.assertEqual(m.main(), 0)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[0]['id'], 'my-iphone')
            send.reset_mock()
            with patch('sys.argv', ['monitor.py', '--config', 'unused', '--state-dir', tmp, '--test-latest', 'friend']):
                self.assertEqual(m.main(), 2)
            send.assert_not_called()
        with self.assertRaises(m.MonitorError):
            m.send_push({'id': 'friend'}, n, cfg=cfg)

    def test_full_bark_json_limit_keeps_link_and_footer(self):
        n = m.Notice('long', '考试通知🙂' * 15, '2026-10-20',
                     'https://www.swpu.edu.cn/Office/Xbgw_contentx.jsp?urltype=news.NewsContentUrl&wbtreeid=1312&wbnewsid=45778')
        target = {'id': 'my-iphone', 'type': 'bark', 'server': 'https://api.day.app', 'key_file': 'unused'}
        content = {'text': '考试时间 2026-10-26 9:00。\\“🙂”\n' * 1000, 'images': 0}
        with patch.object(m, 'read_secret', return_value='synthetic-key'), patch.object(m, 'http_read', return_value=(b'{"code":200}', 'utf-8')) as call:
            m.send_push(target, n, content=content)
            encoded = call.call_args.kwargs['data']
            payload = json.loads(encoded)
            self.assertLessEqual(len(encoded), 3500)
            self.assertTrue(payload['body'].endswith('原文：' + n.url + '\n' + FOOTER))
            self.assertIn('正文已节选', payload['body'])
            self.assertEqual(payload['url'], n.url)

    def test_ai_content_is_reused_and_pipeline_stays_outside_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = m.connect(Path(tmp) / 'state.sqlite3')
            try:
                n = m.Notice('new', '新公告', '2026-10-20', m.PAGE_URL)
                m.ingest(con, [n])
                target = {'id': 'my-iphone'}
                cfg = {'ai': {'enabled': True}, 'delivery_allowlist': ['my-iphone']}
                def prepare(con, *args):
                    self.assertFalse(con.in_transaction)
                    return {'text': '原文段落', 'images': 0, 'status': 'ai', 'reason': 'ok'}
                with patch.object(m, 'prepare_content', side_effect=prepare) as prepare_call, patch.object(m, 'send_push') as send:
                    self.assertTrue(m.deliver_one(con, [target], cfg))
                    self.assertEqual(send.call_args.kwargs['content']['text'], '原文段落')
                    self.assertFalse(m.deliver_one(con, [target], cfg))
                    self.assertEqual(prepare_call.call_count, 1)
            finally:
                con.close()


if __name__ == '__main__':
    unittest.main()
