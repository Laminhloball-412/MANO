from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass

from .command import CommandError, run
from .intent_engine import api_request
from .telemetry import measure_ping


@dataclass(frozen=True)
class ApplicationPattern:
    packet_size_bytes: int
    message_rate_per_second: float


PATTERNS = {
    "video": ApplicationPattern(1200, 1000),
    "streaming": ApplicationPattern(1200, 1000),
    "control": ApplicationPattern(120, 50),
    "robot": ApplicationPattern(120, 50),
    "sensor": ApplicationPattern(80, 10),
    "telemetry": ApplicationPattern(80, 10),
}


def interface_ipv4(interface: str) -> str:
    result = run(["ip", "-j", "-4", "addr", "show", "dev", interface])
    document = json.loads(result.stdout)
    addresses = document[0].get("addr_info", []) if document else []
    for address in addresses:
        if address.get("scope") == "global":
            return str(address["local"])
    raise RuntimeError(f"interface has no global IPv4 address: {interface}")


class UdpApplicationSimulator:
    """Independent UE application traffic generator.

    It does not invoke slice selection. When given an intent ID it follows the
    binding published by the intent service and acknowledges a migration only
    after a socket has successfully bound to the new PDU address.
    """

    def __init__(self, intent_api_url: str = "http://127.0.0.1:9106"):
        self.intent_api_url = intent_api_url.rstrip("/")

    def _binding(self, intent_id: str) -> dict | None:
        state = api_request("GET", self.intent_api_url + "/api/v1/state")
        intent = state.get("intents", {}).get(intent_id)
        return intent if intent and intent.get("target") else None

    def run(
        self,
        service: str,
        duration_seconds: int,
        interface: str,
        gateway: str,
        intent_id: str | None = None,
        packet_size_bytes: int | None = None,
        message_rate_per_second: float | None = None,
        end_intent_on_exit: bool = True,
        flow_id: str | None = None,
        ue_id: int = 1,
    ) -> dict:
        pattern = PATTERNS.get(service.lower(), ApplicationPattern(256, 10))
        packet_size = packet_size_bytes or pattern.packet_size_bytes
        message_rate = message_rate_per_second or pattern.message_rate_per_second
        interval = 1.0 / max(message_rate, 0.1)
        payload = bytes(packet_size)
        current_interface = interface
        current_gateway = gateway
        sock: socket.socket | None = None
        sent_packets = sent_bytes = 0
        send_errors = binding_errors = 0
        switches = []
        acknowledged = False
        intent_ended = False
        flow_registered = False
        started = time.monotonic()
        next_binding_check = started
        next_sample_at = started + 1.0
        sample_started = started
        sample_packets = sample_bytes = 0
        latest_probe = None
        resolved_intent_id = intent_id
        if flow_id:
            api_request("POST", self.intent_api_url + "/api/v1/flows", {
                "flow_id": flow_id,
                "ue_id": ue_id,
                "service": service,
                "intent_source": "application",
                "interface": interface,
                "gateway": gateway,
            })
            flow_registered = True
        try:
            while time.monotonic() - started < duration_seconds:
                now = time.monotonic()
                dynamic_intent_id = intent_id
                flow = None
                if flow_id and now >= next_binding_check:
                    try:
                        flow = api_request(
                            "GET", self.intent_api_url + f"/api/v1/flows/{flow_id}"
                        )
                    except (OSError, TimeoutError, ValueError):
                        flow = None
                    if flow and flow.get("intent"):
                        dynamic_intent_id = flow["intent"]["intent_id"]
                        resolved_intent_id = dynamic_intent_id
                    next_binding_check = now + 1.0
                # A flow lookup performed in this iteration is already the
                # binding poll. Process its linked intent before advancing to
                # the next iteration; otherwise next_binding_check has just
                # moved forward and the handoff can never run.
                if dynamic_intent_id and (flow is not None or now >= next_binding_check):
                    try:
                        api_request(
                            "POST",
                            self.intent_api_url
                            + f"/api/v1/intents/{dynamic_intent_id}/heartbeat",
                            {},
                        )
                    except (OSError, TimeoutError, ValueError):
                        pass
                    intent = (
                        flow.get("intent") if flow and flow.get("intent")
                        else self._binding(dynamic_intent_id)
                    )
                    if intent and intent.get("target"):
                        target = intent["target"]
                        target_interface = target["interface"]
                        if target_interface != current_interface:
                            # Binding succeeds before the old socket is closed.
                            replacement = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            try:
                                replacement.bind((interface_ipv4(target_interface), 0))
                            except (OSError, RuntimeError, CommandError):
                                replacement.close()
                                binding_errors += 1
                            else:
                                old = sock
                                sock = replacement
                                current_interface = target_interface
                                current_gateway = target["gateway"]
                                switches.append({
                                    "at_seconds": round(now - started, 3),
                                    "interface": current_interface,
                                })
                                if old:
                                    old.close()
                        if (
                            intent["state"] == "AWAITING_APPLICATION_ACK"
                            and not acknowledged
                        ):
                            try:
                                api_request(
                                    "POST",
                                    self.intent_api_url
                                    + f"/api/v1/intents/{dynamic_intent_id}/ack",
                                    {},
                                )
                            except (OSError, TimeoutError, ValueError):
                                # The request may have committed before the
                                # response was lost. The next state poll makes
                                # the retry idempotent from the application's
                                # perspective.
                                pass
                            else:
                                acknowledged = True
                        elif (
                            intent.get("mode") == "migrate"
                            and intent["state"] == "ACTIVE"
                            and target_interface == current_interface
                        ):
                            acknowledged = True
                    if not flow_id:
                        next_binding_check = now + 1.0
                if sock is None:
                    candidate = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    try:
                        candidate.bind((interface_ipv4(current_interface), 0))
                    except (OSError, RuntimeError, CommandError):
                        candidate.close()
                        binding_errors += 1
                        time.sleep(min(interval, 1.0))
                        continue
                    sock = candidate
                try:
                    sock.sendto(payload, (current_gateway, 9999))
                except OSError:
                    send_errors += 1
                    sock.close()
                    sock = None
                    time.sleep(min(interval, 1.0))
                    continue
                sent_packets += 1
                sent_bytes += len(payload)
                sample_packets += 1
                sample_bytes += len(payload)
                if flow_id and time.monotonic() >= next_sample_at:
                    sample_elapsed = max(time.monotonic() - sample_started, 0.001)
                    try:
                        latest_probe = measure_ping(
                            current_interface,
                            current_gateway,
                            1,
                            min(56, max(8, packet_size)),
                            3,
                        )
                    except (OSError, RuntimeError, CommandError):
                        latest_probe = None
                    sample = {
                        "throughput_mbps": sample_bytes * 8 / sample_elapsed / 1_000_000,
                        # A failed probe is degraded evidence, not zero latency.
                        "rtt_ms": latest_probe.rtt_ms if latest_probe else 10_000.0,
                        "jitter_ms": latest_probe.jitter_ms if latest_probe else 10_000.0,
                        "loss_percent": latest_probe.loss_percent if latest_probe else 100.0,
                        "packet_size_bytes": packet_size,
                        "message_rate_per_second": sample_packets / sample_elapsed,
                    }
                    try:
                        api_request(
                            "POST",
                            self.intent_api_url + f"/api/v1/flows/{flow_id}/samples",
                            {
                                "metrics": sample,
                                "interface": current_interface,
                                "gateway": current_gateway,
                            },
                        )
                    except (OSError, TimeoutError, ValueError):
                        pass
                    sample_packets = sample_bytes = 0
                    sample_started = time.monotonic()
                    next_sample_at = sample_started + 2.0
                delay = interval - (time.monotonic() - now)
                if delay > 0:
                    time.sleep(delay)
        finally:
            if sock:
                sock.close()
            if flow_registered and end_intent_on_exit:
                try:
                    api_request(
                        "DELETE", self.intent_api_url + f"/api/v1/flows/{flow_id}"
                    )
                except (OSError, TimeoutError, ValueError):
                    pass
                else:
                    intent_ended = True
            elif intent_id and end_intent_on_exit:
                try:
                    api_request(
                        "DELETE",
                        self.intent_api_url + f"/api/v1/intents/{intent_id}",
                    )
                except (OSError, TimeoutError):
                    pass
                else:
                    intent_ended = True
        elapsed = max(time.monotonic() - started, 0.001)
        return {
            "service": service,
            "intent_id": resolved_intent_id,
            "flow_id": flow_id,
            "duration_seconds": elapsed,
            "sent_packets": sent_packets,
            "sent_bytes": sent_bytes,
            "send_errors": send_errors,
            "binding_errors": binding_errors,
            "average_message_rate_per_second": sent_packets / elapsed,
            "final_interface": current_interface,
            "switches": switches,
            "binding_acknowledged": acknowledged,
            "intent_ended": intent_ended,
        }
