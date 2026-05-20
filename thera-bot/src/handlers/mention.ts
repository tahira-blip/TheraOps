import { App } from '@slack/bolt';
import { summarizeChannelHistory, summarizeThread } from '../features/summarize';
import { askThera } from '../lib/llm';
import { compactNetworksForLlm, deterministicNetworkReport } from '../lib/networkSummary';
import { fetchNetworkSummary } from '../lib/theraopsBackend';

function logRequest(entry: {
  userId: string;
  cmd: string;
  channel?: string;
  latency_ms: number;
  status: 'ok' | 'error';
  error?: string;
}) {
  const line = JSON.stringify({ ts: new Date().toISOString(), ...entry });
  console.log(line);
}

function isNetworkOpsQuery(message: string): boolean {
  return [
    /\bour networks?\b/i,
    /\bnetworks?\b/i,
    /\bnetwork (health|status|issues?|errors?|summary)\b/i,
    /\bshow.*networks?\b/i,
    /\bnetwork[_\s-]?id\b/i,
    /\b(field[_\s-]?devices?|standalone[_\s-]?components?)\b/i,
    /\b(devices?|sensors?).*\b(service\s+)?applets?\b/i,
    /\b(service\s+)?applets?.*\b(devices?|sensors?)\b/i,
    /\bhybrid hierarchy\b/i,
    /\boffline\b/i,
    /\bstatus\b/i,
    /\bis it down\b/i,
  ].some((pattern) => pattern.test(message));
}

async function answerNetworkOpsQuery(client: any, channelId: string, threadTs: string | undefined, say: any) {
  await say({ text: 'Translating that to a Graylog network query...', thread_ts: threadTs });

  let payload: any;
  try {
    payload = await fetchNetworkSummary('graylog');
  } catch (err) {
    console.error('[MENTION NETWORK FETCH ERROR]', err);
    await say({ text: 'Failed to fetch network status from backend.', thread_ts: threadTs });
    return;
  }
  if (payload.status === 'error') {
    await say({ text: payload.message || 'Graylog query failed.', thread_ts: threadTs });
    return;
  }

  const networks = payload.networks || {};
  if (Object.keys(networks).length === 0) {
    await say({
      text: 'No matching Graylog network issue rows were returned for the current network-status query window.',
      thread_ts: threadTs,
    });
    return;
  }

  const compactNetworks = compactNetworksForLlm(networks);
  const prompt = `Summarize this Graylog network issue payload using the hybrid hierarchy. Root level is network_id. Field Devices are hardware-bound hosts with sensors and applets nested under the device; applets may be direct device children or nested under sensors. Standalone Components are edgeless applets or sensors directly under the network without a parent device. Cite event_code and device_message from the affected child component and do not hallucinate any data:\n${JSON.stringify(compactNetworks)}`;
  let aiSummary: string;
  try {
    aiSummary = await askThera({ userMessage: prompt, scaffoldMode: false, opsMode: false });
  } catch (llmErr) {
    console.error('[MENTION NETWORK LLM ERROR]', llmErr);
    aiSummary = deterministicNetworkReport(networks);
  }
  const msg = await client.chat.postMessage({
    channel: channelId,
    thread_ts: threadTs,
    text: aiSummary,
  });

  await client.files.uploadV2({
    channel_id: channelId,
    thread_ts: msg.ts!,
    content: JSON.stringify(payload.raw_data ?? payload, null, 2),
    filename: 'graylog_raw_data.txt',
    title: 'Raw Graylog Data',
  });
}

export function registerMentionHandler(app: App) {
  app.event('app_mention', async ({ event, client, say }) => {
    console.log('[MENTION EVENT RECEIVED]', JSON.stringify(event, null, 2));

    const startTime = Date.now();
    const ev = event as any;

    try {
      const text: string = (ev.text || '').toLowerCase();
      const userId: string = ev.user || 'unknown';
      const channelId: string = ev.channel || '';

      console.log(`[MENTION DEBUG] user=${userId}, channel=${channelId}, text="${text}", thread_ts=${ev.thread_ts || 'none'}`);

      if (isNetworkOpsQuery(text)) {
        await answerNetworkOpsQuery(client, channelId, ev.thread_ts, say);
        logRequest({ userId, cmd: 'mention_network_query', channel: channelId, latency_ms: Date.now() - startTime, status: 'ok' });
        return;
      }

      if (!text.includes('summarize') && !text.includes('summarise') && !text.includes('summary')) {
        console.log('[MENTION] No Graylog or summarize intent detected, ignoring.');
        logRequest({ userId, cmd: 'mention_noop', channel: channelId, latency_ms: Date.now() - startTime, status: 'ok' });
        return;
      }

      if (ev.thread_ts) {
        console.log(`[MENTION] Summarizing thread: channel=${channelId}, thread_ts=${ev.thread_ts}`);
        await say({ text: 'Reading thread...', thread_ts: ev.thread_ts });

        const summary = await summarizeThread(client, channelId, ev.thread_ts);

        await say({ text: summary, thread_ts: ev.thread_ts });
        logRequest({ userId, cmd: 'mention_summarize_thread', channel: channelId, latency_ms: Date.now() - startTime, status: 'ok' });
        return;
      }

      console.log(`[MENTION] Fetching channel history: channel=${channelId}`);
      await say({ text: 'Reading last 40 messages...' });

      const summary = await summarizeChannelHistory(client, channelId);
      await say({ text: summary });
      logRequest({ userId, cmd: 'mention_summarize_history', channel: channelId, latency_ms: Date.now() - startTime, status: 'ok' });
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      console.error('[MENTION ERROR]', error);

      try {
        await say({ text: 'Something broke while handling that request. Try again?' });
      } catch (sayErr) {
        console.error('[MENTION SAY ERROR]', sayErr);
      }

      logRequest({
        userId: (ev.user || 'unknown'),
        cmd: 'mention',
        channel: (ev.channel || ''),
        latency_ms: Date.now() - startTime,
        status: 'error',
        error: errMsg,
      });
    }
  });
}
