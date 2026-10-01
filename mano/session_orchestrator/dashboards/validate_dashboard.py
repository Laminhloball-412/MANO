#!/usr/bin/env python3
"""Validate every PromQL expression in the generated dashboard."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prometheus", default="http://127.0.0.1:30090")
    parser.add_argument("--dashboard", type=Path, default=Path(__file__).with_name("ansa-multipdu-dashboard.json"))
    args = parser.parse_args()
    dashboard = json.loads(args.dashboard.read_text(encoding="utf-8"))
    errors = []
    count = 0
    for item in dashboard["panels"]:
        for query in item.get("targets", []):
            expression = query.get("expr")
            if not expression:
                continue
            for variable in ("$ue", "$pdu", "$service", "$flow", "$profile", "$slice", "$cell", "$rnti"):
                expression = expression.replace(variable, ".*")
            url = args.prometheus.rstrip("/") + "/api/v1/query?" + urlencode({"query": expression})
            try:
                with urlopen(url, timeout=5) as response:
                    result = json.load(response)
                if result.get("status") != "success":
                    errors.append((item["title"], result))
            except Exception as error:
                errors.append((item["title"], str(error)))
            count += 1
    if errors:
        for title, error in errors:
            print(f"FAIL {title}: {error}")
        return 1
    print(f"OK: {count} PromQL expressions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
