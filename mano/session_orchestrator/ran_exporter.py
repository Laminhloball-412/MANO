"""Standalone OCUDU scheduler WebSocket to Prometheus exporter."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .ran_metrics import ConnectionTracker, RanMetricsSchemaError, RanSnapshot, parse_scheduler_report, render_ran_metrics

try:
    import websocket
except ModuleNotFoundError:  # The exporter dependency is intentionally standalone.
    websocket = None


LOGGER = logging.getLogger(__name__)
_SUBSCRIPTION = {"cmd": "metrics_subscribe"}


class RanSnapshotStore:
    """Atomically exposes the latest complete scheduler snapshot and transport status."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = _StoreState(snapshot=None, connected=False)

    def publish(self, snapshot: RanSnapshot) -> None:
        with self._lock:
            self._state = _StoreState(snapshot=snapshot, connected=self._state.connected)

    def read(self) -> RanSnapshot | None:
        return self._state.snapshot

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self._state = _StoreState(snapshot=self._state.snapshot, connected=connected)

    def read_with_status(self) -> tuple[RanSnapshot | None, bool]:
        state = self._state
        return state.snapshot, state.connected


@dataclass(frozen=True)
class _StoreState:
    snapshot: RanSnapshot | None
    connected: bool


ConnectionFactory = Callable[[str, int], Any]


class OcuDuMetricsClient:
    """Consumes scheduler reports without coupling the exporter to lab control code."""

    def __init__(
        self,
        url: str,
        store: RanSnapshotStore,
        reconnect_min_seconds: float = 1,
        reconnect_max_seconds: float = 30,
        connection_factory: ConnectionFactory | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if reconnect_min_seconds <= 0:
            raise ValueError("reconnect_min_seconds must be greater than zero")
        if reconnect_max_seconds < reconnect_min_seconds:
            raise ValueError("reconnect_max_seconds must be at least reconnect_min_seconds")
        self.url = url
        self.store = store
        self.reconnect_min_seconds = reconnect_min_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self._connection_factory = connection_factory or _create_connection
        self._clock = clock
        self._tracker = ConnectionTracker()

    def run(self, stop_event: threading.Event) -> None:
        reconnect_delay = self.reconnect_min_seconds
        while not stop_event.is_set():
            connection = None
            try:
                connection = self._connection_factory(self.url, 5)
                self.store.set_connected(True)
                connection.send(json.dumps(_SUBSCRIPTION))
                while not stop_event.is_set():
                    message = connection.recv()
                    snapshot = self._parse_message(message)
                    if snapshot is None:
                        continue
                    self.store.publish(snapshot)
                    reconnect_delay = self.reconnect_min_seconds
            except Exception as error:
                self.store.set_connected(False)
                if not stop_event.is_set():
                    LOGGER.warning("OCUDU metrics connection failed at %.6f: %s", self._clock(), error)
                    stop_event.wait(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, self.reconnect_max_seconds)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        LOGGER.debug("OCUDU metrics connection close failed", exc_info=True)
        self.store.set_connected(False)

    def _parse_message(self, message: Any) -> RanSnapshot | None:
        try:
            report = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            _log_schema_error("message")
            return None
        if not isinstance(report, dict) or "cells" not in report:
            return None
        received_at = self._clock()
        try:
            return parse_scheduler_report(report, received_at, self._tracker)
        except RanMetricsSchemaError as error:
            _log_schema_error(_schema_field(error))
            return None


def _create_connection(url: str, timeout: int) -> Any:
    if websocket is None:
        raise RuntimeError(
            "websocket-client is required; install session_orchestrator/ran-exporter-requirements.txt"
        )
    return websocket.create_connection(url, timeout=timeout)


def _schema_field(error: RanMetricsSchemaError) -> str:
    return str(error)


def _log_schema_error(field: str) -> None:
    LOGGER.warning("OCUDU scheduler schema error at %.6f field=%s", time.time(), field)


def create_http_server(
    store: RanSnapshotStore,
    stale_after_seconds: float,
    host: str,
    port: int,
    clock: Callable[[], float] = time.time,
) -> ThreadingHTTPServer:
    if stale_after_seconds < 0:
        raise ValueError("stale_after_seconds must be non-negative")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            snapshot, connected = store.read_with_status()
            now = clock()
            if self.path == "/metrics":
                body = _metrics_body(snapshot, now, connected)
                self._send(200, body, "text/plain; version=0.0.4; charset=utf-8")
                return
            if self.path == "/healthz":
                status = 200 if _is_fresh(snapshot, now, stale_after_seconds) else 503
                self._send(status, b"", "text/plain; charset=utf-8")
                return
            if self.path == "/state":
                body = json.dumps(_state_document(snapshot), sort_keys=True).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
                return
            self.send_error(404)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def _metrics_body(snapshot: RanSnapshot | None, now: float, connected: bool) -> bytes:
    if snapshot is None:
        return (
            "# HELP ocudu_ran_metrics_exporter_up Whether the RAN metrics exporter is up.\n"
            "# TYPE ocudu_ran_metrics_exporter_up gauge\n"
            f"ocudu_ran_metrics_exporter_up {1 if connected else 0}\n"
        ).encode("utf-8")
    return render_ran_metrics(snapshot, now=now, exporter_up=connected).encode("utf-8")


def _is_fresh(snapshot: RanSnapshot | None, now: float, stale_after_seconds: float) -> bool:
    return snapshot is not None and now - snapshot.received_at <= stale_after_seconds


def _state_document(snapshot: RanSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {"source_timestamp": None, "received_at": None, "ues": []}
    return {
        "source_timestamp": snapshot.source_timestamp,
        "received_at": snapshot.received_at,
        "ues": [asdict(ue) for ue in snapshot.ues],
    }


def serve_ran_exporter(config: dict) -> None:
    """Run the standalone exporter until its HTTP server is stopped."""
    store = RanSnapshotStore()
    client = OcuDuMetricsClient(
        str(config["ocudu_websocket_url"]),
        store,
        reconnect_min_seconds=float(config["reconnect_min_seconds"]),
        reconnect_max_seconds=float(config["reconnect_max_seconds"]),
    )
    server = create_http_server(
        store,
        stale_after_seconds=float(config["stale_after_seconds"]),
        host=str(config["listen_host"]),
        port=int(config["listen_port"]),
    )
    stop_event = threading.Event()
    client_thread = threading.Thread(target=client.run, args=(stop_event,), daemon=True)
    client_thread.start()
    try:
        server.serve_forever()
    finally:
        stop_event.set()
        server.server_close()
        client_thread.join(timeout=6)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export OCUDU scheduler reports as Prometheus metrics.")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as config_file:
        config = json.load(config_file)
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    serve_ran_exporter(config)


if __name__ == "__main__":
    main()
