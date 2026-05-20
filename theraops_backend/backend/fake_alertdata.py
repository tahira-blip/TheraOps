from __future__ import annotations

from datetime import datetime, timezone, timedelta
import random
from typing import List

NETWORKS = [
    {"network_code": "qtc-l7f2b7", "network_id": 71},
    {"network_code": "dtc-m9k3p1", "network_id": 94},
    {"network_code": "vpc-x2r8s4", "network_id": 38},
]

DEVICES = [
    {"device_id": 1362, "device_name": "TM815672"},
    {"device_id": 1234, "device_name": "TM901234"},
    {"device_id": 1089, "device_name": "TM556789"},
]

FAILURE_CODES = [
    "con_0002",
    "pc_0008",
    "pc_0010",
    "pc_0012",
    "samdt_0003",
    "samdt_0006",
    "pc_0014",
]


def generate_fake_network_events(num_events: int = 50) -> List[dict]:
    events: List[dict] = []
    now = datetime.now(timezone.utc)
    for i in range(num_events):
        network = random.choice(NETWORKS)
        device = random.choice(DEVICES)
        code = random.choice(FAILURE_CODES)
        # spread timestamps across last 48 hours
        delta = timedelta(seconds=random.randint(0, 48 * 3600))
        ts = (now - delta).astimezone(timezone.utc).isoformat()

        events.append(
            {
                "timestamp": ts,
                "event_code": code,
                "device_id": device["device_id"],
                "device_name": device["device_name"],
                "network_code": network["network_code"],
                "network_id": network["network_id"],
                "location_name": f"Site {random.choice(['A','B','C'])}",
            }
        )

    return events
