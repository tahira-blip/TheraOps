const MAX_NETWORKS_FOR_LLM = 8;
const MAX_CHILDREN_FOR_LLM = 8;
const MAX_MESSAGE_CHARS = 220;

function firstEvent(item: any): any {
  return Array.isArray(item?.events) && item.events.length > 0 ? item.events[0] : null;
}

function clip(value: unknown, maxLength = MAX_MESSAGE_CHARS): string | undefined {
  if (typeof value !== 'string') return undefined;
  const text = value.trim();
  if (!text) return undefined;
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
}

function componentEvidence(item: any): Record<string, unknown> {
  const event = firstEvent(item);
  return {
    id: item?.id,
    app_code: item?.app_code,
    name: item?.name,
    type: item?.type,
    status: item?.status,
    failure_type: item?.failure_type,
    event_code: event?.event_code ?? item?.event_code,
    event_name: event?.event_name ?? item?.label,
    device_message: clip(event?.device_message ?? item?.detail),
    timestamp: event?.timestamp ?? item?.timestamp,
  };
}

function compactDevice(device: any): Record<string, unknown> {
  return {
    ...componentEvidence(device),
    device_id: device?.device_id ?? device?.id,
    serial_identifier: device?.serial_identifier,
    sensors: (device?.sensors ?? []).slice(0, MAX_CHILDREN_FOR_LLM).map((sensor: any) => ({
      ...componentEvidence(sensor),
      applets: (sensor?.applets ?? []).slice(0, MAX_CHILDREN_FOR_LLM).map(componentEvidence),
    })),
    applets: (device?.applets ?? []).slice(0, MAX_CHILDREN_FOR_LLM).map(componentEvidence),
  };
}

function compactStandalone(component: any): Record<string, unknown> {
  return {
    ...componentEvidence(component),
    sensors: (component?.sensors ?? []).slice(0, MAX_CHILDREN_FOR_LLM).map(componentEvidence),
    applets: (component?.applets ?? []).slice(0, MAX_CHILDREN_FOR_LLM).map(componentEvidence),
  };
}

export function compactNetworksForLlm(networks: Record<string, any>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(networks).slice(0, MAX_NETWORKS_FOR_LLM).map(([networkId, network]) => {
      const fieldDevices = Array.isArray(network.field_devices)
        ? network.field_devices
        : (Array.isArray(network.devices) ? network.devices : []);
      const standaloneComponents = Array.isArray(network.standalone_components)
        ? network.standalone_components
        : (Array.isArray(network.standalone_sensors) ? network.standalone_sensors : []);

      return [
        networkId,
        {
          network_id: network.network_id ?? networkId,
          network_code: network.network_code,
          network_name: network.network_name,
          field_device_count: fieldDevices.length,
          standalone_component_count: standaloneComponents.length,
          field_devices: fieldDevices.slice(0, MAX_CHILDREN_FOR_LLM).map(compactDevice),
          standalone_components: standaloneComponents.slice(0, MAX_CHILDREN_FOR_LLM).map(compactStandalone),
        },
      ];
    }),
  );
}

export function deterministicNetworkReport(networks: Record<string, any>): string {
  const lines = ['*Graylog network findings*'];
  const entries = Object.entries(networks).slice(0, MAX_NETWORKS_FOR_LLM);

  if (entries.length === 0) {
    return '*Status*: Unknown\n*Summary*: No matching Graylog network issue rows were returned.';
  }

  for (const [networkId, network] of entries) {
    const fieldDevices = Array.isArray(network.field_devices)
      ? network.field_devices
      : (Array.isArray(network.devices) ? network.devices : []);
    const standaloneComponents = Array.isArray(network.standalone_components)
      ? network.standalone_components
      : (Array.isArray(network.standalone_sensors) ? network.standalone_sensors : []);

    lines.push(`\n*Network:* ${network.network_name || network.network_code || networkId} / \`${network.network_id || networkId}\``);
    lines.push(`Field devices: ${fieldDevices.length}; standalone components: ${standaloneComponents.length}`);

    for (const device of fieldDevices.slice(0, 5)) {
      const ev = firstEvent(device);
      lines.push(`- Device \`${device.serial_identifier || device.device_id || device.id || 'unknown'}\`: ${ev?.event_name || device.status || 'issue'}${ev?.event_code ? ` (${ev.event_code})` : ''}`);
      for (const sensor of (device.sensors ?? []).slice(0, 3)) {
        const sensorEvent = firstEvent(sensor);
        lines.push(`  - Sensor \`${sensor.id || 'unknown'}\`: ${sensorEvent?.event_name || sensor.status || 'issue'}${sensorEvent?.event_code ? ` (${sensorEvent.event_code})` : ''}`);
      }
      for (const applet of (device.applets ?? []).slice(0, 3)) {
        const appletEvent = firstEvent(applet);
        lines.push(`  - Applet \`${applet.app_code || applet.name || 'unknown'}\`: ${appletEvent?.event_name || applet.status || 'issue'}${appletEvent?.event_code ? ` (${appletEvent.event_code})` : ''}`);
      }
    }

    for (const component of standaloneComponents.slice(0, 5)) {
      const ev = firstEvent(component);
      const label = component.app_code || component.name || component.id || 'unknown';
      lines.push(`- Standalone ${component.type || 'component'} \`${label}\`: ${ev?.event_name || component.status || 'issue'}${ev?.event_code ? ` (${ev.event_code})` : ''}`);
    }
  }

  lines.push('\nLLM diagnostic was unavailable, so this is a deterministic summary from the Graylog hierarchy payload.');
  return lines.join('\n');
}
