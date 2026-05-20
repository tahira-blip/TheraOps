const THERAOPS_BACKEND_URL = (process.env.THERAOPS_BACKEND_URL ?? 'http://127.0.0.1:8000')
  .replace(/\/+$/, '');
const INTERNAL_API_TOKEN = process.env.THERAOPS_INTERNAL_API_TOKEN?.trim() ?? '';

export interface LogsDiagnosisResponse {
  service: string;
  device_service_id?: string;
  primary_stream?: string;
  error_count: number;
  sample_messages: string[];
  reply: string;
}

export interface LogsDiagnosisRequest {
  service: string;
  window_seconds?: number;
}

export interface DiagnosticsResponse {
  device_id: string;
  device_label?: string | null;
  device_status: string;

  last_seen?: string | null;
  firmware_version?: string | null;
  connectivity_state?: string | null;

  telemetry_health?: string | null;
  sensor_health?: string | null;
  battery_power?: string | null;

  disconnect_reconnect_events: Array<{
    kind?: string | null;
    timestamp?: string | null;
    device_id?: string | null;
  }>;

  active_alerts: Array<{
    severity?: string | null;
    title: string;
    detail?: string | null;
  }>;

  likely_issue_cause: string;
  recommended_troubleshooting: string[];
  ai_summary?: string | null;

  provider_errors?: string[];
}

export interface DiagnosticsRequest {
  device_id: string;
  window_seconds?: number;
}

interface StoredIncidentResponse {
  status: string;
  incident: {
    service: string;
    root_cause: string;
    fix: string;
    created_at: string;
  };
}

export interface SlackThreadMessage {
  user?: string;
  text: string;
  ts?: string;
}

export interface ChatResponse {
  action: string;
  reply: string;
  service?: string;
  device_service_id?: string;
  env?: string;
  window_seconds?: number;
}

export interface ReceptionistChatRequest {
  user_message: string;
  service_alias?: string;
  thread_history: Array<{
    role: 'user' | 'assistant';
    content: string;
  }>;
}

async function postJson<T>(path: string, body: object): Promise<T> {
  const response = await fetch(`${THERAOPS_BACKEND_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(INTERNAL_API_TOKEN ? { 'X-Internal-API-Token': INTERNAL_API_TOKEN } : {}),
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30000),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`TheraOps backend request failed (${response.status}): ${errorText}`);
  }

  return response.json() as Promise<T>;
}

export async function fetchLogDiagnosis(request: LogsDiagnosisRequest): Promise<LogsDiagnosisResponse> {
  return postJson<LogsDiagnosisResponse>('/slack/logs', request);
}

export async function fetchDeviceDiagnostics(request: DiagnosticsRequest): Promise<DiagnosticsResponse> {
  return postJson<DiagnosticsResponse>('/slack/diagnostics', request);
}

export async function continueSlackThread(request: {
  channel_id: string;
  thread_ts: string;
  user_id: string;
  messages: SlackThreadMessage[];
}): Promise<ChatResponse> {
  return postJson<ChatResponse>('/slack/chat', request);
}

export async function sendReceptionistTechnicalChat(
  request: ReceptionistChatRequest,
): Promise<ChatResponse> {
  return postJson<ChatResponse>('/slack/chat', request);
}

export async function storeResolvedIncident(
  service: string,
  rootCause: string,
  fix: string,
): Promise<StoredIncidentResponse> {
  return postJson<StoredIncidentResponse>('/incidents/resolve', {
    service,
    root_cause: rootCause,
    fix,
  });
}

export async function fetchNetworkSummary(source = 'graylog'): Promise<any> {
  const url = `${THERAOPS_BACKEND_URL}/slack/api/issues/network-summary?source=${encodeURIComponent(
    source,
  )}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      ...(INTERNAL_API_TOKEN ? { 'X-Internal-API-Token': INTERNAL_API_TOKEN } : {}),
    },
    signal: AbortSignal.timeout(30000),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`TheraOps backend request failed (${response.status}): ${errorText}`);
  }

  return response.json();
}
