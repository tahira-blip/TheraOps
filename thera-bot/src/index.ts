import { App } from '@slack/bolt';
import { WebClient } from '@slack/web-api';
import * as crypto from 'crypto';
import * as dotenv from 'dotenv';
import * as path from 'path';
dotenv.config({ path: path.join(__dirname, '../../.env'), override: true });
import { registerCommandHandler } from './handlers/command';
import { registerMentionHandler } from './handlers/mention';
import { registerShortcutHandler } from './handlers/shortcut';
import { registerDMHandler } from './handlers/dm';
import { registerLogActionHandlers } from './handlers/logActions';
import { registerThreadListener } from './listeners/threads';

function requireEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

const slackBotToken = requireEnv('SLACK_BOT_TOKEN');
const slackSigningSecret = requireEnv('SLACK_SIGNING_SECRET');
const slackAppToken = requireEnv('SLACK_APP_TOKEN');
const adminSlackUserId = process.env.ADMIN_SLACK_USER_ID?.trim();

export const app = new App({
  token: slackBotToken,
  signingSecret: slackSigningSecret,
  socketMode: true,
  appToken: slackAppToken,
});

app.error(async (err) => {
  console.error('[T-HERA ERROR]', err);
});

registerCommandHandler(app);
registerMentionHandler(app);
registerShortcutHandler(app);
registerDMHandler(app);
registerLogActionHandlers(app);
registerThreadListener(app);

async function reportFatalError(error: Error, label: string) {
  console.error(`[${label}]`, error);

  if (!adminSlackUserId) {
    return;
  }

  try {
    // Log potential network/proxy overrides that can affect WebClient requests
    try {
      console.log('ENV HTTPS_PROXY:', process.env.HTTPS_PROXY || process.env.https_proxy || '<none>');
      console.log('ENV HTTP_PROXY:', process.env.HTTP_PROXY || process.env.http_proxy || '<none>');
      console.log('ENV ALL_PROXY:', process.env.ALL_PROXY || process.env.all_proxy || '<none>');
      console.log('ENV NO_PROXY:', process.env.NO_PROXY || process.env.no_proxy || '<none>');
      console.log('ENV SLACK_API_URL:', process.env.SLACK_API_URL || '<none>');
    } catch (e) {
      console.log('could not read proxy envs', e);
    }
    await app.client.chat.postMessage({
      channel: adminSlackUserId,
      text: `⚠️ T-hera ${label.toLowerCase()}: ${error.message}`,
    });
  } catch (notificationError) {
    console.error('[FATAL NOTIFY ERROR]', notificationError);
  }
}

process.on('uncaughtException', async (error) => {
  const fatalError = error instanceof Error ? error : new Error(String(error));
  await reportFatalError(fatalError, 'FATAL');
  process.exit(1);
});

process.on('unhandledRejection', async (reason) => {
  const fatalError = reason instanceof Error ? reason : new Error(String(reason));
  await reportFatalError(fatalError, 'UNHANDLED REJECTION');
  process.exit(1);
});

(async () => {
  console.log('SLACK token prefix:', process.env.SLACK_BOT_TOKEN?.slice(0, 6), 'length:', process.env.SLACK_BOT_TOKEN?.length);
  try {
    try {
      (app.client as any).token = slackBotToken;
      console.log('forced app.client.token from env');
    } catch (e) {
      console.log('could not force app.client.token', e);
    }
    // show fingerprints to ensure tokens match exactly
    try {
      const envFp = crypto.createHash('sha256').update(slackBotToken).digest('hex').slice(0, 12);
      const clientToken = (app.client as any).token || '';
      const clientFp = clientToken ? crypto.createHash('sha256').update(clientToken).digest('hex').slice(0, 12) : '<none>';
      console.log('fingerprint env:', envFp, 'client:', clientFp);
    } catch (e) {
      console.log('could not compute token fingerprint', e);
    }
    // direct WebClient test using the env token to compare
    try {
      const directClient = new WebClient(slackBotToken);
      const directRes = await directClient.auth.test();
      console.log('direct WebClient auth.test result:', directRes);
    } catch (e) {
      console.error('direct WebClient auth.test error:', (e as any)?.data || e);
    }
    try {
      // debug: show the token held by the app's WebClient (do not print full token)
      // eslint-disable-next-line @typescript-eslint/ban-ts-comment
      // @ts-ignore
      console.log('app.client.token prefix:', (app.client.token || '').toString().slice(0, 12));
    } catch (e) {
      console.log('could not read app.client.token', e);
    }
    const authRes = await app.client.auth.test();
    if (!authRes.ok) {
      console.error('[SLACK AUTH TEST FAILED]', authRes);
      process.exit(1);
    }
    console.log('Slack auth.test ok for user:', authRes.user_id);
  } catch (err: any) {
    console.error('[SLACK AUTH TEST ERROR]', err);
    if (err?.data?.error === 'invalid_auth') {
      console.error('Invalid Slack bot token. Ensure SLACK_BOT_TOKEN is a Bot Token (xoxb-) and the app is installed to this workspace.');
    }
    process.exit(1);
  }

  await app.start();
  console.log('◆ T-hera is thinking');
})();
