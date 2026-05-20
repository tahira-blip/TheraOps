import { App } from '@slack/bolt';
import { summarizeChannelHistory, summarizeThread } from '../features/summarize';
//import { extractActions } from '../features/actions';
import { decisionSupport } from '../features/decide';
import {
  fetchDeviceDiagnostics,
  fetchLogDiagnosis,
  storeResolvedIncident,
  fetchNetworkSummary,
} from '../lib/theraopsBackend';
import { parseLogsCommandText } from '../lib/logCommandParser';
import { logsDiagnosisBlocks } from '../lib/logMessage';
import { askThera } from '../lib/llm';
import { compactNetworksForLlm, deterministicNetworkReport } from '../lib/networkSummary';

type SlackBlock = Record<string, any>;

function truncateSlackText(text: string, maxLength = 2800): string {
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
}

function networkFallbackText(networks: Record<string, any>): string {
  return Object.entries(networks)
    .map(([networkCode, network]: [string, any]) => {
      const devices = Array.isArray(network.devices) ? network.devices.length : 0;
      const standalone = Array.isArray(network.standalone_sensors) ? network.standalone_sensors.length : 0;
      return `Network ${networkCode}: ${devices} device issue(s), ${standalone} standalone sensor issue(s)`;
    })
    .join('\n') || 'No network issues found.';
}

function formatDevice(device: any): string {
  const status = device.status ? `_${device.status}_` : '_unknown_';
  const label = device.label || device.failure_type || 'Device issue';
  const lines = [`- *${device.name || device.id || 'Unknown device'}* - ${label} (${status})`];

  for (const applet of device.applets || []) {
    lines.push(`  - Applet \`${applet.app_code || 'unknown'}\` - ${applet.label || applet.status || 'issue'}`);
  }
  for (const sensor of device.sensors || []) {
    lines.push(`  - Sensor \`${sensor.id || 'unknown'}\` - ${sensor.label || sensor.status || 'issue'}`);
  }
  return lines.join('\n');
}

function formatStandaloneSensor(sensor: any): string {
  const lines = [`- *Sensor ${sensor.id || 'unknown'}* - Standalone Sensor issue (no field device)`];
  for (const applet of sensor.applets || []) {
    lines.push(`  - Applet \`${applet.app_code || 'unknown'}\` - ${applet.label || applet.status || 'impacted'}`);
  }
  return lines.join('\n');
}

function networkSummaryBlocks(networks: Record<string, any>, aiSummary: string): SlackBlock[] {
  const blocks: SlackBlock[] = [];

  for (const [networkCode, network] of Object.entries(networks).slice(0, 5)) {
    const devices = Array.isArray((network as any).devices) ? (network as any).devices : [];
    const standaloneSensors = Array.isArray((network as any).standalone_sensors) ? (network as any).standalone_sensors : [];

    blocks.push({
      type: 'header',
      text: { type: 'plain_text', text: `🚨 Network ${networkCode}`, emoji: true },
    });

    if (devices.length > 0) {
      blocks.push({
        type: 'section',
        text: { type: 'mrkdwn', text: '*Devices*' },
      });
      for (const device of devices.slice(0, 8)) {
        blocks.push({
          type: 'section',
          text: { type: 'mrkdwn', text: truncateSlackText(formatDevice(device)) },
        });
      }
    }

    if (standaloneSensors.length > 0) {
      blocks.push({
        type: 'section',
        text: { type: 'mrkdwn', text: '*📡 Standalone Sensors (No Field Device)*' },
      });
      for (const sensor of standaloneSensors.slice(0, 8)) {
        blocks.push({
          type: 'section',
          text: { type: 'mrkdwn', text: truncateSlackText(formatStandaloneSensor(sensor)) },
        });
      }
    }

    blocks.push({ type: 'divider' });
  }

  blocks.push({
    type: 'section',
    text: { type: 'mrkdwn', text: truncateSlackText(`*T-hera summary*\n${aiSummary}`) },
  });

  return blocks.length > 50 ? [...blocks.slice(0, 49), blocks[blocks.length - 1]] : blocks;
}

function getHybridFieldDevices(network: any): any[] {
  return Array.isArray(network.field_devices)
    ? network.field_devices
    : (Array.isArray(network.devices) ? network.devices : []);
}

function getHybridStandaloneComponents(network: any): any[] {
  return Array.isArray(network.standalone_components)
    ? network.standalone_components
    : (Array.isArray(network.standalone_sensors)
      ? network.standalone_sensors.map((sensor: any) => ({ ...sensor, type: 'sensor' }))
      : []);
}

function hybridNetworkFallbackText(networks: Record<string, any>): string {
  return Object.entries(networks)
    .map(([networkId, network]: [string, any]) => {
      const devices = getHybridFieldDevices(network).length;
      const standalone = getHybridStandaloneComponents(network).length;
      return `Network ${network.network_name || network.network_code || networkId}: ${devices} field device issue(s), ${standalone} standalone component issue(s)`;
    })
    .join('\n') || 'No network issues found.';
}

function hybridEventSummary(component: any): string {
  const event = Array.isArray(component?.events) && component.events.length > 0 ? component.events[0] : null;
  const name = event?.event_name || component?.label || component?.failure_type || component?.status || 'Unknown Event';
  return event?.event_code ? `${name} (${event.event_code})` : name;
}

function hybridMessageSnippet(component: any): string | undefined {
  const event = Array.isArray(component?.events) && component.events.length > 0 ? component.events[0] : null;
  const message = event?.device_message || component?.detail;
  return typeof message === 'string' && message.trim() ? truncateSlackText(message.trim(), 220) : undefined;
}

function hybridFormatDevice(device: any): string {
  const status = device.status ? `_${device.status}_` : '_unknown_';
  const identity = device.serial_identifier || device.device_id || device.id || 'unknown';
  const lines = [`*Device:* ${device.name || identity} / \`${identity}\` (${status})`];

  const deviceMessage = hybridMessageSnippet(device);
  if (deviceMessage) lines.push(`  "${deviceMessage}"`);

  for (const sensor of device.sensors || []) {
    lines.push(`  └─ 🛰️ Sensor: \`${sensor.id || 'unknown'}\` -> ${hybridEventSummary(sensor)}`);
    const sensorMessage = hybridMessageSnippet(sensor);
    if (sensorMessage) lines.push(`     "${sensorMessage}"`);

    for (const applet of sensor.applets || []) {
      lines.push(`     └─ 🧩 Applet: \`${applet.app_code || applet.name || 'unknown'}\` -> ${hybridEventSummary(applet)}`);
      const appletMessage = hybridMessageSnippet(applet);
      if (appletMessage) lines.push(`        "${appletMessage}"`);
    }
  }

  for (const applet of device.applets || []) {
    lines.push(`  └─ 🧩 Applet: \`${applet.app_code || applet.name || 'unknown'}\` -> ${hybridEventSummary(applet)}`);
    const appletMessage = hybridMessageSnippet(applet);
    if (appletMessage) lines.push(`     "${appletMessage}"`);
  }

  return lines.join('\n');
}

function hybridFormatStandaloneComponent(component: any): string {
  const type = component.type || (component.app_code ? 'applet' : 'sensor');
  if (type === 'applet') {
    const lines = [`☁️ *Edgeless Service:* \`${component.app_code || component.name || 'unknown'}\` -> ${hybridEventSummary(component)}`];
    const appletMessage = hybridMessageSnippet(component);
    if (appletMessage) lines.push(`  "${appletMessage}"`);
    for (const sensor of component.sensors || []) {
      lines.push(`  └─ 🛰️ Sensor: \`${sensor.id || 'unknown'}\` -> ${hybridEventSummary(sensor)}`);
      const sensorMessage = hybridMessageSnippet(sensor);
      if (sensorMessage) lines.push(`     "${sensorMessage}"`);
    }
    return lines.join('\n');
  }

  const lines = [`🛰️ *Sensor:* \`${component.id || 'unknown'}\` -> ${hybridEventSummary(component)}`];
  const sensorMessage = hybridMessageSnippet(component);
  if (sensorMessage) lines.push(`  "${sensorMessage}"`);
  for (const applet of component.applets || []) {
    lines.push(`  └─ 🧩 Applet: \`${applet.app_code || applet.name || 'unknown'}\` -> ${hybridEventSummary(applet)}`);
  }
  return lines.join('\n');
}

function hybridNetworkStatus(network: any): string {
  const all = [...getHybridFieldDevices(network), ...getHybridStandaloneComponents(network)];
  if (all.some((item) => item.status === 'offline' || item.failure_type === 'primary')) return '🔴 Critical';
  if (all.length > 0) return '⚠️ Warning';
  return '🟢 Nominal';
}

function hybridNetworkSummaryBlocks(networks: Record<string, any>, aiSummary: string): SlackBlock[] {
  const blocks: SlackBlock[] = [];

  for (const [networkId, network] of Object.entries(networks).slice(0, 5)) {
    const devices = getHybridFieldDevices(network);
    const standaloneComponents = getHybridStandaloneComponents(network);
    const networkName = (network as any).network_name || (network as any).network_code || networkId;

    blocks.push({
      type: 'section',
      text: { type: 'mrkdwn', text: `*📍 Network:* ${networkName} / \`${(network as any).network_id || networkId}\`\n*Status:* ${hybridNetworkStatus(network)}` },
    });

    if (devices.length > 0) {
      blocks.push({
        type: 'section',
        text: { type: 'mrkdwn', text: '*Section 1: Field Devices (Hardware-Bound)*' },
      });
      for (const device of devices.slice(0, 8)) {
        blocks.push({
          type: 'section',
          text: { type: 'mrkdwn', text: truncateSlackText(hybridFormatDevice(device)) },
        });
      }
    }

    if (standaloneComponents.length > 0) {
      blocks.push({
        type: 'section',
        text: { type: 'mrkdwn', text: '*Section 2: Standalone Components (Edgeless)*' },
      });
      for (const component of standaloneComponents.slice(0, 8)) {
        blocks.push({
          type: 'section',
          text: { type: 'mrkdwn', text: truncateSlackText(hybridFormatStandaloneComponent(component)) },
        });
      }
    }

    blocks.push({ type: 'divider' });
  }

  blocks.push({
    type: 'section',
    text: { type: 'mrkdwn', text: truncateSlackText(`*Section 3: AI Diagnostic*\n${aiSummary}`) },
  });

  return blocks.length > 50 ? [...blocks.slice(0, 49), blocks[blocks.length - 1]] : blocks;
}

function logRequest(entry: {
  userId: string;
  cmd: string;
  service?: string;
  latency_ms: number;
  status: 'ok' | 'error';
  error?: string;
}) {
  const line = JSON.stringify({ ts: new Date().toISOString(), ...entry });
  console.log(line);
}

async function fetchAndFormatNetworkSummary(client: any, respond: any, channelId: string) {
    try {
    let payload: any;
    try {
      payload = await fetchNetworkSummary('graylog');
    } catch (fetchErr) {
      console.error('[NETWORK SUMMARY FETCH ERROR]', fetchErr);
      await respond({ text: 'Failed to fetch network status from backend.', response_type: 'ephemeral' });
      return;
    }
    if (payload.status === 'error') {
      await respond({ text: payload.message || 'Graylog query failed.', response_type: 'in_channel' });
      return;
    }
    const networks = payload.networks || {};
    const entries = Object.entries(networks);

    if (entries.length === 0) {
      await respond({ text: '*Status*: ❓ Unknown\n*Summary*: Insufficient data to determine system health.\n*Root Cause Hypothesis*: The network issue payload contained no current issue entries, and no heartbeat or success logs were provided to prove health.\n*Recommended Action*: Rerun with raw Graylog logs or a heartbeat-backed health source before declaring the system healthy.', response_type: 'in_channel' });
      return;
    }

    const compactNetworks = compactNetworksForLlm(networks);
    const prompt = `Summarize this network issue payload concisely using the hybrid hierarchy. Root level is network_id. Field Devices are hardware-bound hosts with sensors and applets nested under the device; applets may be direct device children or nested under sensors. Standalone Components are edgeless applets or sensors directly under the network without a parent device. Cite event_code and device_message from the affected child component and do not hallucinate any data:\n${JSON.stringify(compactNetworks)}`;
    let aiSummary: string;
    try {
      aiSummary = await askThera({ userMessage: prompt, scaffoldMode: false, opsMode: false });
    } catch (llmErr) {
      console.error('[COMMAND NETWORK LLM ERROR]', llmErr);
      aiSummary = deterministicNetworkReport(networks);
    }

    const fallbackText = `${hybridNetworkFallbackText(networks)}\n\n${aiSummary}`;
    const msg = await client.chat.postMessage({
      channel: channelId,
      text: fallbackText,
      blocks: hybridNetworkSummaryBlocks(networks, aiSummary),
    });

    await client.files.uploadV2({
      channel_id: channelId,
      thread_ts: msg.ts!,
      content: JSON.stringify(payload.raw_data ?? payload, null, 2),
      filename: 'graylog_raw_data.txt',
      title: 'Raw Graylog Data'
    });

  } catch (err) {
    console.error('[NETWORK SUMMARY ERROR]', err);
    await respond({ text: 'Error querying network summary.', response_type: 'ephemeral' });
  }
}

export function registerCommandHandler(app: App) {
  app.command('/thera', async ({ command, ack, respond, client }) => {
    await ack();  // Always within 3 seconds - non-negotiable

    const [sub, ...rest] = command.text.trim().split(' ');
    const args = rest.join(' ');
    const startTime = Date.now();
    const rawText = (command.text || '').toLowerCase();

    try {
      if (/\boffline\b/.test(rawText) || /\bstatus\b/.test(rawText) || /is it down/.test(rawText)) {
        await respond({ text: 'Checking network status (production-non-session-events)...', response_type: 'ephemeral' });
        await fetchAndFormatNetworkSummary(client, respond, command.channel_id);
        logRequest({ userId: command.user_id, cmd: 'network_quick_check', latency_ms: Date.now() - startTime, status: 'ok' });
        return;
      }

      switch (sub) {
        case 'summarize':
        case 'summarise':
        case 'summary':
          await respond({ text: 'Reading...', response_type: 'ephemeral' });
          const summary = command.thread_ts
            ? await summarizeThread(client, command.channel_id, command.thread_ts)
            : await summarizeChannelHistory(client, command.channel_id);
          await respond({ text: summary, response_type: 'in_channel' });
          logRequest({ userId: command.user_id, cmd: 'summarize', latency_ms: Date.now() - startTime, status: 'ok' });
          break;

        case 'think':
          if (!args)
            return respond('What do you want to think through? `/thera think [question]`');
          const thinking = await decisionSupport(args, command.user_id, { opsMode: true }, respond);
          if (thinking === null) {
            logRequest({ userId: command.user_id, cmd: 'think', latency_ms: Date.now() - startTime, status: 'error', error: 'All LLM options failed' });
            break;
          }
          await respond({ text: thinking, response_type: 'ephemeral' });
          logRequest({ userId: command.user_id, cmd: 'think', latency_ms: Date.now() - startTime, status: 'ok' });
          break;

        case 'logs': {
          const parsed = parseLogsCommandText(args);
          if (!parsed.ok) {
            logRequest({ userId: command.user_id, cmd: 'logs', latency_ms: Date.now() - startTime, status: 'error', error: parsed.message });
            return respond({
              text: parsed.message,
              response_type: 'ephemeral',
            });
          }

          const { service } = parsed;

          await respond({
            text: `Checking Graylog for service \`${service}\`...`,
            response_type: 'ephemeral',
          });
          const logRequestPayload = { service };
          const logDiagnosis = await fetchLogDiagnosis(logRequestPayload);
          if (command.thread_ts) {
            await client.chat.postMessage({
              channel: command.channel_id,
              thread_ts: command.thread_ts,
              text: logDiagnosis.reply,
              blocks: logsDiagnosisBlocks(logDiagnosis.reply, logRequestPayload),
            });
          } else {
            await client.chat.postMessage({
              channel: command.channel_id,
              text: logDiagnosis.reply,
              blocks: logsDiagnosisBlocks(logDiagnosis.reply, logRequestPayload),
            });
          }
          logRequest({ userId: command.user_id, cmd: 'logs', service, latency_ms: Date.now() - startTime, status: 'ok' });
          break;
        }



        case 'networks': {
          await respond({ text: 'Fetching network issues...', response_type: 'ephemeral' });
          await fetchAndFormatNetworkSummary(client, respond, command.channel_id);
          logRequest({ userId: command.user_id, cmd: 'networks', latency_ms: Date.now() - startTime, status: 'ok' });
          break;
        }

        case 'resolve': {
          const parts = args.split('|').map((part) => part.trim()).filter(Boolean);
          if (parts.length !== 3)
            return respond('Usage: `/thera resolve [service] | [root cause] | [fix]`');

          const [service, rootCause, fix] = parts;
          await storeResolvedIncident(service, rootCause, fix);
          await respond({
            text: `Stored incident memory for \`${service}\`.`,
            response_type: 'in_channel',
          });
          logRequest({ userId: command.user_id, cmd: 'resolve', service, latency_ms: Date.now() - startTime, status: 'ok' });
          break;
        }

        case 'diagnostics': {
          if (!args) {
            await respond({
              text: 'Usage: `/thera diagnostics [device]` (example: `/thera diagnostics device-1234`)',
              response_type: 'ephemeral',
            });
            logRequest({ userId: command.user_id, cmd: 'diagnostics', latency_ms: Date.now() - startTime, status: 'error', error: 'missing_device_arg' });
            return;
          }

          await respond({ text: 'Fetching diagnostics...', response_type: 'ephemeral' });

          const deviceId = args.trim();
          const diag = await fetchDeviceDiagnostics({ device_id: deviceId });

          const blocks: any[] = [];

          blocks.push({
            type: 'header',
            text: { type: 'plain_text', text: `🩺 Device Diagnostics`, emoji: true },
          });

          blocks.push({
            type: 'section',
            text: { type: 'mrkdwn', text: `*Device:* \`${diag.device_id}\`${diag.device_label ? `\n*Label:* ${diag.device_label}` : ''}` },
          });

          blocks.push({
            type: 'section',
            text: { type: 'mrkdwn', text: `*Status:* ${diag.device_status || '_unknown_'}${diag.last_seen ? `\n*Last seen:* ${diag.last_seen}` : ''}` },
          });

          const telemetryLines: string[] = [];
          if (diag.firmware_version) telemetryLines.push(`*Firmware:* ${diag.firmware_version}`);
          if (diag.connectivity_state) telemetryLines.push(`*Connectivity:* ${diag.connectivity_state}`);
          if (diag.telemetry_health) telemetryLines.push(`*Telemetry health:* ${diag.telemetry_health}`);
          if (diag.sensor_health) telemetryLines.push(`*Sensor health:* ${diag.sensor_health}`);
          if (diag.battery_power) telemetryLines.push(`*Power/Battery:* ${diag.battery_power}`);

          blocks.push({
            type: 'section',
            text: { type: 'mrkdwn', text: telemetryLines.length ? telemetryLines.join('\n') : '*Telemetry:* _not available_' },
          });

          const disconnectEvents = Array.isArray(diag.disconnect_reconnect_events) ? diag.disconnect_reconnect_events : [];
          blocks.push({ type: 'divider' });
          blocks.push({
            type: 'section',
            text: { type: 'mrkdwn', text: '*Recent disconnect/reconnect events*' },
          });

          if (disconnectEvents.length === 0) {
            blocks.push({
              type: 'section',
              text: { type: 'mrkdwn', text: '_No recent disconnect/reconnect events available (or telemetry source unreachable)._' },
            });
          } else {
            for (const e of disconnectEvents.slice(0, 6)) {
              const kind = e.kind ? String(e.kind) : 'event';
              const ts = e.timestamp ? String(e.timestamp) : 'unknown time';
              blocks.push({
                type: 'section',
                text: { type: 'mrkdwn', text: `• *${kind}* — ${ts}` },
              });
            }
          }

          const alerts = Array.isArray(diag.active_alerts) ? diag.active_alerts : [];
          blocks.push({ type: 'divider' });
          blocks.push({
            type: 'section',
            text: { type: 'mrkdwn', text: '*Related active alerts*' },
          });

          if (alerts.length === 0) {
            blocks.push({
              type: 'section',
              text: { type: 'mrkdwn', text: '_No active alerts available._' },
            });
          } else {
            for (const a of alerts.slice(0, 6)) {
              blocks.push({
                type: 'section',
                text: { type: 'mrkdwn', text: `• ${a.severity ? `_${a.severity}_ — ` : ''}*${a.title}*${a.detail ? `\n  _${a.detail}_` : ''}` },
              });
            }
          }

          blocks.push({ type: 'divider' });
          blocks.push({
            type: 'section',
            text: { type: 'mrkdwn', text: `*Likely issue cause:* ${diag.likely_issue_cause || '_unknown_'}` },
          });

          blocks.push({
            type: 'section',
            text: { type: 'mrkdwn', text: `*Recommended troubleshooting*:\n${(diag.recommended_troubleshooting || []).slice(0, 8).map((s) => `• ${s}`).join('\n') || '_not available_'}` },
          });

          if (diag.ai_summary) {
            blocks.push({
              type: 'section',
              text: { type: 'mrkdwn', text: `*AI-assisted summary*\n${truncateSlackText(diag.ai_summary, 2300)}` },
            });
          }

          const responseText =
            `Device Health: ${diag.device_status || 'Unknown'}\n` +
            `Device: ${diag.device_id}${diag.device_label ? ` (${diag.device_label})` : ''}\n` +
            `${diag.last_seen ? `Last seen: ${diag.last_seen}\n` : ''}` +
            `${diag.firmware_version ? `Firmware: ${diag.firmware_version}\n` : ''}` +
            `${diag.telemetry_health ? `Telemetry health: ${diag.telemetry_health}\n` : ''}` +
            `Likely issue cause: ${diag.likely_issue_cause || 'Unknown'}\n` +
            `Recommended action: ${(diag.recommended_troubleshooting || []).slice(0, 3).join(' | ') || 'N/A'}`;

          // post in-channel
          await client.chat.postMessage({
            channel: command.channel_id,
            text: responseText,
            blocks,
          });

          if (Array.isArray(diag.provider_errors) && diag.provider_errors.length > 0) {
            logRequest({
              userId: command.user_id,
              cmd: 'diagnostics',
              latency_ms: Date.now() - startTime,
              status: 'ok',
              error: diag.provider_errors.join('; ').slice(0, 500),
            });
          } else {
            logRequest({
              userId: command.user_id,
              cmd: 'diagnostics',
              latency_ms: Date.now() - startTime,
              status: 'ok',
            });
          }
          break;
        }

        default:
          await respond(
            '*T-hera.* What I do:\n' +
            '- `/thera summarize` or `/thera summarise` - thread or channel summary\n' +
            '- `/thera think [question]` - decision support\n' +
            '- `/thera logs [service]` - Graylog-backed incident diagnosis\n' +
            '- `/thera diagnostics [device]` - device diagnostics summary\n' +
            '- `/thera resolve service | root cause | fix` - store a resolved incident\n' +
            'Or DM me directly.'
          );
          logRequest({ userId: command.user_id, cmd: sub || 'help', latency_ms: Date.now() - startTime, status: 'ok' });
      }
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      logRequest({ userId: command.user_id, cmd: sub || 'unknown', latency_ms: Date.now() - startTime, status: 'error', error: errMsg });
      console.error('[CMD ERROR]', error);

      let userFacingMsg = 'Something broke on my end. Try again?';
      if (errMsg.includes('TheraOps backend request failed')) {
        try {
          const match = errMsg.match(/failed \(\d+\):\s*(.+)$/);
          if (match) {
            const parsed = JSON.parse(match[1]);
            if (parsed.detail) {
              userFacingMsg = `Backend Error: ${parsed.detail}`;
            }
          }
        } catch (_) { }
      }

      await respond(userFacingMsg);
    }
  });
}
