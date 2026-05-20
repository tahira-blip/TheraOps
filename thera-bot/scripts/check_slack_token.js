const path = require('path');
const dotenv = require('dotenv');
dotenv.config({ path: path.join(__dirname, '../../.env') });
const { WebClient } = require('@slack/web-api');
const crypto = require('crypto');

const t = process.env.SLACK_BOT_TOKEN;
if (!t) {
  console.error('SLACK_BOT_TOKEN is not set in ../../.env');
  process.exit(1);
}

console.log('token visible prefix:', t.slice(0, 8));
console.log('token length:', t.length);
const fingerprint = crypto.createHash('sha256').update(t).digest('hex').slice(0, 12);
console.log('token sha256 fingerprint:', fingerprint);

(async () => {
  try {
    const client = new WebClient(t, { logLevel: 'debug' });
    const res = await client.auth.test();
    console.log('auth.test result:', res);
  } catch (err) {
    console.error('auth.test error:', err?.data || err);
    process.exit(2);
  }
})();
