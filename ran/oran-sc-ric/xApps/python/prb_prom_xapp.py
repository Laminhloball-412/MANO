#!/usr/bin/env python3
"""E2SM-KPM PRB xApp with a small Prometheus endpoint."""

import argparse
import math
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


CELL_METRICS = (
    "RRU.PrbUsedDl",
    "RRU.PrbAvailDl",
    "RRU.PrbTotDl",
    "RRU.PrbUsedUl",
    "RRU.PrbAvailUl",
    "RRU.PrbTotUl",
)
UE_METRICS = ("RRU.PrbUsedDl", "RRU.PrbTotDl", "RRU.PrbUsedUl", "RRU.PrbTotUl")


def _latest_number(values):
    if not isinstance(values, (list, tuple)):
        values = [values]
    for value in reversed(values):
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _format_number(value):
    return str(int(value)) if float(value).is_integer() else format(value, ".6g")


def _escape_label(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class PrbMetricStore:
    """Thread-safe latest-value store for cell and UE PRB KPM samples."""

    def __init__(self, e2_node_id, stale_after_seconds=5):
        self.e2_node_id = str(e2_node_id)
        self.stale_after_seconds = float(stale_after_seconds)
        self._cell = {}
        self._ues = {}
        self._last_sample_at = None
        self._samples_total = 0
        self._lock = threading.Lock()

    def update_cell(self, extracted, received_at=None):
        measurements = (extracted or {}).get("measData", {})
        changed = False
        with self._lock:
            for metric_name in CELL_METRICS:
                value = _latest_number(measurements.get(metric_name, []))
                if value is not None:
                    self._cell[metric_name] = value
                    changed = True
            if changed:
                self._record_sample(received_at)

    def update_ues(self, extracted, received_at=None):
        ue_measurements = (extracted or {}).get("ueMeasData", {})
        changed = False
        with self._lock:
            for ue_id, report in ue_measurements.items():
                measurements = (report or {}).get("measData", {})
                current = self._ues.setdefault(str(ue_id), {})
                for metric_name in UE_METRICS:
                    value = _latest_number(measurements.get(metric_name, []))
                    if value is not None:
                        current[metric_name] = value
                        changed = True
            if changed:
                self._record_sample(received_at)

    def _record_sample(self, received_at):
        self._last_sample_at = time.time() if received_at is None else float(received_at)
        self._samples_total += 1

    def render(self, now=None):
        now = time.time() if now is None else float(now)
        with self._lock:
            cell = dict(self._cell)
            ues = {ue_id: dict(values) for ue_id, values in self._ues.items()}
            last_sample_at = self._last_sample_at
            samples_total = self._samples_total

        age = -1 if last_sample_at is None else max(0.0, now - last_sample_at)
        exporter_up = int(last_sample_at is not None and age <= self.stale_after_seconds)
        node = _escape_label(self.e2_node_id)
        lines = [
            "# HELP srsran_kpm_exporter_up Whether fresh E2SM-KPM PRB samples are available.",
            "# TYPE srsran_kpm_exporter_up gauge",
            "srsran_kpm_exporter_up {}".format(exporter_up),
            "# HELP srsran_kpm_sample_age_seconds Age of the newest accepted PRB sample.",
            "# TYPE srsran_kpm_sample_age_seconds gauge",
            "srsran_kpm_sample_age_seconds {}".format(_format_number(age)),
            "# HELP srsran_kpm_samples_total Number of accepted PRB indication batches.",
            "# TYPE srsran_kpm_samples_total counter",
            "srsran_kpm_samples_total {}".format(samples_total),
            "# HELP srsran_prb_used Mean physical resource blocks scheduled per slot.",
            "# TYPE srsran_prb_used gauge",
            "# HELP srsran_prb_available Mean physical resource blocks remaining per slot.",
            "# TYPE srsran_prb_available gauge",
            "# HELP srsran_prb_utilization_percent Percentage of cell PRBs scheduled per slot.",
            "# TYPE srsran_prb_utilization_percent gauge",
        ]
        self._append_scope(lines, cell, node, scope="cell")
        for ue_id in sorted(ues, key=lambda item: int(item) if item.isdigit() else item):
            self._append_scope(lines, ues[ue_id], node, scope="ue", ran_ue_id=ue_id)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _append_scope(lines, values, node, scope, ran_ue_id=None):
        for direction, suffix in (("dl", "Dl"), ("ul", "Ul")):
            labels = [('direction', direction), ('e2_node_id', node)]
            if ran_ue_id is not None:
                labels.append(('ran_ue_id', _escape_label(ran_ue_id)))
            labels.append(('scope', scope))
            label_text = ",".join('{}="{}"'.format(key, value) for key, value in labels)
            for source, target in (
                ("RRU.PrbUsed" + suffix, "srsran_prb_used"),
                ("RRU.PrbAvail" + suffix, "srsran_prb_available"),
                ("RRU.PrbTot" + suffix, "srsran_prb_utilization_percent"),
            ):
                if source in values:
                    lines.append("{}{{{}}} {}".format(target, label_text, _format_number(values[source])))


def start_metrics_server(store, host, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/metrics":
                body = store.render().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/healthz":
                body = b"ok\n"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, name="prometheus-http", daemon=True).start()
    return server


def run(args):
    from lib.xAppBase import xAppBase

    store = PrbMetricStore(args.e2_node_id, args.stale_after_seconds)
    metrics_server = start_metrics_server(store, args.metrics_host, args.metrics_port)

    class PrbPromXapp(xAppBase):
        def cell_callback(self, _e2_agent_id, _subscription_id, indication_hdr, indication_msg):
            self.e2sm_kpm.extract_hdr_info(indication_hdr)
            store.update_cell(self.e2sm_kpm.extract_meas_data(indication_msg))

        def ue_callback(self, _e2_agent_id, _subscription_id, indication_hdr, indication_msg):
            self.e2sm_kpm.extract_hdr_info(indication_hdr)
            store.update_ues(self.e2sm_kpm.extract_meas_data(indication_msg))

        @xAppBase.start_function
        def start(self):
            self.e2sm_kpm.subscribe_report_service_style_1(
                args.e2_node_id, args.report_period_ms, list(CELL_METRICS), args.granularity_ms, self.cell_callback
            )
            self.e2sm_kpm.subscribe_report_service_style_5(
                args.e2_node_id,
                args.report_period_ms,
                list(range(args.max_ues)),
                list(UE_METRICS),
                args.granularity_ms,
                self.ue_callback,
            )

    xapp = PrbPromXapp(args.config, args.http_server_port, args.rmr_port)
    xapp.e2sm_kpm.set_ran_func_id(args.ran_func_id)
    for signum in (signal.SIGQUIT, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, xapp.signal_handler)
    print("PRB Prometheus endpoint listening on {}:{}".format(args.metrics_host, args.metrics_port), flush=True)
    try:
        xapp.start()
    finally:
        metrics_server.shutdown()


def parse_args():
    parser = argparse.ArgumentParser(description="Export srsRAN E2SM-KPM PRB measurements to Prometheus")
    parser.add_argument("--config", default="")
    parser.add_argument("--http_server_port", type=int, default=8091)
    parser.add_argument("--rmr_port", type=int, default=4561)
    parser.add_argument("--e2_node_id", default="gnbd_460_099_00019b_0")
    parser.add_argument("--ran_func_id", type=int, default=2)
    parser.add_argument("--metrics_host", default="0.0.0.0")
    parser.add_argument("--metrics_port", type=int, default=9108)
    parser.add_argument("--max_ues", type=int, default=8)
    parser.add_argument("--report_period_ms", type=int, default=1000)
    parser.add_argument("--granularity_ms", type=int, default=100)
    parser.add_argument("--stale_after_seconds", type=float, default=5)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
