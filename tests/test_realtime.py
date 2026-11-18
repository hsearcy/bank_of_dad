"""Real HTTP + WebSocket integration against a disposable app subprocess."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from simple_websocket import ConnectionClosed
from websocket import create_connection, WebSocketTimeoutException

ROOT = Path(__file__).resolve().parents[1]


class TestSocket:
    """Small timeout adapter over a real, independent WebSocket client."""
    def __init__(self, url, origin):
        self.ws = create_connection(url, origin=origin, timeout=2)

    def receive(self, timeout):
        self.ws.settimeout(timeout)
        try:
            return self.ws.recv()
        except WebSocketTimeoutException:
            return None

    def close(self):
        self.ws.close()


class AppServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = open(Path(self.tmp.name) / "server.log", "w+")
        self.addCleanup(self.log.close)
        env = dict(os.environ, BANK_OF_DAD_DB=str(Path(self.tmp.name) / "bank.db"))
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-c", """
from werkzeug.serving import make_server
from app import app
server = make_server('127.0.0.1', 0, app, threaded=True)
print(server.server_port, flush=True)
server.serve_forever()
"""], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=self.log, text=True)
        self.addCleanup(self.stop)
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline().strip()
        if not line.isdigit():
            self.log.seek(0)
            self.fail("Server failed to start: " + self.log.read())
        self.origin = "http://127.0.0.1:" + line
        self.ws_url = self.origin.replace("http:", "ws:") + "/api/updates"
        self.post("/api/admin/pin", {"new_pin": "1234"})
        self.post("/api/admin/kid", {"name": "Synthetic"})
        self.kid_id = self.get_state()["kids"][0]["id"]

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)
        assert self.proc.stdout is not None
        self.proc.stdout.close()

    def post(self, path, body, pin="1234"):
        req = Request(self.origin + path, json.dumps(body).encode(),
                      {"Content-Type": "application/json", "X-Dad-Pin": pin})
        try:
            with urlopen(req, timeout=5) as response:
                return json.load(response)
        except HTTPError as response:
            with response:
                return json.load(response)

    def get_state(self):
        with urlopen(self.origin + "/api/state", timeout=5) as response:
            return json.load(response)


class RealtimeTests(AppServerCase):
    def connect(self):
        ws = TestSocket(self.ws_url, self.origin)
        self.addCleanup(self.close_socket, ws)
        self.assert_invalidation(ws)
        return ws

    def assert_invalidation(self, ws):
        message = ws.receive(timeout=2)
        self.assertIsNotNone(message, "Expected a real WebSocket invalidation")
        assert message is not None
        self.assertEqual({"type": "state_changed"}, json.loads(message))

    @staticmethod
    def close_socket(ws):
        try:
            ws.close()
        except ConnectionClosed:
            pass

    def test_two_clients_receive_only_successful_mutations_after_commit(self):
        clients = [self.connect(), self.connect()]
        before = self.get_state()["kids"][0]["wallet_cents"]
        self.assertTrue(self.post("/api/admin/gift", {
            "kid_id": self.kid_id, "amount_cents": 125, "note": "synthetic"})["ok"])
        for ws in clients:
            self.assert_invalidation(ws)
            self.assertEqual(before + 125, self.get_state()["kids"][0]["wallet_cents"])
        self.assertFalse(self.post("/api/admin/gift", {
            "kid_id": self.kid_id, "amount_cents": 100}, pin="9999")["ok"])
        self.assertFalse(self.post("/api/spend", {
            "kid_id": self.kid_id, "amount_cents": 0})["ok"])
        self.post("/api/admin/verify", {"pin": "1234"})
        self.get_state()
        for ws in clients:
            self.assertIsNone(ws.receive(timeout=0.2))
        self.assertTrue(self.post("/api/save", {
            "kid_id": self.kid_id, "amount_cents": 100})["ok"])
        for ws in clients:
            self.assert_invalidation(ws)

    def test_all_existing_mutation_routes_publish(self):
        ws = self.connect()
        kid = self.kid_id
        actions = [
            ("/api/admin/gift", {"kid_id": kid, "amount_cents": 100}),
            ("/api/save", {"kid_id": kid, "amount_cents": 100}),
            ("/api/withdraw", {"kid_id": kid, "amount_cents": 50}),
            ("/api/bond", {"kid_id": kid, "term": "1mo", "amount_cents": 100}),
            ("/api/spend", {"kid_id": kid, "amount_cents": 50}),
            ("/api/admin/kid/update", {"kid_id": kid, "name": "Updated"}),
            ("/api/admin/kid/allowance", {"kid_id": kid, "amount_cents": 100,
                                          "period_days": 7, "enabled": True}),
            ("/api/admin/rates", {"savings_weekly_pct": 10, "returns": {"1mo": 30}}),
            ("/api/admin/fastforward", {"days": 14}),
            ("/api/collect", {"kid_id": kid, "kind": "allowance"}),
        ]
        for path, body in actions:
            with self.subTest(path=path):
                self.assertTrue(self.post(path, body)["ok"])
                self.assert_invalidation(ws)
        state = self.get_state()
        self.assertEqual("Updated", state["kids"][0]["name"])
        self.assertEqual(600, state["kids"][0]["wallet_cents"])
        self.assertEqual(50, state["kids"][0]["savings_cents"])
        self.assertEqual(100, state["kids"][0]["invest_cents"])
        self.assertEqual(10, state["config"]["savings_weekly_pct"])
        with urlopen(self.origin + f"/api/transactions?kid_id={kid}") as response:
            gift = next(tx for tx in json.load(response)["transactions"] if tx["kind"] == "gift")
        for path, body in [
            ("/api/admin/tx/edit", {"tx_id": gift["id"], "amount_cents": 125}),
            ("/api/admin/tx/delete", {"tx_id": gift["id"]}),
            ("/api/admin/kid", {"name": "Another synthetic child"}),
            ("/api/admin/pin", {"current_pin": "1234", "new_pin": "5678"}),
        ]:
            with self.subTest(path=path):
                self.assertTrue(self.post(path, body)["ok"])
                self.assert_invalidation(ws)
        self.assertEqual(500, self.get_state()["kids"][0]["wallet_cents"])
        self.assertEqual(2, len(self.get_state()["kids"]))
        self.assertFalse(self.post("/api/admin/gift", {"kid_id": kid, "amount_cents": 10})["ok"])
        self.assertIsNone(ws.receive(timeout=0.2))

    def test_reconnect_resync_signal_and_origin_boundary(self):
        ws = self.connect()
        ws.close()
        self.post("/api/admin/gift", {"kid_id": self.kid_id, "amount_cents": 50})
        self.connect()  # initial invalidation covers the missed mutation
        self.assertEqual(550, self.get_state()["kids"][0]["wallet_cents"])
        for headers in ({}, {"Origin": "https://untrusted.example"}, {"Origin": "null"}):
            req = Request(self.origin + "/api/updates", headers={
                "Connection": "Upgrade", "Upgrade": "websocket",
                "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", **headers})
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as error:
                urlopen(req, timeout=5)
            self.assertEqual(403, error.exception.code)
            error.exception.close()


class BroadcasterTests(unittest.TestCase):
    def test_slow_subscribers_are_coalesced_and_disconnect_removes_them(self):
        from queue import Queue
        from realtime import Updates
        updates = Updates()
        pending = Queue(maxsize=1)
        updates._subscribers[pending] = None
        for _ in range(1000):
            updates.publish()
        self.assertEqual(1, pending.qsize())
        updates._subscribers.pop(pending)

        class ClosedSocket:
            def send(self, message):
                raise ConnectionClosed()

            def close(self):
                pass
        updates.serve(ClosedSocket())
        self.assertEqual({}, updates._subscribers)


if __name__ == "__main__":
    unittest.main()
