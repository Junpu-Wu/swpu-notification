import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import monitor as m

class WxPusherTests(unittest.TestCase):
 def test_payload_and_url(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"secret.spt";p.write_text("SPT_synthetic_only");p.chmod(0o600)
   target={"type":"wxpusher","server":"https://wxpusher.zjiecode.com","spt_file":str(p)}
   n=m.Notice("test","公告标题","2026-10-15",m.PAGE_URL)
   with patch.object(m,"http_read",return_value=(b'{"code":1000,"data":[{"code":1000}]}',"utf-8")) as call:
    m.send_push(target,n)
    payload=json.loads(call.call_args.kwargs["data"])
    self.assertEqual(payload["sptList"],["SPT_synthetic_only"])
    self.assertEqual(payload["url"],n.url)
    self.assertIn(n.title,payload["content"])
    self.assertNotIn("SPT_",call.call_args.args[0])
 def test_business_errors_are_not_accepted(self):
  for response in (b'{"code":1001}',b'{"code":1000,"success":false}',b'{"code":1000,"data":[{"code":1001}]}'):
   with patch.object(m,"read_secret",return_value="SPT_synthetic_only"),patch.object(m,"http_read",return_value=(response,"utf-8")):
    with self.assertRaises(m.MonitorError):m.send_push({"type":"wxpusher","server":"https://example.test","spt_file":"unused"},m.Notice("test","test","2026-10-15",m.PAGE_URL))
 def test_private_configuration_required(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"config.json"
   c={"interval_min_seconds":1800,"interval_max_seconds":3600,"targets":[{"id":"wx","type":"wxpusher","enabled":True,"server":"https://wxpusher.zjiecode.com"}]}
   p.write_text(json.dumps(c))
   with self.assertRaises(m.MonitorError):m.load_config(p)
   c["targets"][0]["spt_file"]="private.spt";p.write_text(json.dumps(c));m.load_config(p)
