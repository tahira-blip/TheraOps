from __future__ import annotations

ISSUE_CATEGORIES = {
    "device_offline":   { "emoji": "🔴", "label": "Device Offline",       "event_codes": ["con_0002", "pc_0010"],              "severity": "critical" },
    "sensor_offline":   { "emoji": "📷", "label": "Sensor Offline",       "event_codes": ["pc_0008", "scon_0002", "samdt_0006"],"severity": "critical" },
    "connection_error": { "emoji": "🌐", "label": "Connection Error",      "event_codes": ["pc_0012", "samdt_0003"],             "severity": "high"     },
    "fov_obstruction":  { "emoji": "⚠️", "label": "Camera Obstruction",   "event_codes": ["pc_0014", "samdt_0005"],             "severity": "medium"   },
}

RESOLUTION_CODES = ["pc_0007", "pc_0009", "pc_0011", "samdt_0002"]


def categorize_event(event_code: str) -> str:
    """Returns category key or 'unknown'"""
    if not event_code:
        return "unknown"
    ec = event_code.strip()
    for key, meta in ISSUE_CATEGORIES.items():
        if ec in meta.get("event_codes", []):
            return key
    return "unknown"


def is_resolution_event(event_code: str) -> bool:
    """Returns True if this event closes an alert"""
    if not event_code:
        return False
    return event_code.strip() in RESOLUTION_CODES
