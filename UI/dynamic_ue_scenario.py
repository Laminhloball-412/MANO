#!/usr/bin/env python3
"""GNU Radio ZMQ RF bridge for a selectable set of srsUE instances.

The original multi_ue_scenario.py always waits for UE 1, UE 2 and UE 3.
This UI-owned scenario only creates paths for the UE IDs supplied with --ues,
so the GNU Radio scheduler is not blocked by disabled UEs.
"""

import argparse
import os
import signal
import threading

from gnuradio import blocks, gr, zeromq


SAMPLE_RATE = 11_520_000
ZMQ_TIMEOUT_MS = 100
ZMQ_HWM = -1
MAX_UE_ID = int(os.getenv("UI_MAX_UES", "8"))


def path_loss_db(ue_id):
    # Preserve the original UE1/2/3 values. Additional software UEs are capped
    # at 20 dB so UE4+ are not made unusable merely because of their ID.
    return min((ue_id - 1) * 10, 20)


def parse_ue_ids(raw_value):
    try:
        ue_ids = sorted({int(value.strip()) for value in raw_value.split(",") if value.strip()})
    except ValueError as error:
        raise argparse.ArgumentTypeError("UE IDs must be comma-separated integers") from error
    if not ue_ids or any(ue_id < 1 or ue_id > MAX_UE_ID for ue_id in ue_ids):
        raise argparse.ArgumentTypeError(f"UE IDs must be between 1 and {MAX_UE_ID}")
    return ue_ids


class DynamicUeScenario(gr.top_block):
    def __init__(self, ue_ids):
        super().__init__("srsRAN dynamic UE scenario", catch_exceptions=True)
        self.ue_ids = ue_ids

        # gNB downlink: gNB TX (2000) -> selected UE RX ports (x100).
        self.gnb_downlink = zeromq.req_source(
            gr.sizeof_gr_complex, 1, "tcp://127.0.0.1:2000",
            ZMQ_TIMEOUT_MS, False, ZMQ_HWM, False,
        )
        self.downlink_throttle = blocks.throttle(
            gr.sizeof_gr_complex, SAMPLE_RATE, True,
        )
        self.connect(self.gnb_downlink, self.downlink_throttle)

        # Selected UE uplinks are combined and sent to the gNB RX port (2001).
        self.uplink_adder = blocks.add_vcc(1)
        self.gnb_uplink = zeromq.rep_sink(
            gr.sizeof_gr_complex, 1, "tcp://127.0.0.1:2001",
            ZMQ_TIMEOUT_MS, False, ZMQ_HWM, True,
        )
        self.connect(self.uplink_adder, self.gnb_uplink)

        self.downlink_gains = []
        self.downlink_sinks = []
        self.uplink_sources = []
        self.uplink_gains = []

        for input_index, ue_id in enumerate(ue_ids):
            amplitude = 10 ** (-path_loss_db(ue_id) / 20.0)
            ue_downlink_port = 2000 + (ue_id * 100)
            ue_uplink_port = ue_downlink_port + 1

            downlink_gain = blocks.multiply_const_cc(amplitude)
            downlink_sink = zeromq.rep_sink(
                gr.sizeof_gr_complex, 1, f"tcp://127.0.0.1:{ue_downlink_port}",
                ZMQ_TIMEOUT_MS, False, ZMQ_HWM, True,
            )
            self.connect(self.downlink_throttle, downlink_gain, downlink_sink)
            self.downlink_gains.append(downlink_gain)
            self.downlink_sinks.append(downlink_sink)

            uplink_source = zeromq.req_source(
                gr.sizeof_gr_complex, 1, f"tcp://127.0.0.1:{ue_uplink_port}",
                ZMQ_TIMEOUT_MS, False, ZMQ_HWM, False,
            )
            uplink_gain = blocks.multiply_const_cc(amplitude)
            self.connect(uplink_source, uplink_gain)
            self.connect((uplink_gain, 0), (self.uplink_adder, input_index))
            self.uplink_sources.append(uplink_source)
            self.uplink_gains.append(uplink_gain)


def main():
    parser = argparse.ArgumentParser(description="Dynamic srsRAN multi-UE RF bridge")
    parser.add_argument(
        "--ues", type=parse_ue_ids, default=parse_ue_ids("1,2,3"),
        help=f"Comma-separated UE IDs from 1 to {MAX_UE_ID}, for example 1 or 1,4",
    )
    args = parser.parse_args()

    stop_event = threading.Event()
    top_block = DynamicUeScenario(args.ues)

    def request_stop(_signal_number=None, _frame=None):
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    print(f"Dynamic RF paths ready for UE(s): {','.join(map(str, args.ues))}", flush=True)
    top_block.start()
    try:
        stop_event.wait()
    finally:
        top_block.stop()
        top_block.wait()


if __name__ == "__main__":
    main()
