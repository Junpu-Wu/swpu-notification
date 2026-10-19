"""Local transport bounds; never contacts school or third-party services."""
import contextlib
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import monitor as m


class BudgetHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/periodic-chunks":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            # Each byte arrives well inside the socket's original idle timeout.
            # Without an absolute deadline this stream lasts two full seconds.
            end = time.monotonic() + 2
            try:
                while time.monotonic() < end and not self.server.stop_stream.is_set():
                    self.wfile.write(b"1\r\n \r\n")
                    self.wfile.flush()
                    self.server.stop_stream.wait(0.03)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # Client is expected to disconnect at its deadline.
        elif self.path == "/oversized":
            body = b"x" * (1024 * 1024 + 1)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        else:
            self.send_error(404)


@contextlib.contextmanager
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), BudgetHandler)
    server.daemon_threads = True
    server.stop_stream = threading.Event()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_port
    finally:
        server.stop_stream.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class HttpBudgetTests(unittest.TestCase):
    def test_periodic_chunked_whitespace_cannot_reset_total_deadline(self):
        with local_server() as base:
            started = time.monotonic()
            with self.assertRaises((m.MonitorError, TimeoutError)) as caught:
                m.http_read(base + "/periodic-chunks", timeout=0.3)
            elapsed = time.monotonic() - started
            if isinstance(caught.exception, m.MonitorError):
                self.assertEqual(str(caught.exception), "http_deadline_exceeded")
            # A generous ceiling allows loaded CI hosts but still fails if each
            # periodic chunk resets the timeout and the two-second stream wins.
            self.assertGreaterEqual(elapsed, 0.2)
            self.assertLess(elapsed, 1.2)

    def test_more_than_one_mebibyte_returns_fixed_error(self):
        with local_server() as base:
            with self.assertRaises(m.MonitorError) as caught:
                m.http_read(base + "/oversized", timeout=2)
            self.assertEqual(str(caught.exception), "response_too_large")


if __name__ == "__main__":
    unittest.main()
