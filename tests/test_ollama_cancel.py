"""Request-owned cancellation tests; no installed model server is contacted."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import socket
import threading
import unittest
from unittest.mock import patch

from gcb.ollama import LoopbackTransport
from gcb.profile_backend import UnknownCompletion


@contextmanager
def interrupted_server(*, partial_body=False):
    entered = threading.Event()
    disconnected = threading.Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 intentionally exercises getresponse() clearing connection.sock
        # while its response file still owns the socket and blocks in read().
        protocol_version = "HTTP/1.0"

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append((self.path, body))
            self.connection.settimeout(3)
            if partial_body:
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                self.wfile.write(b"{")
                self.wfile.flush()
            entered.set()
            try:
                if self.connection.recv(1) == b"":
                    disconnected.set()
            except (ConnectionError, OSError):
                disconnected.set()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", entered, disconnected, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class OllamaCancellationTests(unittest.TestCase):
    def worker(self, transport):
        result = []

        def call():
            try:
                result.append(transport("POST", "/api/chat", {"model": "offline-fixture"}, timeout=5))
            except BaseException as exc:
                result.append(exc)

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        return thread, result

    def assert_cancelled(self, transport):
        for method, path, body in (("GET", "/api/version", None), ("POST", "/api/chat", {})):
            with self.assertRaises(UnknownCompletion):
                transport(method, path, body, timeout=1)

    def test_cancel_before_connect_latches_for_metadata_and_inference(self):
        transport = LoopbackTransport("http://127.0.0.1:11434")
        transport.cancel()
        transport.cancel()
        with patch("gcb.ollama.http.client.HTTPConnection") as factory:
            self.assert_cancelled(transport)
            factory.assert_not_called()

    def test_cancel_during_connect_closes_late_socket_without_sending(self):
        entered = threading.Event()
        release = threading.Event()

        class Socket:
            def __init__(self):
                self.shutdowns = []
            def shutdown(self, how):
                self.shutdowns.append(how)

        class Connecting:
            def __init__(self, *args, **kwargs):
                self.sock = None
                self.socket = Socket()
                self.requests = []
                self.closed = 0
            def connect(self):
                entered.set()
                if not release.wait(2):
                    raise TimeoutError()
                self.sock = self.socket
            def request(self, *args):
                self.requests.append(args)
            def close(self):
                self.closed += 1
                self.sock = None

        transport = LoopbackTransport("http://127.0.0.1:11434")
        connection = Connecting()
        with patch("gcb.ollama.http.client.HTTPConnection", return_value=connection):
            thread, result = self.worker(transport)
            try:
                self.assertTrue(entered.wait(1))
                transport.cancel()
            finally:
                release.set()
                thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(result[0], UnknownCompletion)
        self.assertEqual(connection.requests, [])
        self.assertEqual(connection.socket.shutdowns, [socket.SHUT_RDWR])
        self.assertGreater(connection.closed, 0)
        self.assert_cancelled(transport)

    def test_cancel_interrupts_cold_load_wait_and_never_sends_a_second_request(self):
        with interrupted_server() as (endpoint, entered, disconnected, calls):
            transport = LoopbackTransport(endpoint)
            thread, result = self.worker(transport)
            try:
                self.assertTrue(entered.wait(1))
                transport.cancel()
                thread.join(timeout=1)
                self.assertFalse(thread.is_alive())
                self.assertTrue(disconnected.wait(1))
                self.assertIsInstance(result[0], UnknownCompletion)
                self.assert_cancelled(transport)
                self.assertEqual(len(calls), 1)
            finally:
                transport.cancel()
                thread.join(timeout=2)

    def test_cancel_between_connect_and_send_cannot_reconnect(self):
        before_send = threading.Event()
        release = threading.Event()

        class BeforeSend(http.client.HTTPConnection):
            def request(self, *args, **kwargs):
                before_send.set()
                if not release.wait(2):
                    raise TimeoutError()
                return super().request(*args, **kwargs)

        with interrupted_server() as (endpoint, entered, disconnected, calls):
            transport = LoopbackTransport(endpoint)
            with patch("gcb.ollama.http.client.HTTPConnection", BeforeSend):
                thread, result = self.worker(transport)
                try:
                    self.assertTrue(before_send.wait(1))
                    transport.cancel()
                finally:
                    release.set()
                    thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertIsInstance(result[0], UnknownCompletion)
            self.assertEqual(calls, [])

    def test_cancel_interrupts_body_read_after_httpclient_detaches_socket(self):
        reading = threading.Event()
        original_read = http.client.HTTPResponse.read

        def read(response, amount=None):
            reading.set()
            return original_read(response, amount)

        with interrupted_server(partial_body=True) as (endpoint, entered, disconnected, calls):
            transport = LoopbackTransport(endpoint)
            with patch("gcb.ollama.http.client.HTTPResponse.read", new=read):
                thread, result = self.worker(transport)
                try:
                    self.assertTrue(entered.wait(1))
                    self.assertTrue(reading.wait(1))
                    transport.cancel()
                    thread.join(timeout=1)
                    self.assertFalse(thread.is_alive())
                    self.assertTrue(disconnected.wait(1))
                    self.assertIsInstance(result[0], UnknownCompletion)
                    self.assertEqual(len(calls), 1)
                finally:
                    transport.cancel()
                    thread.join(timeout=2)

    def test_cancelled_late_response_is_not_reported_as_success(self):
        entered = threading.Event()
        release = threading.Event()

        class Response:
            status = 200
            def read(self, limit):
                entered.set()
                if not release.wait(2):
                    raise TimeoutError()
                return b'{"done":true}'
            def close(self):
                pass

        class Connection:
            sock = None
            def connect(self):
                pass
            def request(self, *args):
                pass
            def getresponse(self):
                return Response()
            def close(self):
                pass

        transport = LoopbackTransport("http://127.0.0.1:11434")
        with patch("gcb.ollama.http.client.HTTPConnection", return_value=Connection()):
            thread, result = self.worker(transport)
            try:
                self.assertTrue(entered.wait(1))
                transport.cancel()
            finally:
                release.set()
                thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(result[0], UnknownCompletion)


if __name__ == "__main__":
    unittest.main()
