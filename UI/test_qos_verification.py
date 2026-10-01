import json
import importlib
import unittest
from types import SimpleNamespace

try:
    qos = importlib.import_module('qos_verification')
except ModuleNotFoundError:
    qos = SimpleNamespace()


class QosVerificationTests(unittest.TestCase):
    def test_parses_qos_requested_from_decoded_ngap_log(self):
        line = (
            '2026-09-17T14:11:03 [NGAP] [I] ue=0 ran_ue=0 amf_ue=9: '
            'PDU session QoS requested: operation=setup psi=1 sst=1 sd=000001 '
            'qfi=1 five_qi=9 qos_characteristics=non-dynamic arp_priority=8 '
            'preemption_capability=shall-not-trigger-pre-emption '
            'preemption_vulnerability=not-pre-emptable gbr=no'
        )

        event = getattr(qos, 'parse_gnb_qos_event', lambda _line: None)(line)

        self.assertEqual({
            'event': 'requested', 'gnb_ue_id': 0, 'pdu_session_id': 1,
            'sst': 1, 'sd': '000001', 'qfi': 1, 'five_qi': 9,
            'arp_priority': 8,
        }, event)

    def test_parses_qos_acceptance_and_sdap_mapping(self):
        parse = getattr(qos, 'parse_gnb_qos_event', lambda _line: None)

        accepted = parse(
            '2026 [NGAP] [I] ue=2 ran_ue=2 amf_ue=11: '
            'PDU session QoS accepted: operation=setup psi=2 qfi=1'
        )
        mapped = parse(
            '2026 [SDAP] [I] ue=2 psi=2: Mapping QFI=1 DRB2 '
            'default_drb=false hdr_ul=absent hdr_dl=absent'
        )

        self.assertEqual({
            'event': 'accepted', 'gnb_ue_id': 2,
            'pdu_session_id': 2, 'qfi': 1,
        }, accepted)
        self.assertEqual({
            'event': 'mapped', 'gnb_ue_id': 2,
            'pdu_session_id': 2, 'qfi': 1,
        }, mapped)

    def test_reads_expected_qos_for_matching_open5gs_session(self):
        document = json.loads('''{
          "imsi": "460990000000001",
          "slice": [{"sst": 1, "sd": "000001", "session": [{
            "name": "srsapn", "qos": {"index": 9,
            "arp": {"priority_level": 8}}
          }]}]
        }''')

        actual = getattr(qos, 'find_open5gs_qos', lambda *_args: None)(
            document, 1, '000001', 'srsapn'
        )

        self.assertEqual({'five_qi': 9, 'arp_priority': 8}, actual)

    def test_marks_verified_only_when_open5gs_ran_and_mapping_agree(self):
        verify = getattr(qos, 'evaluate_qos_verification', lambda **_kwargs: None)

        self.assertEqual('verified', verify(
            expected_5qi=9, expected_arp=8,
            received_5qi=9, received_arp=8,
            accepted=True, mapped=True,
        ))
        self.assertEqual('mismatch', verify(
            expected_5qi=9, expected_arp=8,
            received_5qi=5, received_arp=8,
            accepted=True, mapped=True,
        ))
        self.assertEqual('ran_accepted', verify(
            expected_5qi=9, expected_arp=8,
            received_5qi=9, received_arp=8,
            accepted=True, mapped=False,
        ))


if __name__ == '__main__':
    unittest.main()
