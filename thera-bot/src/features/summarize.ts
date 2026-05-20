import { WebClient } from '@slack/web-api';
import { askThera } from '../lib/llm';

const MAX_THREAD_MESSAGES = 40;
const MAX_THREAD_CHARS = 8000;

export async function summarizeThread(
  client: WebClient, channelId: string, threadTs: string
): Promise<string> {
  const result = await client.conversations.replies({
    channel: channelId, ts: threadTs, limit: 100
  });

  if (!result.messages?.length)
    return "Thread is empty — nothing to synthesize.";

  const messages = result.messages.slice(-MAX_THREAD_MESSAGES);
  let formatted = messages
    .map(m => `[${m.user ?? 'Unknown'}]: ${m.text ?? ''}`)
    .join('\n');

  const wasTruncated = result.messages.length > messages.length || formatted.length > MAX_THREAD_CHARS;
  if (formatted.length > MAX_THREAD_CHARS) {
    formatted = formatted.slice(-MAX_THREAD_CHARS);
  }

  return askThera({
    userMessage: `Summarize this Slack thread.\n\n${wasTruncated ? 'Note: the thread was truncated to keep the summary request small.\n\n' : ''}Thread:\n${formatted}`,
    taskContext: `Summarize in 3–5 sentences covering: key decisions made, blockers identified, and what remains unresolved. Always flag unowned action items. End with one short question that surfaces what the user might not have consciously noticed.`
  });
}

export async function summarizeChannelHistory(
  client: WebClient, channelId: string
): Promise<string> {
  const result = await client.conversations.history({
    channel: channelId,
    limit: MAX_THREAD_MESSAGES,
  });

  if (!result.messages?.length)
    return "Channel is empty - nothing to synthesize.";

  const messages = [...result.messages].reverse();
  let formatted = messages
    .map(m => `[${m.user ?? 'Unknown'}]: ${m.text ?? ''}`)
    .join('\n');

  const wasTruncated = formatted.length > MAX_THREAD_CHARS;
  if (wasTruncated) {
    formatted = formatted.slice(-MAX_THREAD_CHARS);
  }

  return askThera({
    userMessage: `Summarize this Slack channel history.\n\n${wasTruncated ? 'Note: the history was truncated to keep the summary request small.\n\n' : ''}Messages:\n${formatted}`,
    taskContext: `Summarize in 3-5 sentences covering: key decisions made, blockers identified, and what remains unresolved. Always flag unowned action items. End with one short question that surfaces what the user might not have consciously noticed.`
  });
}
