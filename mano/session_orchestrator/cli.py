from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .controller import SessionOrchestrator, build_profiles, load_config
from .metrics import PrometheusReader, SnapshotStore, serve
from .intent_engine import (
    IntentExecutor,
    IntentService,
    LiveTelemetryReader,
    api_request,
)
from .intent_runtime import IntentRegistry, RuntimeStore
from .models import ServiceIntent
from .oai_control import OaiTelnetControl
from .slice_manager import SliceManager
from .slice_runtime import SliceRuntimeCollector
from .steering import BoundInterfaceSteering
from .ue_telemetry import UeTelemetryCollector
from .app_simulator import UdpApplicationSimulator
from .qos_enforcement import QosEnforcer


def build_controller(config: dict) -> SessionOrchestrator:
    profiles = build_profiles(config)
    store = SnapshotStore(Path(config["state_file"]))
    slices = SliceManager(
        Path(config["intent_flow"]),
        set(config.get("protected_namespaces", [])),
        bool(config.get("dry_run_deallocate", True)),
    )
    telnet = config.get("oai_telnet", {})
    prometheus = (
        PrometheusReader(
            config["prometheus_url"],
            int(config.get("prometheus_wait_seconds", 10)),
        )
        if config.get("prometheus_url") else None
    )
    return SessionOrchestrator(
        profiles,
        store,
        slices,
        OaiTelnetControl(
            telnet.get("host", "127.0.0.1"), int(telnet.get("port", 9090))
        ),
        BoundInterfaceSteering(),
        int(config.get("stable_seconds", 15)),
        int(config.get("drain_seconds", 3)),
        prometheus=prometheus,
    )


def build_intent_service(config: dict) -> IntentService:
    profiles = build_profiles(config)
    registry = IntentRegistry(
        RuntimeStore(Path(config.get(
            "intent_state_file",
            "/var/lib/ansa-session-orchestrator/intents.json",
        )))
    )
    prometheus_url = config["prometheus_url"]
    telnet = config.get("oai_telnet", {})
    executor = IntentExecutor(
        registry,
        profiles,
        LiveTelemetryReader(
            prometheus_url, int(config.get("max_metric_age_seconds", 30))
        ),
        PrometheusReader(prometheus_url, 0),
        SliceManager(
            Path(config["intent_flow"]),
            set(config.get("protected_namespaces", [])),
            bool(config.get("dry_run_deallocate", True)),
        ),
        OaiTelnetControl(
            telnet.get("host", "127.0.0.1"), int(telnet.get("port", 9090))
        ),
        int(config.get("stable_seconds", 15)),
        int(config.get("drain_seconds", 3)),
        int(config.get("slice_idle_timeout_seconds", 300)),
        [int(value) for value in config.get("ue_ids", [1])],
        int(config.get("pdu_establish_timeout_seconds", 45)),
        int(config.get("max_pdu_sessions_per_ue", 3)),
        QosEnforcer(
            min_interval_seconds=int(config.get("qos_reconcile_seconds", 30))
        ) if config.get("qos_enforcement_enabled", True) else None,
        int(config.get("min_flow_samples", 2)),
        float(config.get("min_observation_seconds", 3)),
    )
    return IntentService(
        registry, executor, float(config.get("intent_poll_seconds", 1))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="ANSA make-before-break PDU orchestrator")
    parser.add_argument("--config", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    serve_command = commands.add_parser("serve", help="serve Prometheus metrics and flow state")
    serve_command.add_argument("--host")
    serve_command.add_argument("--port", type=int)
    intentd_command = commands.add_parser(
        "intentd", help="run the asynchronous intent API and worker"
    )
    intentd_command.add_argument("--host")
    intentd_command.add_argument("--port", type=int)
    submit_command = commands.add_parser("submit", help="submit an application intent")
    submit_command.add_argument("--service", required=True)
    submit_command.add_argument("--ue-id", type=int, default=1)
    submit_command.add_argument(
        "--mode", choices=("auto", "migrate", "concurrent"), default="auto"
    )
    submit_command.add_argument("--replaces-intent-id")
    submit_command.add_argument("--lease-seconds", type=int, default=60)
    submit_command.add_argument("--flow-id")
    submit_command.add_argument(
        "--intent-source",
        choices=("application", "inferred", "operator"),
        default="operator",
    )
    ack_command = commands.add_parser("ack", help="acknowledge a new application binding")
    ack_command.add_argument("intent_id")
    end_command = commands.add_parser("end", help="end an application intent")
    end_command.add_argument("intent_id")
    commands.add_parser("status", help="read asynchronous intent state")
    simulate_command = commands.add_parser(
        "simulate", help="run independent application traffic over a UE TUN"
    )
    simulate_command.add_argument("--service", required=True)
    simulate_command.add_argument("--duration", type=int, default=30)
    simulate_command.add_argument("--interface", default="oaitun_ue1")
    simulate_command.add_argument("--gateway", default="10.45.1.1")
    simulate_command.add_argument("--intent-id")
    simulate_command.add_argument("--flow-id")
    simulate_command.add_argument("--ue-id", type=int, default=1)
    simulate_command.add_argument("--packet-size", type=int)
    simulate_command.add_argument("--message-rate", type=float)
    simulate_command.add_argument(
        "--keep-intent", action="store_true",
        help="leave the intent active after the simulated application exits",
    )
    run_command = commands.add_parser("run", help="run one service transition")
    run_command.add_argument("--service", required=True)
    run_command.add_argument("--ue-id", type=int, default=1)
    run_command.add_argument("--duration", type=int, default=20)
    run_command.add_argument("--packet-size", type=int, default=120)
    run_command.add_argument("--message-rate", type=float, default=10)
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == "serve":
        listen = config.get("listen", {})
        serve(
            SnapshotStore(Path(config["state_file"])),
            args.host or listen.get("host", "0.0.0.0"),
            args.port or int(listen.get("port", 9105)),
            SliceRuntimeCollector(build_profiles(config)),
            UeTelemetryCollector(
                build_profiles(config),
                [int(value) for value in config.get("ue_ids", [1])],
            ),
        )
        return 0
    intent_api = config.get("intent_api", {})
    intent_base_url = intent_api.get("url", "http://127.0.0.1:9106").rstrip("/")
    if args.command == "intentd":
        service = build_intent_service(config)
        service.serve(
            args.host or intent_api.get("host", "127.0.0.1"),
            args.port or int(intent_api.get("port", 9106)),
        )
        return 0
    if args.command == "submit":
        result = api_request("POST", intent_base_url + "/api/v1/intents", {
            "service": args.service,
            "ue_id": args.ue_id,
            "mode": args.mode,
            "replaces_intent_id": args.replaces_intent_id,
            "lease_seconds": args.lease_seconds,
            "flow_id": args.flow_id,
            "intent_source": args.intent_source,
        })
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "ack":
        result = api_request(
            "POST", intent_base_url + f"/api/v1/intents/{args.intent_id}/ack", {}
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "end":
        result = api_request(
            "DELETE", intent_base_url + f"/api/v1/intents/{args.intent_id}"
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(api_request(
            "GET", intent_base_url + "/api/v1/state"
        ), indent=2))
        return 0
    if args.command == "simulate":
        result = UdpApplicationSimulator(intent_base_url).run(
            args.service,
            args.duration,
            args.interface,
            args.gateway,
            args.intent_id,
            args.packet_size,
            args.message_rate,
            not args.keep_intent,
            args.flow_id,
            args.ue_id,
        )
        print(json.dumps(result, indent=2))
        return 0
    result = build_controller(config).run(
        ServiceIntent(args.service, args.ue_id, args.duration, args.packet_size, args.message_rate)
    )
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.error is None else 1


if __name__ == "__main__":
    try:
        exit_code = main()
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        exit_code = 2
    raise SystemExit(exit_code)
