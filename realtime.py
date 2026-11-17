"""Best-effort, content-free invalidations for the single-process local app."""
from queue import Empty, Full, Queue
from threading import Lock

from simple_websocket import ConnectionClosed


class Updates:
    MESSAGE = '{"type":"state_changed"}'

    def __init__(self):
        self._lock = Lock()
        self._subscribers = set()

    def publish(self):
        # Never do socket I/O on a successful money-changing request. Slow
        # readers get one pending refresh, not an unbounded event backlog.
        with self._lock:
            for pending in self._subscribers:
                try:
                    pending.put_nowait(True)
                except Full:
                    pass

    def serve(self, ws):
        pending = Queue(maxsize=1)
        with self._lock:
            self._subscribers.add(pending)
        try:
            # Subscribe BEFORE the initial signal, closing the read/connect gap.
            # Reconnection always refetches; this is not a durable event log.
            ws.send(self.MESSAGE)
            while ws.connected:
                try:
                    pending.get(timeout=0.5)
                except Empty:
                    pass
                else:
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
                self._subscribers.discard(pending)
