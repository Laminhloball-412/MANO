from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Mapping


class RanMetricsSchemaError(ValueError):
    """Raised when an OCUDU scheduler report cannot be represented faithfully."""


@dataclass(frozen=True)
class RanUeSample:
    cell_id: str
    pci: str
    du_ue_index: str
    rnti: str
    connection_id: str
    dl_prbs_used: int
    ul_prbs_used: int
    dl_prb_symbols_used: int
    ul_prb_symbols_used: int
    dl_prb_share_ratio: float | None
    ul_prb_share_ratio: float | None
    dl_bitrate_mbps: float | None
    ul_bitrate_mbps: float | None
    cqi: float | None
    dl_mcs: float | None
    ul_mcs: float | None
    dl_bler_ratio: float | None
    ul_bler_ratio: float | None


@dataclass(frozen=True)
class RanSnapshot:
    source_timestamp: float
    received_at: float
    ues: tuple[RanUeSample, ...]


@dataclass(frozen=True)
class _ParsedUe:
    cell_id: str
    pci: str
    du_ue_index: str
    rnti: str
    dl_prbs_used: int
    ul_prbs_used: int
    dl_prb_symbols_used: int
    ul_prb_symbols_used: int
    dl_prb_share_ratio: float | None
    ul_prb_share_ratio: float | None
    dl_bitrate_mbps: float | None
    ul_bitrate_mbps: float | None
    cqi: float | None
    dl_mcs: float | None
    ul_mcs: float | None
    dl_bler_ratio: float | None
    ul_bler_ratio: float | None


class ConnectionTracker:
    """Tracks currently visible cell/RNTI connections across report windows."""

    def __init__(self) -> None:
        self._active: dict[tuple[str, str], str] = {}
        self._generations: dict[tuple[str, str], int] = {}

    def observe(self, report: dict, received_at: float) -> RanSnapshot:
        return parse_scheduler_report(report, received_at, self)

    def _snapshot(
        self, source_timestamp: float, received_at: float, parsed_ues: list[_ParsedUe]
    ) -> RanSnapshot:
        current_keys = {(ue.cell_id, ue.rnti) for ue in parsed_ues}
        for key in set(self._active) - current_keys:
            del self._active[key]

        samples = []
        received_at_ns = int(received_at * 1_000_000_000)
        for ue in parsed_ues:
            key = (ue.cell_id, ue.rnti)
            connection_id = self._active.get(key)
            if connection_id is None:
                generation = self._generations.get(key, -1) + 1
                self._generations[key] = generation
                material = f"{ue.cell_id}|{ue.rnti}|{received_at_ns}|{generation}"
                connection_id = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
                self._active[key] = connection_id
            samples.append(
                RanUeSample(
                    cell_id=ue.cell_id,
                    pci=ue.pci,
                    du_ue_index=ue.du_ue_index,
                    rnti=ue.rnti,
                    connection_id=connection_id,
                    dl_prbs_used=ue.dl_prbs_used,
                    ul_prbs_used=ue.ul_prbs_used,
                    dl_prb_symbols_used=ue.dl_prb_symbols_used,
                    ul_prb_symbols_used=ue.ul_prb_symbols_used,
                    dl_prb_share_ratio=ue.dl_prb_share_ratio,
                    ul_prb_share_ratio=ue.ul_prb_share_ratio,
                    dl_bitrate_mbps=ue.dl_bitrate_mbps,
                    ul_bitrate_mbps=ue.ul_bitrate_mbps,
                    cqi=ue.cqi,
                    dl_mcs=ue.dl_mcs,
                    ul_mcs=ue.ul_mcs,
                    dl_bler_ratio=ue.dl_bler_ratio,
                    ul_bler_ratio=ue.ul_bler_ratio,
                )
            )
        samples.sort(key=lambda sample: (sample.cell_id, sample.rnti, sample.connection_id))
        return RanSnapshot(source_timestamp, received_at, tuple(samples))


def parse_scheduler_report(
    report: dict, received_at: float, tracker: ConnectionTracker
) -> RanSnapshot:
    """Strictly convert one OCUDU scheduler report into the current RAN view."""
    if not isinstance(tracker, ConnectionTracker):
        raise TypeError("tracker must be a ConnectionTracker")
    if not isinstance(report, Mapping):
        raise RanMetricsSchemaError("scheduler report must be an object")
    source_timestamp = _source_timestamp(report, received_at)
    cells = _required_list(report, "cells", "report")
    parsed_ues: list[_ParsedUe] = []
    for cell_index, cell in enumerate(cells):
        cell_object = _required_mapping(cell, f"cells[{cell_index}]")
        cell_metrics = _required_mapping(
            _required(cell_object, "cell_metrics", f"cells[{cell_index}]"),
            f"cells[{cell_index}].cell_metrics",
        )
        context = f"cells[{cell_index}].cell_metrics"
        pci = str(_required_nonnegative_integer(cell_metrics, "pci", context))
        _required_nonnegative_integer(cell_metrics, "nof_prbs", context)
        _required_nonnegative_integer(cell_metrics, "nof_slots", context)
        _required_nonnegative_integer(cell_metrics, "nof_dl_slots", context)
        _required_nonnegative_integer(cell_metrics, "nof_ul_slots", context)
        dl_available = _required_nonnegative_integer(
            cell_metrics, "dl_prb_symbols_available", context
        )
        ul_available = _required_nonnegative_integer(
            cell_metrics, "ul_prb_symbols_available", context
        )
        ue_list = _required_list(cell_object, "ue_list", f"cells[{cell_index}]")
        for ue_index, ue in enumerate(ue_list):
            parsed_ues.append(
                _parse_ue(
                    _required_mapping(ue, f"cells[{cell_index}].ue_list[{ue_index}]"),
                    str(cell_index),
                    pci,
                    dl_available,
                    ul_available,
                    f"cells[{cell_index}].ue_list[{ue_index}]",
                )
            )
    return tracker._snapshot(source_timestamp, received_at, parsed_ues)


def _parse_ue(
    ue: Mapping[str, Any],
    cell_id: str,
    pci: str,
    dl_available: int,
    ul_available: int,
    context: str,
) -> _ParsedUe:
    rnti = f"{_required_nonnegative_integer(ue, 'rnti', context):04x}"
    dl_symbols = _required_nonnegative_integer(ue, "dl_nof_prb_symbols", context)
    ul_symbols = _required_nonnegative_integer(ue, "ul_nof_prb_symbols", context)
    return _ParsedUe(
        cell_id=cell_id,
        pci=pci,
        du_ue_index=str(_required_nonnegative_integer(ue, "ue", context)),
        rnti=rnti,
        dl_prbs_used=_required_nonnegative_integer(ue, "dl_nof_prbs", context),
        ul_prbs_used=_required_nonnegative_integer(ue, "ul_nof_prbs", context),
        dl_prb_symbols_used=dl_symbols,
        ul_prb_symbols_used=ul_symbols,
        dl_prb_share_ratio=_ratio(dl_symbols, dl_available),
        ul_prb_share_ratio=_ratio(ul_symbols, ul_available),
        dl_bitrate_mbps=_scaled_optional_number(ue, "dl_brate", context),
        ul_bitrate_mbps=_scaled_optional_number(ue, "ul_brate", context),
        cqi=_optional_number(ue, "cqi", context),
        dl_mcs=_optional_number(ue, "dl_mcs", context),
        ul_mcs=_optional_number(ue, "ul_mcs", context),
        dl_bler_ratio=_bler(ue, "dl", context),
        ul_bler_ratio=_bler(ue, "ul", context),
    )


def _source_timestamp(report: Mapping[str, Any], received_at: float) -> float:
    value = report.get("timestamp", received_at)
    return _number(value, "report.timestamp")


def _required(container: Mapping[str, Any], field: str, context: str) -> Any:
    if field not in container:
        raise RanMetricsSchemaError(f"missing required field {context}.{field}")
    return container[field]


def _required_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RanMetricsSchemaError(f"{context} must be an object")
    return value


def _required_list(container: Mapping[str, Any], field: str, context: str) -> list[Any]:
    value = _required(container, field, context)
    if not isinstance(value, list):
        raise RanMetricsSchemaError(f"{context}.{field} must be an array")
    return value


def _number(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RanMetricsSchemaError(f"{context} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise RanMetricsSchemaError(f"{context} must be a finite number")
    return number


def _required_integer(container: Mapping[str, Any], field: str, context: str) -> int:
    value = _required(container, field, context)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RanMetricsSchemaError(f"{context}.{field} must be an integer")
    return value


def _required_nonnegative_integer(
    container: Mapping[str, Any], field: str, context: str
) -> int:
    value = _required_integer(container, field, context)
    if value < 0:
        raise RanMetricsSchemaError(f"{context}.{field} must be non-negative")
    return value


def _optional_number(container: Mapping[str, Any], field: str, context: str) -> float | None:
    if field not in container or container[field] is None:
        return None
    return _number(container[field], f"{context}.{field}")


def _scaled_optional_number(
    container: Mapping[str, Any], field: str, context: str
) -> float | None:
    value = _optional_number(container, field, context)
    return None if value is None else value / 1_000_000


def _ratio(used: int, available: int) -> float | None:
    return None if available == 0 else used / available


def _bler(ue: Mapping[str, Any], direction: str, context: str) -> float | None:
    ok = _optional_nonnegative_integer(ue, f"{direction}_nof_ok", context)
    nok = _optional_nonnegative_integer(ue, f"{direction}_nof_nok", context)
    if ok is None or nok is None:
        return None
    denominator = ok + nok
    return None if denominator <= 0 else nok / denominator


def _optional_nonnegative_integer(
    container: Mapping[str, Any], field: str, context: str
) -> int | None:
    if field not in container or container[field] is None:
        return None
    return _required_nonnegative_integer(container, field, context)


_UE_METRICS: tuple[tuple[str, str, str], ...] = (
    ("ocudu_ran_ue_connected", "connected", "Whether the UE is in the current scheduler report."),
    ("ocudu_ran_ue_dl_prbs_used", "dl_prbs_used", "DL PRB grants used in the report window."),
    ("ocudu_ran_ue_ul_prbs_used", "ul_prbs_used", "UL PRB grants used in the report window."),
    ("ocudu_ran_ue_dl_prb_symbols_used", "dl_prb_symbols_used", "DL PRB-symbols used in the report window."),
    ("ocudu_ran_ue_ul_prb_symbols_used", "ul_prb_symbols_used", "UL PRB-symbols used in the report window."),
    ("ocudu_ran_ue_dl_prb_share_ratio", "dl_prb_share_ratio", "DL share of available PRB-symbols."),
    ("ocudu_ran_ue_ul_prb_share_ratio", "ul_prb_share_ratio", "UL share of available PRB-symbols."),
    ("ocudu_ran_ue_dl_bitrate_mbps", "dl_bitrate_mbps", "DL scheduler bitrate in Mbit/s."),
    ("ocudu_ran_ue_ul_bitrate_mbps", "ul_bitrate_mbps", "UL scheduler bitrate in Mbit/s."),
    ("ocudu_ran_ue_cqi", "cqi", "Channel quality indicator."),
    ("ocudu_ran_ue_dl_mcs", "dl_mcs", "Downlink MCS index."),
    ("ocudu_ran_ue_ul_mcs", "ul_mcs", "Uplink MCS index."),
    ("ocudu_ran_ue_dl_bler_ratio", "dl_bler_ratio", "Downlink HARQ BLER ratio."),
    ("ocudu_ran_ue_ul_bler_ratio", "ul_bler_ratio", "Uplink HARQ BLER ratio."),
)
_GLOBAL_METRICS: tuple[tuple[str, str], ...] = (
    ("ocudu_ran_metrics_sample_age_seconds", "Age of the latest scheduler report."),
    ("ocudu_ran_metrics_exporter_up", "Whether the RAN metrics exporter is up."),
)


def render_ran_metrics(snapshot: RanSnapshot, now: float, exporter_up: bool) -> str:
    """Render a deterministic Prometheus text exposition for the latest snapshot."""
    lines: list[str] = []
    for metric, _, help_text in _UE_METRICS:
        lines.extend((f"# HELP {metric} {help_text}", f"# TYPE {metric} gauge"))
    for metric, help_text in _GLOBAL_METRICS:
        lines.extend((f"# HELP {metric} {help_text}", f"# TYPE {metric} gauge"))

    for ue in sorted(snapshot.ues, key=lambda item: (item.cell_id, item.rnti, item.connection_id)):
        labels = _ue_labels(ue)
        for metric, attribute, _ in _UE_METRICS:
            value: float | int | None = 1 if attribute == "connected" else getattr(ue, attribute)
            if value is not None:
                lines.append(f"{metric}{{{labels}}} {_format_value(value)}")
    lines.append(
        "ocudu_ran_metrics_sample_age_seconds "
        f"{_format_value(float(now) - snapshot.received_at)}"
    )
    lines.append(f"ocudu_ran_metrics_exporter_up {1 if exporter_up else 0}")
    return "\n".join(lines) + "\n"


def _ue_labels(ue: RanUeSample) -> str:
    fields = (
        ("cell_id", ue.cell_id),
        ("pci", ue.pci),
        ("du_ue_index", ue.du_ue_index),
        ("rnti", ue.rnti),
        ("connection_id", ue.connection_id),
    )
    return ",".join(f'{key}="{_escape_label(value)}"' for key, value in fields)


def _escape_label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_value(value: float | int) -> str:
    return str(value) if isinstance(value, int) else repr(value)
