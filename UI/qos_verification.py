import re


_UE_RE = re.compile(r"\bue=(\d+)\b")
_REQUESTED_RE = re.compile(
    r"PDU session QoS requested: operation=setup "
    r"psi=(\d+) sst=(\d+) sd=([0-9a-fA-F]{6}|none) "
    r"qfi=(\d+) five_qi=(\d+|invalid).*?arp_priority=(\d+)"
)
_ACCEPTED_RE = re.compile(
    r"PDU session QoS accepted: operation=setup psi=(\d+) qfi=(\d+)"
)
_MAPPED_RE = re.compile(r"\bpsi=(\d+): Mapping QFI=(\d+)\b")


def parse_gnb_qos_event(line):
    ue_match = _UE_RE.search(line or "")
    if not ue_match:
        return None
    gnb_ue_id = int(ue_match.group(1))

    requested = _REQUESTED_RE.search(line)
    if requested:
        five_qi = requested.group(5)
        return {
            "event": "requested",
            "gnb_ue_id": gnb_ue_id,
            "pdu_session_id": int(requested.group(1)),
            "sst": int(requested.group(2)),
            "sd": requested.group(3).lower(),
            "qfi": int(requested.group(4)),
            "five_qi": None if five_qi == "invalid" else int(five_qi),
            "arp_priority": int(requested.group(6)),
        }

    accepted = _ACCEPTED_RE.search(line)
    if accepted:
        return {
            "event": "accepted",
            "gnb_ue_id": gnb_ue_id,
            "pdu_session_id": int(accepted.group(1)),
            "qfi": int(accepted.group(2)),
        }

    mapped = _MAPPED_RE.search(line)
    if mapped:
        return {
            "event": "mapped",
            "gnb_ue_id": gnb_ue_id,
            "pdu_session_id": int(mapped.group(1)),
            "qfi": int(mapped.group(2)),
        }
    return None


def find_open5gs_qos(document, sst, sd, dnn):
    expected_sd = str(sd or "").lower().zfill(6)
    expected_dnn = str(dnn or "").lower()
    for slice_item in (document or {}).get("slice") or []:
        item_sd = str(slice_item.get("sd") or "").lower().zfill(6)
        if int(slice_item.get("sst", -1)) != int(sst) or item_sd != expected_sd:
            continue
        for session in slice_item.get("session") or []:
            if str(session.get("name") or "").lower() != expected_dnn:
                continue
            qos = session.get("qos") or {}
            arp = qos.get("arp") or {}
            return {
                "five_qi": qos.get("index"),
                "arp_priority": arp.get("priority_level"),
            }
    return None


def evaluate_qos_verification(
    *, expected_5qi, expected_arp, received_5qi, received_arp,
    accepted, mapped,
):
    if expected_5qi is None:
        return "open5gs_unavailable"
    if received_5qi is None:
        return "waiting_for_ngap"
    if int(expected_5qi) != int(received_5qi):
        return "mismatch"
    if expected_arp is not None and received_arp is not None:
        if int(expected_arp) != int(received_arp):
            return "mismatch"
    if accepted and mapped:
        return "verified"
    if accepted:
        return "ran_accepted"
    return "ngap_received"
