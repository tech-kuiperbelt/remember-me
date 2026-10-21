"""Observe loopback readiness; never initiate model loading or repair."""
import json
import sys
from urllib.request import ProxyHandler, build_opener


def main():
    try:
        with build_opener(ProxyHandler({})).open(
            "http://127.0.0.1:1314/api/model", timeout=3
        ) as response:
            ready = json.load(response).get("ready") is True
    except (OSError, ValueError):
        ready = False
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
