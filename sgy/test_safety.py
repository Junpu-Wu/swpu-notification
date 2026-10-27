"""Incident regressions. Synthetic notices and mocked providers only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import monitor as m


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'db.sqlite3'
        self.con = m.connect(self.db)
        self.addCleanup(lambda: self.con.close())
        self.now = 1792540800.0
        clock = patch.object(m.time, 'time', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        date = patch.object(m, 'today', return_value=m.dt.date(2026, 10, 21))
        date.start()
        self.addCleanup(date.stop)
        self.target = {'id': 'phone', 'enabled': True}
        self.cfg = {}

    def notice(self, ident, date='2026-10-21'):
        return m.Notice('synthetic:' + str(ident), '合成公告', date, m.PAGE_URL)

    def baseline(self):
        n = self.notice('known', '2026-10-20')
        m.ingest(self.con, [n], baseline=True)
        return n

    def test_697_unseen_old_articles_cannot_become_new_or_requeue(self):
        self.baseline()
        rows = [self.notice(i, '2023-10-17') for i in range(697)]
        m.ingest(self.con, rows)
        m.queue_targets(self.con, [self.target])
        self.assertEqual(self.con.execute('SELECT count(*) FROM notices').fetchone()[0], 698)
        self.assertEqual(self.con.execute('SELECT count(*) FROM notices WHERE is_new=1').fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)
        self.assertFalse(m.meta(self.con, 'delivery_pause_reason'))

    def test_pinned_known_and_unseen_old_tail_do_not_walk_archives(self):
        known = self.baseline()
        pin = self.notice('pin', '2026-06-16')
        m.ingest(self.con, [pin], baseline=True)
        rows = [pin, self.notice('fresh'), known, self.notice('old-tail', '2023-10-17')]
        with patch.object(m, 'fetch_page', return_value=(rows, m.PAGE_URL + '/older')) as fetch:
            m.poll(self.con)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM notices WHERE is_new=1').fetchone()[0], 1)

    def test_six_candidates_pause_entire_batch_and_pause_survives_restart(self):
        known = self.baseline()
        with patch.object(m, 'fetch_page', return_value=([known] + [self.notice(i) for i in range(6)], None)):
            m.poll(self.con)
        self.assertEqual(m.meta(self.con, 'delivery_pause_reason'), 'candidate_batch_limit')
        self.assertEqual(self.con.execute('SELECT count(*) FROM safety_quarantine').fetchone()[0], 6)
        self.con.close()
        self.con = m.connect(self.db)
        m.ingest(self.con, [self.notice('later')])
        with patch.object(m, 'send_push') as send:
            self.assertFalse(m.deliver_one(self.con, [self.target], self.cfg))
            send.assert_not_called()
        self.assertEqual(self.con.execute('SELECT count(*) FROM notices WHERE is_new=1').fetchone()[0], 0)

    def test_failed_page_does_not_publish_partial_batch_to_worker(self):
        self.baseline()
        def fetch(url, **kwargs):
            if url == m.PAGE_URL:
                return [self.notice('fresh')], 'older-page'
            other = m.connect(self.db)
            try:
                m.queue_targets(other, [self.target])
                self.assertEqual(other.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)
            finally:
                other.close()
            raise TimeoutError()
        with patch.object(m, 'fetch_page', side_effect=fetch), self.assertRaises(TimeoutError):
            m.poll(self.con)
        self.assertEqual(self.con.execute('SELECT count(*) FROM notices').fetchone()[0], 1)

    def test_archive_limit_is_three_pages_and_no_partial_release(self):
        self.baseline()
        with patch.object(m, 'fetch_page', side_effect=[([self.notice(1)], 'p2'), ([self.notice(2)], 'p3'), ([self.notice(3)], 'p4')]) as fetch:
            m.poll(self.con)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(m.meta(self.con, 'delivery_pause_reason'), 'archive_scan_limit')
        self.assertEqual(self.con.execute('SELECT count(*) FROM notices WHERE is_new=1').fetchone()[0], 0)

    def test_upgrade_preserves_accepted_and_quarantines_legacy_pending(self):
        self.con.execute("DELETE FROM metadata WHERE key='safety_version'")
        for ident, state in [('accepted', 'accepted'), ('pending', 'pending')]:
            n = self.notice(ident)
            self.con.execute('INSERT INTO notices VALUES(?,?,?,?,?,1)', (n.uid, n.title, n.date, n.url, self.now))
            self.con.execute('INSERT INTO deliveries(uid,target,state) VALUES(?,?,?)', (n.uid, 'phone', state))
        m.set_meta(self.con, 'recovery_cursor', 'legacy-cursor')
        self.con.commit()
        m.ensure_safety(self.con)
        states = dict(self.con.execute('SELECT uid,state FROM deliveries'))
        self.assertEqual(states['synthetic:accepted'], 'accepted')
        self.assertEqual(states['synthetic:pending'], 'quarantined')
        self.assertFalse(m.meta(self.con, 'recovery_cursor'))
        self.assertEqual(self.con.execute('SELECT count(*) FROM notices WHERE is_new=1').fetchone()[0], 0)

    def test_rate_limits_and_attempt_reservations_survive_restart(self):
        self.baseline()
        m.ingest(self.con, [self.notice(1), self.notice(2)])
        content = {'text': '模拟', 'status': 'fallback', 'reason': 'test', 'images': 0}
        with patch.object(m, 'prepare_content', return_value=content), patch.object(m, 'send_push') as send:
            self.assertTrue(m.deliver_one(self.con, [self.target], self.cfg))
            self.con.close()
            self.con = m.connect(self.db)
            self.assertFalse(m.deliver_one(self.con, [self.target], self.cfg))
            self.now += 61
            self.assertTrue(m.deliver_one(self.con, [self.target], self.cfg))
            self.assertEqual(send.call_count, 2)
            m.ingest(self.con, [self.notice(3)])
            for i in range(4):
                self.con.execute('INSERT INTO delivery_attempt_log(target,uid,attempted_at) VALUES(?,?,?)', ('phone', 'ambiguous', self.now - 120))
            self.con.commit()
            self.now += 61
            self.assertFalse(m.deliver_one(self.con, [self.target], self.cfg))
            self.now += 3601
            self.assertTrue(m.deliver_one(self.con, [self.target], self.cfg))

    def test_expired_pending_is_never_sent_after_outage(self):
        self.baseline()
        m.ingest(self.con, [self.notice(1)])
        m.queue_targets(self.con, [self.target])
        with patch.object(m, 'today', return_value=m.dt.date(2026, 10, 27)), patch.object(m, 'send_push') as send:
            self.assertFalse(m.deliver_one(self.con, [self.target], self.cfg))
            send.assert_not_called()
        self.assertEqual(self.con.execute('SELECT state FROM deliveries').fetchone()[0], 'quarantined')

    def test_five_failed_attempts_require_manual_review(self):
        self.baseline()
        m.ingest(self.con, [self.notice(1)])
        with patch.object(m, 'prepare_content', side_effect=TimeoutError()), patch.object(m, 'send_push') as send:
            for _ in range(5):
                self.assertTrue(m.deliver_one(self.con, [self.target], self.cfg))
                self.now += 181
            self.assertFalse(m.deliver_one(self.con, [self.target], self.cfg))
            send.assert_not_called()
        state, attempts = self.con.execute('SELECT state,attempts FROM deliveries').fetchone()
        self.assertEqual((state, attempts), ('quarantined', 5))

    def test_more_than_ten_pending_articles_latches_dispatch_pause(self):
        self.baseline()
        for i in range(11):
            n = self.notice(i)
            self.con.execute('INSERT INTO notices VALUES(?,?,?,?,?,1)', (n.uid, n.title, n.date, n.url, self.now))
        self.con.commit()
        with patch.object(m, 'send_push') as send:
            self.assertFalse(m.deliver_one(self.con, [self.target], self.cfg))
            send.assert_not_called()
        self.assertEqual(m.meta(self.con, 'delivery_pause_reason'), 'pending_article_limit')
        self.assertEqual(self.con.execute("SELECT count(*) FROM deliveries WHERE state='pending'").fetchone()[0], 0)

    def test_older_than_watermark_is_history_but_same_day_new_identity_is_allowed(self):
        self.baseline()
        m.ingest(self.con, [self.notice('old', '2026-10-19'), self.notice('same-day', '2026-10-20')])
        flags = dict(self.con.execute('SELECT uid,is_new FROM notices'))
        self.assertEqual(flags['synthetic:old'], 0)
        self.assertEqual(flags['synthetic:same-day'], 1)

    def test_future_or_invalid_dates_block_batch(self):
        self.baseline()
        m.ingest(self.con, [self.notice('future', '2027-01-01'), self.notice('fresh')])
        self.assertEqual(m.meta(self.con, 'delivery_pause_reason'), 'invalid_or_future_date')
        self.assertEqual(self.con.execute('SELECT count(*) FROM notices WHERE is_new=1').fetchone()[0], 0)

    def test_expected_recipient_set_catches_accidental_iphone_only_config(self):
        path = Path(self.tmp.name) / 'config.json'
        cfg = {'interval_min_seconds': 1800, 'interval_max_seconds': 3600,
               'targets': [{'id': i, 'type': 'bark', 'enabled': True, 'server': 'https://example.test', 'key_file': 'unused'} for i in ('phone', 'friend')],
               'expected_delivery_targets': ['phone', 'friend'], 'delivery_allowlist': ['phone', 'friend']}
        path.write_text(json.dumps(cfg))
        self.assertEqual(len(m.allowed_targets(m.load_config(path))), 2)
        cfg['delivery_allowlist'] = ['phone']
        path.write_text(json.dumps(cfg))
        with self.assertRaisesRegex(m.MonitorError, 'delivery_target_set_mismatch'):
            m.load_config(path)


if __name__ == '__main__':
    unittest.main()
