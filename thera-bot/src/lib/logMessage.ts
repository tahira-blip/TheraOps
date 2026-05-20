import { LogsDiagnosisRequest } from './theraopsBackend';

export const LOG_TIME_RANGE_ACTION = 'thera_logs_time_range';

const TIME_RANGE_OPTIONS = [
  { label: '2 minutes', seconds: 120 },
  { label: '5 minutes', seconds: 300 },
  { label: '15 minutes', seconds: 900 },
  { label: '30 minutes', seconds: 1800 },
  { label: '1 hour', seconds: 3600 },
  { label: '2 hours', seconds: 7200 },
  { label: '8 hours', seconds: 28800 },
  { label: '1 day', seconds: 86400 },
  { label: '2 days', seconds: 172800 },
  { label: '5 days', seconds: 432000 },
  { label: '7 days', seconds: 604800 },
  { label: '14 days', seconds: 1209600 },
  { label: '30 days', seconds: 2592000 },
];

export function encodeLogActionContext(request: LogsDiagnosisRequest, windowSeconds?: number): string {
  return JSON.stringify({
    service: request.service,
    window_seconds: windowSeconds,
  });
}

export function decodeLogActionContext(value: string): LogsDiagnosisRequest {
  const parsed = JSON.parse(value) as LogsDiagnosisRequest;
  return {
    service: parsed.service,
    window_seconds: parsed.window_seconds,
  };
}

export function chunkSlackText(text: string, maxLen = 2800): string[] {
  const lines = text.split('\\n');
  const chunks: string[] = [];
  let current = '';

  for (const line of lines) {
    if ((current + line + '\\n').length > maxLen && current) {
      chunks.push(current.trim());
      current = line + '\\n';
    } else {
      current += line + '\\n';
    }
  }
  if (current.trim()) {
    chunks.push(current.trim());
  }
  return chunks;
}

export function logsDiagnosisBlocks(text: string, request: LogsDiagnosisRequest): any[] {
  const chunks = chunkSlackText(text);
  const blocks: any[] = chunks.map(chunk => ({
    type: 'section',
    text: {
      type: 'mrkdwn',
      text: chunk,
    },
  }));

  blocks.push({
    type: 'actions',
    elements: [
      {
        type: 'static_select',
        action_id: LOG_TIME_RANGE_ACTION,
        placeholder: {
          type: 'plain_text',
          text: 'Select time range',
        },
        options: TIME_RANGE_OPTIONS.map((option) => ({
          text: {
            type: 'plain_text',
            text: option.label,
          },
          value: encodeLogActionContext(request, option.seconds),
        })),
      },
    ],
  });
  return blocks;
}

