"""Best-effort, content-free invalidations for a single-process app.

Hosted channels are server-owned family IDs; None is the free local channel.
"""
from queue import Empty, Full, Queue
from threading import Lock

from simple_websocket import ConnectionClosed


class Updates:
    MESSAGE = '{"type":"state_changed"}'

    def __init__(self):
        self._lock = Lock()
        self._subscribers = {}

    def publish(self, channel=None):
        # Never do socket I/O on a successful money-changing request. Slow
        # readers get one pending refresh, not an unbounded event backlog.
        with self._lock:
            for pending, subscribed_channel in self._subscribers.items():
                if subscribed_channel != channel:
                    continue
                try:
                    pending.put_nowait(True)
                except Full:
                    pass

    def serve(self, ws, channel=None, authorized=None):
        pending = Queue(maxsize=1)
        with self._lock:
            self._subscribers[pending] = channel
        try:
            # Subscribe BEFORE the initial signal, closing the read/connect gap.
            # Reconnection always refetches; this is not a durable event log.
            if authorized is not None and not authorized():
                ws.close(reason=1008, message="Access expired")
                return
            ws.send(self.MESSAGE)
            while ws.connected:
                try:
                    pending.get(timeout=0.5)
                    changed = True
                except Empty:
                    changed = False
                # Reauthorize before every send AND on idle timeout: the session
                # object at handshake does not reflect logout or billing expiry.
                if authorized is not None and not authorized():
                    ws.close(reason=1008, message="Access expired")
                    break
                if changed:
                    ws.send(self.MESSAGE)
                # Receive checks closure and bounds unsolicited client messages.
                # This channel is server -> client only, with no write commands.
                if ws.receive(timeout=0) is not None:
                    ws.close(reason=1008, message="Read-only channel")
                    break
        except (ConnectionClosed, OSError):
            pass
        finally:
            with self._lock:
                self._subscribers.pop(pending, None)
            # An authorization storage failure must fail closed too.
            try:
                ws.close()
            except (ConnectionClosed, OSError):
                pass
