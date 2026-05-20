import { App } from '@slack/bolt';
import { continueSlackThread, SlackThreadMessage, fetchNetworkSummary } from '../lib/theraopsBackend';
import { askThera } from '../lib/llm';
import { compactNetworksForLlm, deterministicNetworkReport } from '../lib/networkSummary';

const processedReplyTs = new Set<string>();

function rememberReply(ts: string) {
  processedReplyTs.add(ts);
  setTimeout(() => processedReplyTs.delete(ts), 5 * 60 * 1000).unref();
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

async function postNetworkOpsSummary(client: any, channelId: string, threadTs: string) {
  await client.chat.postMessage({
    channel: channelId,
    thread_ts: threadTs,
    text: 'Translating that to a Graylog network query...',
  });

  let payload: any;
  try {
    payload = await fetchNetworkSummary('graylog');
  } catch (err) {
    console.error('[THREAD NETWORK FETCH ERROR]', err);
    await client.chat.postMessage({
      channel: channelId,
      thread_ts: threadTs,
      text: 'Failed to fetch network status from backend.',
    });
    return;
  }
  if (payload.status === 'error') {
    await client.chat.postMessage({
      channel: channelId,
      thread_ts: threadTs,
      text: payload.message || 'Graylog query failed.',
    });
    return;
  }

  const networks = payload.networks || {};
  if (Object.keys(networks).length === 0) {
    await client.chat.postMessage({
      channel: channelId,
      thread_ts: threadTs,
      text: 'No matching Graylog network issue rows were returned for the current network-status query window.',
    });
    return;
  }

  const compactNetworks = compactNetworksForLlm(networks);
  const prompt = `Summarize this Graylog network issue payload using the hybrid hierarchy. Root level is network_id. Field Devices are hardware-bound hosts with sensors and applets nested under the device; applets may be direct device children or nested under sensors. Standalone Components are edgeless applets or sensors directly under the network without a parent device. Cite event_code and device_message from the affected child component and do not hallucinate any data:\n${JSON.stringify(compactNetworks)}`;
  let aiSummary: string;
  try {
    aiSummary = await askThera({ userMessage: prompt, scaffoldMode: false, opsMode: false });
  } catch (llmErr) {
    console.error('[THREAD NETWORK LLM ERROR]', llmErr);
    aiSummary = deterministicNetworkReport(networks);
  }
  const msg = await client.chat.postMessage({
    channel: channelId,
    text: aiSummary,
    thread_ts: threadTs,
  });

  await client.files.uploadV2({
    channel_id: channelId,
    thread_ts: msg.ts!,
    content: JSON.stringify(payload.raw_data ?? payload, null, 2),
    filename: 'graylog_raw_data.txt',
    title: 'Raw Graylog Data',
  });
}

export function registerThreadListener(app: App) {
  console.log('[THREAD LISTENER] registered');

  app.event('message', async ({ event, client }) => {
    const ev = event as any;

    console.log('[MESSAGE EVENT]', {
      channel: ev.channel,
      subtype: ev.subtype ?? 'none',
      thread_ts: ev.thread_ts ?? ev.message?.thread_ts ?? ev.message?.ts,
      ts: ev.ts,
      user: ev.user,
      bot_id: ev.bot_id,
      text: typeof ev.text === 'string' ? ev.text.slice(0, 80) : undefined,
    });

    if (ev.channel_type === 'im') {
      return;
    }

    const isPlainThreadReply = !ev.subtype && ev.thread_ts && ev.thread_ts !== ev.ts;
    const isThreadUpdate = ev.subtype === 'message_replied' && (ev.message?.thread_ts || ev.message?.ts);

    if (!isPlainThreadReply && !isThreadUpdate) {
      return;
    }

    const channelId = ev.channel;
    const threadTs = isPlainThreadReply ? ev.thread_ts : (ev.message.thread_ts ?? ev.message.ts);

    if (!channelId || !threadTs) {
      return;
    }

    try {
      const replies = await client.conversations.replies({
        channel: channelId,
        ts: threadTs,
        limit: 50,
      });

      const rawMessages = ((replies.messages ?? []) as any[])
        .filter((reply) => typeof reply.text === 'string' && reply.text.trim());
      const latest = rawMessages[rawMessages.length - 1];

      if (!latest || latest.ts === threadTs || latest.bot_id || latest.subtype) {
        return;
      }

      if (processedReplyTs.has(latest.ts)) {
        return;
      }
      rememberReply(latest.ts);

      console.log('[THREAD REPLY RECEIVED]', {
        channel: channelId,
        thread_ts: threadTs,
        latest_ts: latest.ts,
        user: latest.user,
        text: latest.text.slice(0, 80),
      });

      if (isNetworkOpsQuery(latest.text)) {
        await postNetworkOpsSummary(client, channelId, threadTs);
        return;
      }

      const messages: SlackThreadMessage[] = rawMessages.map((reply) => ({
        user: reply.user ?? reply.bot_id ?? 'unknown',
        text: reply.text,
        ts: reply.ts,
      }));

      const response = await continueSlackThread({
        channel_id: channelId,
        thread_ts: threadTs,
        user_id: latest.user ?? 'unknown',
        messages,
      });

      await client.chat.postMessage({
        channel: channelId,
        text: response.reply,
        thread_ts: threadTs,
      });
    } catch (error) {
      console.error('[THREAD LISTENER ERROR]', error);
      await client.chat.postMessage({
        channel: channelId,
        text: 'I could not continue the investigation from this thread. Please rerun `/thera logs [service]` with the service and env.',
        thread_ts: threadTs,
      });
    }
  });
}
