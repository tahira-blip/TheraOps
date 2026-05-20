import { App } from '@slack/bolt';
import { fetchLogDiagnosis } from '../lib/theraopsBackend';
import {
  decodeLogActionContext,
  LOG_TIME_RANGE_ACTION,
  logsDiagnosisBlocks,
} from '../lib/logMessage';

export function registerLogActionHandlers(app: App) {
  app.action(LOG_TIME_RANGE_ACTION, async ({ ack, body, client }) => {
    await ack();

    const payload = body as any;
    const action = payload.actions?.[0];
    const selectedValue = action?.selected_option?.value;
    const channelId = payload.channel?.id;
    const userId = payload.user?.id;
    const parentTs = payload.message?.thread_ts || payload.message?.ts;

    if (!channelId || !parentTs || !selectedValue) {
      return;
    }

    try {
      const context = decodeLogActionContext(selectedValue);
      const windowSeconds = context.window_seconds ?? 3600;
      const minutes = Math.round(windowSeconds / 60);

      await client.chat.postEphemeral({
        channel: channelId,
        user: userId,
        text: `Rerunning Universal Search over the last ${minutes < 60 ? `${minutes} minutes` : `${windowSeconds / 3600} hour(s)`}...`,
        thread_ts: parentTs,
      });

      const request = context;
      const diagnosis = await fetchLogDiagnosis(request);

      await client.chat.postMessage({
        channel: channelId,
        thread_ts: parentTs,
        text: diagnosis.reply,
        blocks: logsDiagnosisBlocks(diagnosis.reply, request),
      });
    } catch (error) {
      console.error('[LOG ACTION ERROR]', error);
      await client.chat.postMessage({
        channel: channelId,
        thread_ts: parentTs,
        text: 'I could not rerun the log search. Try `/thera logs [service]` again from the channel.',
      });
    }
  });
}
