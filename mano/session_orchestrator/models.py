from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class FlowState(str, Enum):
    DEFAULT_ACTIVE = "DEFAULT_ACTIVE"
    OBSERVING = "OBSERVING"
    DECIDING = "DECIDING"
    ENSURING_SLICE = "ENSURING_SLICE"
    ESTABLISHING = "ESTABLISHING"
    VALIDATING = "VALIDATING"
    STEERING = "STEERING"
    DRAINING = "DRAINING"
    RELEASING = "RELEASING"
    COMPLETED = "COMPLETED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"


@dataclass(frozen=True)
class SliceProfile:
    name: str
    namespace: str
    sst: int
    sd: str
    dnn: str
    gateway: str
    pdu_session_id: int
    tun_pattern: str
    service_class: str = ""
    protected: bool = True
    max_rtt_ms: float = 200.0
    max_jitter_ms: float = 100.0
    max_loss_percent: float = 5.0
    min_throughput_mbps: float = 0.0
    capacity_mbps: float = 10.0
    max_pdu_sessions: int = 32
    max_message_rate_per_second: float = 1000.0
    baseline_rtt_ms: float = 150.0
    baseline_jitter_ms: float = 75.0
    baseline_loss_percent: float = 2.0
    five_qi: int = 9
    session_ambr_mbps: float = 10.0
    arp_priority: int = 8
    downlink_limit_mbps: float = 10.0
    benchmark_downlink_mbps: float = 0.0
    downlink_burst_kb: int = 64


@dataclass(frozen=True)
class ServiceIntent:
    service: str
    ue_id: int = 1
    duration_seconds: int = 20
    packet_size_bytes: int = 120
    message_rate_per_second: float = 10.0


@dataclass
class TrafficMetrics:
    throughput_mbps: float
    rtt_ms: float
    jitter_ms: float
    loss_percent: float
    packet_size_bytes: int
    message_rate_per_second: float
    samples: int
    interface: str
    gateway: str


@dataclass(frozen=True)
class SliceCandidate:
    profile: str
    slice_id: str
    sst: int
    sd: str
    dnn: str
    score: float
    reason: str
    requirement_fit: float = 0.0
    qos_headroom: float = 0.0
    health_load: float = 0.0
    intent_affinity: float = 0.0
    evidence_confidence: float = 0.0
    effective_capacity_mbps: float = 0.0
    evidence_status: str = "UNVERIFIED"
    eligible: bool = True


@dataclass(frozen=True)
class SliceRuntime:
    exists: bool = True
    healthy: bool = True
    pdu_sessions: float = 0.0
    cpu_load_ratio: float = 0.0
    memory_load_ratio: float = 0.0
    downlink_enforcement_up: bool | None = None
    enforced_downlink_mbps: float | None = None


@dataclass
class FlowEvent:
    at: str
    state: str
    detail: str


@dataclass
class FlowResult:
    flow_id: str
    state: FlowState
    intent: ServiceIntent
    events: list[FlowEvent] = field(default_factory=list)
    source: SliceProfile | None = None
    target: SliceProfile | None = None
    metrics: TrafficMetrics | None = None
    slice_action: str | None = None
    decision_reason: str | None = None
    decision_candidates: list[SliceCandidate] = field(default_factory=list)
    source_session_active: bool = True
    target_session_active: bool = False
    active_interface: str | None = None
    old_session_released: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["state"] = self.state.value
        return result
