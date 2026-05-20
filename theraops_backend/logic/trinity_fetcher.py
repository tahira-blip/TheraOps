from __future__ import annotations

from typing import Any, Dict, List
import logging

import httpx

from .identity_resolver import RosettaStone, STREAM_NONSESSION_EVENTS, STREAM_DEVICE_ERRORS

logger = logging.getLogger(__name__)


EVENT_QUERY = 'event_code:("con_0002" OR "pc_0010" OR "pc_0008" OR "scon_0002" OR "samdt_0006" OR "pc_0012" OR "samdt_0003")'

EVENT_LABELS = {
    "scon_0002": "Sensor Offline",
    "pc_0010": "Edge Device Offline",
    "con_0002": "Device Offline",
    "pc_0008": "Sensor Offline",
    "samdt_0006": "Sensor Offline",
    "pc_0012": "Applet Connection Error",
    "samdt_0003": "Applet Connection Error",
}


def _has_real_id(value: Any) -> bool:
    if value in (None, "", "0", 0):
        return False
    return str(value).strip().lower() not in {"null", "none", "nan"}


def _event_label(event_code: Any) -> str:
    return EVENT_LABELS.get(str(event_code or "").strip(), "Unknown Event")


def _event_detail(msg: Dict[str, Any]) -> str:
    return str(msg.get("device_message") or msg.get("message") or "").strip()


def _service_app_code(msg: Dict[str, Any]) -> str:
    return str(
        msg.get("service")
        or msg.get("app_code")
        or msg.get("device_service_id")
        or msg.get("device_service_name")
        or "unknown-service"
    )


def _identity_lookup_value(msg: Dict[str, Any]) -> Any:
    return (
        msg.get("device_id")
        or msg.get("device_device_id")
        or msg.get("serial_identifier")
        or msg.get("device_device_serial_id")
        or msg.get("device_serial_id")
    )


def _resolve_identity(rs: RosettaStone, value: Any) -> Any:
    if not _has_real_id(value):
        return None
    return rs.resolve_device_id(value) or rs.resolve(str(value))


def _network_id_key(msg: Dict[str, Any], rs: RosettaStone) -> str:
    if _has_real_id(msg.get("network_id")):
        return str(msg.get("network_id"))
    identity = _resolve_identity(rs, _identity_lookup_value(msg))
    if identity and identity.network_id is not None:
        return str(identity.network_id)
    return "unknown"


def _network_code(msg: Dict[str, Any], rs: RosettaStone) -> str:
    identity = _resolve_identity(rs, _identity_lookup_value(msg))
    if identity and identity.network_code:
        return identity.network_code
    return rs.network_code_for(msg.get("network_id"), msg.get("network_code"))


def _serial_identifier(msg: Dict[str, Any], identity: Any = None) -> str | None:
    value = (
        msg.get("serial_identifier")
        or msg.get("device_device_serial_id")
        or msg.get("device_serial_id")
        or (identity.serial if identity else None)
    )
    return str(value) if _has_real_id(value) else None


def _component_event(
    msg: Dict[str, Any],
    component_type: str,
    parent_context: Dict[str, Any],
) -> Dict[str, Any]:
    event_code = msg.get("event_code", "")
    return {
        "timestamp": msg.get("timestamp"),
        "component_type": component_type,
        "event_code": event_code,
        "event_name": _event_label(event_code),
        "device_message": _event_detail(msg),
        "parent_context": parent_context,
    }


def _is_sensor_event(event_code: str, label: str) -> bool:
    return event_code in {"pc_0008", "scon_0002", "samdt_0006"} or "sensor" in label.lower()


def _is_applet_event(event_code: str) -> bool:
    return event_code in {"pc_0012", "samdt_0003"}


async def fetch_trinity_summary(services, window_seconds: int = 3600) -> Dict[str, Any]:
    graylog = services.graylog

    rs = RosettaStone()
    try:
        await rs.build(graylog, window_seconds=3600)
    except httpx.ReadTimeout:
        logger.error("Graylog query timed out during identity resolver build.")
        return {"status": "error", "message": "🚨 Graylog query timed out."}

    try:
        events_res = await graylog.search_relative(
            query=EVENT_QUERY,
            window_seconds=window_seconds,
            fields=[
                "timestamp",
                "event_code",
                "network_id",
                "network_code",
                "location_name",
                "device_id",
                "device_device_id",
                "device_serial_id",
                "device_device_serial_id",
                "serial_identifier",
                "device_name",
                "sensor_id",
                "service",
                "app_code",
                "device_service_id",
                "device_service_name",
                "device_message",
            ],
            limit=2000,
            stream=STREAM_NONSESSION_EVENTS,
        )
    except httpx.ReadTimeout:
        logger.error("Graylog query timed out during events fetch.")
        return {"status": "error", "message": "🚨 Graylog query timed out."}
    except Exception as exc:
        logger.exception("Failed to fetch events for trinity: %s", exc)
        return {"status": "error", "message": "Graylog query failed."}

    device_ids: set[str] = set()
    network_ids: set[str] = set()
    for msg in events_res.messages:
        for field in (
            "device_id",
            "device_device_id",
            "serial_identifier",
            "device_serial_id",
            "device_device_serial_id",
        ):
            if _has_real_id(msg.get(field)):
                device_ids.add(str(msg.get(field)))
        if _has_real_id(msg.get("network_id")):
            network_ids.add(str(msg.get("network_id")))

    logs_messages: List[Dict[str, Any]] = []
    if device_ids or network_ids:
        device_query = " OR ".join(f'"{item}"' for item in sorted(device_ids))
        network_query = " OR ".join(f'"{item}"' for item in sorted(network_ids))
        query_parts: list[str] = []
        if device_query:
            query_parts.extend(
                [
                    f"device_id:({device_query})",
                    f"device_device_id:({device_query})",
                    f"serial_identifier:({device_query})",
                    f"device_serial_id:({device_query})",
                    f"device_device_serial_id:({device_query})",
                ]
            )
        if network_query:
            query_parts.append(f"network_id:({network_query})")

        try:
            logs_res = await graylog.search_relative(
                query=" OR ".join(query_parts),
                window_seconds=window_seconds,
                fields=[
                    "timestamp",
                    "network_id",
                    "network_code",
                    "device_device_id",
                    "device_id",
                    "device_serial_id",
                    "device_device_serial_id",
                    "serial_identifier",
                    "sensor_id",
                    "app_code",
                    "device_service_id",
                    "device_service_name",
                    "event_code",
                    "device_message",
                    "message",
                    "level",
                ],
                limit=2000,
                stream=STREAM_DEVICE_ERRORS,
            )
            logs_messages = logs_res.messages
        except httpx.ReadTimeout:
            logger.error("Graylog query timed out during companion logs fetch.")
            return {"status": "error", "message": "🚨 Graylog query timed out."}
        except Exception as exc:
            logger.exception("Failed to fetch logs for Graylog streams: %s", exc)

    networks: Dict[str, Dict[str, Any]] = {}
    unknowns: Dict[str, Dict[str, Any]] = {}
    ai_flags: List[str] = []

    def ensure_network(msg: Dict[str, Any]) -> Dict[str, Any]:
        net_id = _network_id_key(msg, rs)
        if net_id not in networks:
            net_code = _network_code(msg, rs)
            networks[net_id] = {
                "network_id": net_id,
                "network_code": net_code,
                "network_name": msg.get("location_name") or net_code,
                "field_devices": {},
                "standalone_components": {},
                "logs": [],
            }
        return networks[net_id]

    def parent_context(
        msg: Dict[str, Any],
        *,
        net_entry: Dict[str, Any],
        device_entry: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return {
            "network_id": net_entry.get("network_id"),
            "network_code": net_entry.get("network_code"),
            "device_id": device_entry.get("device_id") if device_entry else None,
            "serial_identifier": device_entry.get("serial_identifier") if device_entry else None,
            "device_name": device_entry.get("name") if device_entry else None,
            "sensor_id": str(msg.get("sensor_id")) if _has_real_id(msg.get("sensor_id")) else None,
            "app_code": _service_app_code(msg)
            if _has_real_id(msg.get("app_code") or msg.get("device_service_id") or msg.get("service"))
            else None,
        }

    def ensure_device(
        msg: Dict[str, Any],
        device_key: str,
        display_name: str,
        identity: Any = None,
    ) -> Dict[str, Any]:
        net_entry = ensure_network(msg)
        if device_key not in net_entry["field_devices"]:
            device_id = msg.get("device_id") or msg.get("device_device_id") or (identity.device_id if identity else device_key)
            net_entry["field_devices"][device_key] = {
                "id": device_key,
                "device_id": str(device_id) if _has_real_id(device_id) else device_key,
                "serial_identifier": _serial_identifier(msg, identity),
                "name": display_name,
                "status": "online",
                "failure_type": None,
                "sensors": {},
                "applets": {},
                "events": [],
            }
        return net_entry["field_devices"][device_key]

    def ensure_sensor(device_entry: Dict[str, Any], sensor_key: str) -> Dict[str, Any]:
        if sensor_key not in device_entry["sensors"]:
            device_entry["sensors"][sensor_key] = {
                "id": sensor_key,
                "status": "online",
                "failure_type": None,
                "applets": {},
                "events": [],
            }
        return device_entry["sensors"][sensor_key]

    def ensure_applet(container: Dict[str, Any], app_key: str) -> Dict[str, Any]:
        applets = container.setdefault("applets", {})
        if app_key not in applets:
            applets[app_key] = {
                "app_code": app_key,
                "name": app_key,
                "status": "impacted",
                "events": [],
            }
        return applets[app_key]

    def ensure_standalone_component(
        msg: Dict[str, Any],
        component_type: str,
        component_key: str,
    ) -> Dict[str, Any]:
        net_entry = ensure_network(msg)
        key = f"{component_type}:{component_key}"
        if key not in net_entry["standalone_components"]:
            component: Dict[str, Any] = {
                "type": component_type,
                "status": "impacted",
                "failure_type": "standalone-component",
                "events": [],
                "path": "Network -> Standalone Components",
            }
            if component_type == "sensor":
                component["id"] = component_key
                component["applets"] = {}
            else:
                component["app_code"] = component_key
                component["name"] = component_key
                component["sensors"] = {}
            net_entry["standalone_components"][key] = component
        return net_entry["standalone_components"][key]

    def attach_event_to_hybrid_tree(msg: Dict[str, Any]) -> None:
        raw_dev = _identity_lookup_value(msg)
        has_device = _has_real_id(raw_dev)
        has_sensor = _has_real_id(msg.get("sensor_id"))
        has_app = _has_real_id(msg.get("app_code") or msg.get("device_service_id") or msg.get("service"))
        event_code = str(msg.get("event_code") or "").strip()
        label = _event_label(event_code)

        if not has_device and (has_app or has_sensor):
            net_entry = ensure_network(msg)
            app_key = _service_app_code(msg)
            sensor_key = str(msg.get("sensor_id")) if has_sensor else None
            context = parent_context(msg, net_entry=net_entry)

            if has_app:
                applet = ensure_standalone_component(msg, "applet", app_key)
                if has_sensor:
                    sensors = applet.setdefault("sensors", {})
                    if sensor_key not in sensors:
                        sensors[sensor_key] = {
                            "id": sensor_key,
                            "status": "offline" if _is_sensor_event(event_code, label) else "impacted",
                            "events": [],
                        }
                    target = sensors[sensor_key] if _is_sensor_event(event_code, label) else applet
                else:
                    target = applet
            else:
                target = ensure_standalone_component(msg, "sensor", sensor_key or "unknown-sensor")

            target["status"] = "offline" if "offline" in label.lower() else target.get("status", "impacted")
            target.setdefault("events", []).append(
                _component_event(msg, target.get("type", "sensor"), context)
            )

            if event_code == "scon_0002":
                ai_flags.append(
                    f"Sensor {sensor_key or 'unknown'} in network {net_entry.get('network_id')} is offline without a device_id. Treat this as a Standalone Sensor issue, not a host NUC/PC failure."
                )
            return

        if not has_device and not has_app and not has_sensor:
            ensure_network(msg)["logs"].append(
                {
                    "timestamp": msg.get("timestamp"),
                    "message": _event_detail(msg),
                    "level": msg.get("level"),
                }
            )
            return

        raw_dev = raw_dev or msg.get("device_name") or "unknown"
        identity = _resolve_identity(rs, raw_dev)
        if identity:
            display = identity.name or identity.serial or identity.device_id or str(raw_dev)
            dev_key = identity.device_id or identity.serial or display
        else:
            display = str(msg.get("device_name") or msg.get("device_service_name") or raw_dev)
            dev_key = str(raw_dev)

        device = ensure_device(msg, str(dev_key), str(display), identity)
        net_entry = ensure_network(msg)
        context = parent_context(msg, net_entry=net_entry, device_entry=device)

        if identity is None and has_device:
            unknowns[str(dev_key)] = unknowns.get(str(dev_key), {})
            unknowns[str(dev_key)]["id_debug"] = f"Missing mapping for Serial/ID: {raw_dev}"
            ai_flags.append(
                f"I found logs for Serial/ID {raw_dev}, but that device has not sent a heartbeat recently. It may be newly provisioned or missing heartbeat data."
            )

        if event_code in {"con_0002", "pc_0010"}:
            device["status"] = "offline"
            device["failure_type"] = "primary"
            device["events"].append(_component_event(msg, "device", context))
            return

        if _is_sensor_event(event_code, label) or has_sensor:
            if device["failure_type"] != "primary":
                device["failure_type"] = "sub-component"
            sensor = ensure_sensor(device, str(msg.get("sensor_id")) if has_sensor else "unknown-sensor")
            sensor["status"] = "offline" if "offline" in label.lower() else "impacted"
            sensor["failure_type"] = "sensor"
            if has_app and _is_applet_event(event_code):
                applet = ensure_applet(sensor, _service_app_code(msg))
                applet["status"] = "error"
                applet["events"].append(_component_event(msg, "applet", context))
            else:
                sensor["events"].append(_component_event(msg, "sensor", context))
            return

        if has_app:
            if device["failure_type"] != "primary":
                device["failure_type"] = "sub-component"
            applet = ensure_applet(device, _service_app_code(msg))
            applet["status"] = "error" if _is_applet_event(event_code) else "impacted"
            applet["events"].append(_component_event(msg, "applet", context))
            return

        device["events"].append(_component_event(msg, "device", context))

    for msg in events_res.messages:
        attach_event_to_hybrid_tree(msg)

    for msg in logs_messages:
        if _event_detail(msg):
            attach_event_to_hybrid_tree(msg)

    def finalize_applet(applet: Dict[str, Any]) -> Dict[str, Any]:
        return applet

    def finalize_sensor(sensor: Dict[str, Any]) -> Dict[str, Any]:
        sensor["applets"] = [
            finalize_applet(item) for item in sensor.get("applets", {}).values()
        ]
        return sensor

    def finalize_device(device: Dict[str, Any]) -> Dict[str, Any]:
        device["sensors"] = [
            finalize_sensor(item) for item in device.get("sensors", {}).values()
        ]
        device["applets"] = [
            finalize_applet(item) for item in device.get("applets", {}).values()
        ]
        return device

    def finalize_standalone(component: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(component.get("applets"), dict):
            component["applets"] = [
                finalize_applet(item) for item in component["applets"].values()
            ]
        if isinstance(component.get("sensors"), dict):
            component["sensors"] = [
                finalize_sensor(item) for item in component["sensors"].values()
            ]
        return component

    response_networks: Dict[str, Any] = {}
    for net_id, data in networks.items():
        field_devices = [
            finalize_device(item) for item in data["field_devices"].values()
        ]
        standalone_components = [
            finalize_standalone(item)
            for item in data["standalone_components"].values()
        ]
        standalone_sensors = [
            item for item in standalone_components if item.get("type") == "sensor"
        ] + [
            sensor
            for item in standalone_components
            if item.get("type") == "applet"
            for sensor in item.get("sensors", [])
        ]

        response_networks[net_id] = {
            "network_id": data.get("network_id"),
            "network_code": data.get("network_code"),
            "network_name": data.get("network_name"),
            "field_devices": field_devices,
            "standalone_components": standalone_components,
            "logs": data.get("logs", []),
            "devices": field_devices,
            "standalone_sensors": standalone_sensors,
        }

    if unknowns:
        response_networks.setdefault(
            "unknown",
            {
                "network_id": "unknown",
                "network_code": "unknown",
                "network_name": "unknown",
                "field_devices": [],
                "standalone_components": [],
                "logs": [],
                "devices": [],
                "standalone_sensors": [],
            },
        )
        for key, info in unknowns.items():
            existing = any(
                item.get("id") == key
                for item in response_networks["unknown"]["field_devices"]
            )
            if existing:
                continue
            unknown_device = {
                "id": key,
                "device_id": key,
                "serial_identifier": None,
                "name": key,
                "status": "unknown",
                "failure_type": None,
                "sensors": [],
                "applets": [],
                "events": [],
                "id_debug": info.get("id_debug"),
            }
            response_networks["unknown"]["field_devices"].append(unknown_device)
            response_networks["unknown"]["devices"].append(unknown_device)

    return {
        "status": "ok",
        "networks": response_networks,
        "raw_data": {"events": events_res.messages, "logs": logs_messages},
        "ai_flags": ai_flags,
    }
