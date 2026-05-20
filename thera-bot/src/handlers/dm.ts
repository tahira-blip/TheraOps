import { App } from '@slack/bolt';
import { askThera } from '../lib/llm';
import { addToHistory, clearHistory, getHistory } from '../lib/context';
import { fetchLogDiagnosis, LogsDiagnosisRequest, sendReceptionistTechnicalChat, fetchNetworkSummary } from '../lib/theraopsBackend';
import { chunkSlackText, logsDiagnosisBlocks } from '../lib/logMessage';
import { compactNetworksForLlm, deterministicNetworkReport } from '../lib/networkSummary';

const userModes = new Map<string, 'scaffold' | 'direct'>();

const SERVICE_ALIASES = [
  'vianapulse',
  'viana',
  'vp',
  'api',
  'backend',
  'thera-api',
  'worker',
  'jobs',
  'queue',
  'task-runner',
  'billing',
  'payments',
  'billing-api',
];

function detectServiceAlias(text: string): string | undefined {
  const lowered = text.toLowerCase();
  return SERVICE_ALIASES.find((alias) => {
    const escaped = alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp(`(^|[^a-z0-9-])${escaped}([^a-z0-9-]|$)`, 'i').test(lowered);
  });
}

function isGraylogOpsQuery(message: string): boolean {
  const lowered = message.toLowerCase();
  const patterns = [
    /offline/i,
    /status/i,
    /devices?\\.(down|offline|error)/i,
    /\bour networks?\b/i,
    /\bnetworks?\b/i,
    /\bnetwork (health|status|issues?|errors?|summary)\b/i,
    /\bshow.*networks?\b/i,
    /\bnetwork[_\s-]?id\b/i,
    /\b(field[_\s-]?devices?|standalone[_\s-]?components?)\b/i,
    /\b(devices?|sensors?).*\b(service\s+)?applets?\b/i,
    /\b(service\s+)?applets?.*\b(devices?|sensors?)\b/i,
    /\bhybrid hierarchy\b/i,
    /which hosts/i,
    /show.*(errors?|logs?)/i,
    /what.*(offline|down|error)/i,
  ];
  return patterns.some(p => p.test(lowered));
}

function parseTimeRange(text: string): number | null {
  const lowered = text.toLowerCase();
  const hourMatch = lowered.match(/(\\d+(?:\\.\\d+)?)\\s*(hours?|hrs?)/);
  const minMatch = lowered.match(/(\\d+(?:\\.\\d+)?)\\s*(minutes?|mins?)/);
  const dayMatch = lowered.match(/today|last\\s*24h|24\\s*hours?/);

  if (hourMatch) return Math.round(parseFloat(hourMatch[1]) * 3600);
  if (minMatch) return Math.round(parseFloat(minMatch[1]) * 60);
  if (dayMatch) return 86400;
  return null;
}

function isTechnicalIntent(text: string, serviceAlias?: string): boolean {
  const lowered = text.toLowerCase();
  const operationalTerms = [
    'slow',
    'down',
    'latency',
    'timeout',
    'timing out',
    'error',
    'errors',
    'failing',
    'failed',
    'failure',
    'incident',
    'logs',
    'graylog',
    'healthy',
    'health',
    'degraded',
    'spike',
    'unavailable',
    'outage',
    'offline',
    'heartbeat',
    'stale',
  ];

  return Boolean(serviceAlias) && operationalTerms.some((term) => lowered.includes(term));
}

export function registerDMHandler(app: App) {
  app.message(async ({ message, say, client }) => {
    console.log('[DM RECEIVED]', {
      channel_type: message.channel_type,
      user: (message as any).user,
      subtype: message.subtype,
      text: (message as any).text?.slice(0, 50)
    });
    if (message.channel_type !== 'im' || message.subtype) return;
    const userId = (message as any).user;
    const text: string = (message as any).text?.trim() ?? '';
    if (!userId || !text) return;

    // Mode switching commands
    if (text === 'mode: scaffold') {
      userModes.set(userId, 'scaffold');
      await say("Scaffold mode. I'll ask you a question before I answer yours.");
      return;
    }
    if (text === 'mode: direct') {
      userModes.set(userId, 'direct');
      await say("Direct mode. Straight to answers — you can switch back anytime.");
      return;
    }
    if (text === 'clear') {
      clearHistory(userId);
      await say('Memory cleared.');
      return;
    }

    // First-time onboarding
    if (!userModes.has(userId)) {
      userModes.set(userId, 'scaffold'); // Default to scaffold
      await say(`*T-hera here.* I help you think more clearly — not by thinking for you.\n\nBy default I'll ask you a question before I give you an answer. If you'd rather I go straight to answers, say \`mode: direct\`.\n\nWhat's on your mind?`);
      return;
    }

    const scaffoldMode = (userModes.get(userId) ?? 'scaffold') === 'scaffold';
    const history = getHistory(userId);
    console.log('[DM HISTORY]', JSON.stringify(history, null, 2));
    addToHistory(userId, 'user', text);

    console.log('[DM DEBUG]', { userId, mode: userModes.get(userId), scaffoldMode, text });

    try {
    const serviceAlias = detectServiceAlias(text);

    // Check for Graylog ops query before technical chat/LLM
    if (isGraylogOpsQuery(text)) {
      console.log("DEBUG GRAYLOG URL:", process.env.GRAYLOG_URL);
        try {
          await say(`🔍 Checking network status...`);
        let payload: any;
        try {
          payload = await fetchNetworkSummary('graylog');
        } catch (fetchErr) {
          console.error('[DM NETWORK FETCH ERROR]', fetchErr);
          await say('Failed to fetch network status from backend.');
          return;
        }
        if (payload.status === 'error') {
          await say(payload.message || 'Graylog query failed.');
          return;
        }
        const networks = payload.networks || {};
        const entries = Object.entries(networks);
        
        if (entries.length === 0) {
          const noDataReply = '*Status*: ❓ Unknown\n*Summary*: Insufficient data to determine system health.\n*Root Cause Hypothesis*: The network issue payload contained no current issue entries, and no heartbeat or success logs were provided to prove health.\n*Recommended Action*: Rerun with raw Graylog logs or a heartbeat-backed health source before declaring the system healthy.';
          await say(noDataReply);
          addToHistory(userId, 'assistant', noDataReply);
          return;
        }

        const compactNetworks = compactNetworksForLlm(networks);
        const prompt = `Summarize this network issue payload concisely using the hybrid hierarchy. Root level is network_id. Field Devices are hardware-bound hosts with sensors and applets nested under the device; applets may be direct device children or nested under sensors. Standalone Components are edgeless applets or sensors directly under the network without a parent device. Cite event_code and device_message from the affected child component and do not hallucinate any data:\n${JSON.stringify(compactNetworks)}`;
        let aiSummary: string;
        try {
          aiSummary = await askThera({ userMessage: prompt, scaffoldMode: false, opsMode: false });
        } catch (llmErr) {
          console.error('[DM NETWORK LLM ERROR]', llmErr);
          aiSummary = deterministicNetworkReport(networks);
        }

        const msg = await client.chat.postMessage({ channel: message.channel, text: aiSummary });

        await client.files.uploadV2({
          channel_id: message.channel,
          thread_ts: msg.ts!,
          content: JSON.stringify(payload.raw_data, null, 2),
          filename: 'graylog_raw_data.txt',
          title: 'Raw Graylog Data'
        });

        addToHistory(userId, 'assistant', aiSummary);
        return;
      } catch (graylogErr) {
        console.error('[DM GRAYLOG ERROR]', graylogErr);
        await say('Graylog query failed, falling back to analysis...');
      }
    }

    if (isTechnicalIntent(text, serviceAlias)) {
      // Bypass service validation for simple offline/status intents and call Trinity directly
      const lowered = text.toLowerCase();
      if (/\boffline\b/.test(lowered) || /\bstatus\b/.test(lowered)) {
          try {
            await say('🔍 Checking network status...');
          let payload: any;
          try {
            payload = await fetchNetworkSummary('graylog');
          } catch (fetchErr) {
            console.error('[DM TRINITY FETCH ERROR]', fetchErr);
            await say('Failed to fetch network status from backend.');
            return;
          }
          if (payload.status === 'error') {
            await say(payload.message || 'Graylog query failed.');
            return;
          }
          const networks = payload.networks || {};
          const entries = Object.entries(networks);
          if (entries.length === 0) {
            const noDataReply = '*Status*: ❓ Unknown\n*Summary*: Insufficient data to determine system health.\n*Root Cause Hypothesis*: The network issue payload contained no current issue entries, and no heartbeat or success logs were provided to prove health.\n*Recommended Action*: Rerun with raw Graylog logs or a heartbeat-backed health source before declaring the system healthy.';
            await say(noDataReply);
            addToHistory(userId, 'assistant', noDataReply);
            return;
          }
          const compactNetworks = compactNetworksForLlm(networks);
          const prompt = `Summarize this network issue payload concisely using the hybrid hierarchy. Root level is network_id. Field Devices are hardware-bound hosts with sensors and applets nested under the device; applets may be direct device children or nested under sensors. Standalone Components are edgeless applets or sensors directly under the network without a parent device. Cite event_code and device_message from the affected child component and do not hallucinate any data:\n${JSON.stringify(compactNetworks)}`;
          let aiSummary: string;
          try {
            aiSummary = await askThera({ userMessage: prompt, scaffoldMode: false, opsMode: false });
          } catch (llmErr) {
            console.error('[DM TRINITY LLM ERROR]', llmErr);
            aiSummary = deterministicNetworkReport(networks);
          }
          const msg = await client.chat.postMessage({ channel: message.channel, text: aiSummary });
          await client.files.uploadV2({
            channel_id: message.channel,
            thread_ts: msg.ts!,
            content: JSON.stringify(payload.raw_data, null, 2),
            filename: 'trinity_raw_data.txt',
            title: 'Trinity Raw Data'
          });
          addToHistory(userId, 'assistant', aiSummary);
          return;
        } catch (err) {
          console.error('[DM TRINITY ERROR]', err);
          await say('Network summary query failed, falling back to detailed analysis...');
        }
      }

      const response = await sendReceptionistTechnicalChat({
        user_message: text,
        service_alias: serviceAlias,
        thread_history: history,
      });
      addToHistory(userId, 'assistant', response.reply);
      await say(response.reply);
      return;
    }

    const reply = await askThera({ userMessage: text, history, scaffoldMode });
    addToHistory(userId, 'assistant', reply);
    await say({ text: reply, blocks: [{ type: 'section', text: { type: 'mrkdwn', text: reply } }] });
    } catch (error: unknown) {
      console.error('[DM ERROR] Full error details:', error);

      const isDev = process.env.NODE_ENV === 'development';
      let errorMsg = 'Something broke on my end. Try again?';
      if (isDev && error instanceof Error) {
        errorMsg = `Debug: ${error.name} — ${error.message} (check console)`;
      }
      await say(errorMsg);
    }
  });
}
