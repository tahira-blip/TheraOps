import { App } from '@slack/bolt';
import { summarizeThread } from '../features/summarize';

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

export function registerShortcutHandler(app: App) {
  app.shortcut('summarize_thread_shortcut', async ({ ack, body, client }) => {
    await ack(); // Acknowledge immediately within 3 seconds

    const startTime = Date.now();
    const payload = body as any;

    console.log('[SHORTCUT EVENT RECEIVED]', JSON.stringify(payload, null, 2));

    const channelId: string = payload.channel?.id || '';
    const threadTs: string = payload.message?.thread_ts || payload.message?.ts || '';
    const userId: string = payload.user?.id || 'unknown';

    console.log(`[SHORTCUT DEBUG] user=${userId}, channel=${channelId}, thread_ts=${threadTs}`);

    try {
      if (!threadTs) {
        console.log('[SHORTCUT] No thread_ts found, cannot summarize.');
        await client.chat.postEphemeral({
          channel: channelId,
          user: userId,
          text: 'This shortcut only works on threaded messages. Open a thread first.',
        });
        logRequest({ userId, cmd: 'shortcut_no_thread', channel: channelId, latency_ms: Date.now() - startTime, status: 'ok' });
        return;
      }

      console.log(`[SHORTCUT] Summarizing thread: channel=${channelId}, thread_ts=${threadTs}`);

      await client.chat.postEphemeral({
        channel: channelId,
        user: userId,
        text: 'Reading thread...',
      });

      const summary = await summarizeThread(client, channelId, threadTs);

      await client.chat.postEphemeral({
        channel: channelId,
        user: userId,
        text: summary,
      });

      logRequest({ userId, cmd: 'shortcut_summarize', channel: channelId, latency_ms: Date.now() - startTime, status: 'ok' });

    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      console.error('[SHORTCUT ERROR]', error);

      try {
        await client.chat.postEphemeral({
          channel: channelId,
          user: userId,
          text: 'Something broke while summarizing. Try again?',
        });
      } catch (postErr) {
        console.error('[SHORTCUT POST ERROR]', postErr);
      }

      logRequest({
        userId,
        cmd: 'shortcut',
        channel: channelId,
        latency_ms: Date.now() - startTime,
        status: 'error',
        error: errMsg,
      });
    }
  });
}
