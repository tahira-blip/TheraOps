export interface ParsedLogsCommand {
  ok: true;
  service: string;
}

export interface MalformedLogsCommand {
  ok: false;
  message: string;
}

export type LogsCommandParseResult = ParsedLogsCommand | MalformedLogsCommand;

const USAGE = 'Correct syntax: `/thera logs [service]`';

function reliabilityMessage(reason: string): string {
  return `Reliability: I could not parse that logs request. ${reason}\n${USAGE}`;
}

export function parseLogsCommandText(text: string): LogsCommandParseResult {
  const raw = text.trim();

  if (!raw) {
    return {
      ok: false,
      message: reliabilityMessage('Please provide a service name.'),
    };
  }

  const parts = raw.split(/\s+/).filter(Boolean);

  if (parts.length > 1) {
    return {
      ok: false,
      message: reliabilityMessage('Extra arguments found. The command now only accepts a single service name.'),
    };
  }

  const service = parts[0];
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(service)) {
    return {
      ok: false,
      message: reliabilityMessage('The service name should contain only letters, numbers, dots, underscores, hyphens, or colons.'),
    };
  }

  return {
    ok: true,
    service,
  };
}
