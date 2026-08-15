#!/usr/bin/env python3
import argparse
import importlib.util
import signal
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("module_path")
    args = parser.parse_args()

    spec = importlib.util.spec_from_file_location("ansa_runtime_relay", args.module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    block = module.multi_ue_scenario()
    stopping = False

    def stop(_signal=None, _frame=None):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        block.stop()
        block.wait()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    block.start()
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
