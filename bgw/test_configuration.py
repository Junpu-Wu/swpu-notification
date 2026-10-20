import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import monitor as m

spec = importlib.util.spec_from_file_location("configure_under_test", Path(__file__).with_name("configure.py"))
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class ConfigurationTests(unittest.TestCase):
    def test_helper_preserves_ai_scope_and_existing_recipient(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = {"page_url": m.PAGE_URL, "interval_min_seconds": 1800, "interval_max_seconds": 3600,
                   "ai": {"enabled": True, "model": "deepseek-flash", "key_file": "/private/ai.key"},
                   "delivery_allowlist": ["my-iphone"], "targets": [
                       {"id": "my-iphone", "type": "bark", "enabled": False,
                        "server": "https://api.day.app", "key_file": "/private/iphone.key"}]}
            path.write_text(json.dumps(cfg))
            original = cfg["targets"][0].copy()
            output = io.StringIO()
            with patch("sys.argv", ["configure.py", "--config", str(path), "--id", "new-iphone", "--future-only", "--bark-url-stdin"]), patch("sys.stdin", io.StringIO("https://api.day.app/synthetic-key-only")), contextlib.redirect_stdout(output):
                self.assertEqual(configure.main(), 0)
            saved = m.load_config(path)
            self.assertEqual(saved["ai"], cfg["ai"])
            self.assertEqual(saved["delivery_allowlist"], ["my-iphone"])
            self.assertEqual(saved["targets"][0], original)
            self.assertEqual(saved["targets"][1]["id"], "new-iphone")
            self.assertGreater(saved["targets"][1]["since"], 0)
            self.assertEqual(Path(saved["targets"][1]["key_file"]).stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("synthetic-key-only", output.getvalue())
            self.assertNotIn("synthetic-key-only", path.read_text())
            self.assertEqual(m.allowed_targets(saved), [])

    def test_worker_reloads_scope_without_sending_excluded_target(self):
        class Stop:
            waits = 0
            def is_set(self):
                return self.waits >= 2
            def wait(self, _):
                self.waits += 1
        targets = [{"id": "my-iphone", "enabled": True}, {"id": "friend", "enabled": True}]
        configs = [{"targets": targets, "delivery_allowlist": ["my-iphone"]},
                   {"targets": targets, "delivery_allowlist": []}]
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, "load_config", side_effect=configs) as load, patch.object(m, "deliver_one", return_value=False) as deliver:
            m.delivery_worker("unused", Path(tmp) / "state.sqlite3", Stop())
            self.assertEqual(load.call_count, 2)
            self.assertEqual(deliver.call_args_list[0].args[1], [targets[0]])
            self.assertEqual(deliver.call_args_list[1].args[1], [])


if __name__ == "__main__":
    unittest.main()
