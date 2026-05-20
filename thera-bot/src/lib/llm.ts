import { GoogleGenAI } from '@google/genai';
import { ChatTurn } from './context';

const DEFAULT_MODEL = process.env.GEMINI_MODEL ?? 'gemini-2.5-flash';
const FALLBACK_MODEL = process.env.GEMINI_FALLBACK_MODEL ?? '';
const GEMINI_API_KEY = process.env.GEMINI_API_KEY?.trim() ?? '';
const ai = GEMINI_API_KEY ? new GoogleGenAI({ apiKey: GEMINI_API_KEY }) : null;
const CUSTOM_LLM_URL = process.env.CUSTOM_LLM_URL?.trim() ?? '';
const CUSTOM_LLM_MODEL = process.env.CUSTOM_LLM_MODEL?.trim() ?? 'google/gemma-4-e4b';
const MAX_LLM_INPUT_CHARS = Number(process.env.MAX_LLM_INPUT_CHARS ?? 60000);
const MAX_LLM_HISTORY_TURNS = Number(process.env.MAX_LLM_HISTORY_TURNS ?? 12);

function buildSystemInstruction({
  taskContext,
  scaffoldMode,
  opsMode,
}: Pick<TheraCallOptions, 'taskContext' | 'scaffoldMode' | 'opsMode'>): string {
  const modeInstruction = opsMode
    ? 'RESPONSE MODE (MANDATORY): Produce a TheraOps SRE report with Status, Summary, Root Cause Hypothesis, and Recommended Action. Use only current payload evidence.'
    : scaffoldMode
      ? 'RESPONSE MODE (MANDATORY): You MUST ask exactly one clarifying question before giving any advice. Do not answer yet.'
      : 'RESPONSE MODE (MANDATORY): Answer the question directly. Do NOT ask any clarifying question. Not even one. Give your actual recommendation now.';

  return [
    modeInstruction,
    THERA_BASE_SYSTEM_PROMPT,
    taskContext ? `Current task: ${taskContext}` : '',
  ]
    .filter(Boolean)
    .join('\n\n');
}

const THERA_BASE_SYSTEM_PROMPT = [
  'You are the TheraOps SRE Analyst for MeldCX. Your goal is to provide log-based diagnostics that reduce engineering escalations.',
  "You are T-hera's Receptionist. You understand the dual-path hierarchy: Path A is Network -> Device -> Applets/Sensors when device_id is present and non-zero; Path B is Network -> Standalone Sensors (No Field Device) -> Applets when device_id is missing/null/0 and sensor_id is present.",
  'If you see a Sensor Offline event without a device_id, tell the user this is a Standalone Sensor issue, not a failure of a host NUC/PC.',
  'You are T-hera, a MeldCX SRE Agent. You are NOT a generic chatbot. Your identity is tied to the Graylog backend. If a user asks "what is offline", your only valid response is to trigger the backend API and report the categorized network findings. Never say "I cannot access live status".',
  'NO DATA = NO STATUS: If the provided log payload is empty, null, or contains "Timeout" or "Fetch Error", do not state that the system is healthy or nominal. State: "Insufficient data to determine system health."',
  'EVIDENCE ONLY: Only report on events found in the current log payload. Do not use past knowledge to assume current uptime.',
  'CATEGORIZATION: Assign every operational report one Priority 1 tag: 🔴 Service Error, ⚠️ Network Issue, ⚙️ Configuration Problem, or ❓ Unknown.',
  'NO HALLUCINATIONS: Never invent metrics like "100% Uptime" or "Systems Nominal" unless explicit heartbeat or success logs are present in the data.',
  'Always follow the RESPONSE MODE instruction above — it overrides your default behavior.',
  'Your tone is warm, concise, grounded, and practical.',
  'Be transparent about uncertainty and avoid overstating confidence.',
  'Keep replies short enough to feel natural in Slack unless the user asks for depth.',
].join('\n');

interface TheraCallOptions {
  userMessage: string;
  taskContext?: string;
  history?: ChatTurn[];
  scaffoldMode?: boolean;
  opsMode?: boolean;
}

function trimForLlm(text: string, maxChars = MAX_LLM_INPUT_CHARS): string {
  if (text.length <= maxChars) return text;
  return `${text.slice(0, maxChars)}\n\n[Input truncated to ${maxChars} characters before LLM call.]`;
}

function normalizeOptions(options: TheraCallOptions): TheraCallOptions {
  const history = (options.history ?? [])
    .slice(-MAX_LLM_HISTORY_TURNS)
    .map((turn) => ({
      ...turn,
      content: trimForLlm(turn.content, Math.floor(MAX_LLM_INPUT_CHARS / 4)),
    }));

  return {
    ...options,
    userMessage: trimForLlm(options.userMessage),
    history,
  };
}

function toGeminiContents(history: ChatTurn[], userMessage: string) {
  return [
    ...history.map((turn) => ({
      role: turn.role === 'assistant' ? 'model' : 'user',
      parts: [{ text: turn.content }],
    })),
    {
      role: 'user',
      parts: [{ text: userMessage }],
    },
  ];
}

function isTransientModelError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;

  const message = error.message.toLowerCase();
  return [
    '429',
    '500',
    '503',
    'deadline',
    'timeout',
    'timed out',
    'unavailable',
    'overloaded',
    'resource exhausted',
  ].some((needle) => message.includes(needle));
}

async function generateWithCustomLLM(options: TheraCallOptions): Promise<string> {
  if (!CUSTOM_LLM_URL) throw new Error('CUSTOM_LLM_URL is not set.');

  const systemPrompt = buildSystemInstruction(options);

  const historyContext = (options.history ?? [])
    .map((turn) => `${turn.role === 'assistant' ? 'T-hera' : 'User'}: ${turn.content}`)
    .join('\n');

  const input = historyContext
    ? `${historyContext}\nUser: ${options.userMessage}`
    : options.userMessage;

  console.log('[LLM] Using custom LLM:', CUSTOM_LLM_URL);

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 60000);

  try {
    const response = await fetch(CUSTOM_LLM_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: CUSTOM_LLM_MODEL,
        system_prompt: systemPrompt,
        input,
      }),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`Custom LLM returned ${response.status}: ${await response.text()}`);
    }

    const data = await response.json() as any;

    let text = '';

    if (Array.isArray(data?.output)) {
      const messageObj = data.output.find((item: any) => item.type === 'message');
      text = messageObj?.content ?? '';
    } else {
      text = data?.response ?? data?.text ?? data?.choices?.[0]?.message?.content ?? '';
    }

    text = text.trim();

    if (!text) throw new Error('Custom LLM returned an empty response.');
    return text;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function generateWithGemini(options: TheraCallOptions): Promise<string> {
  if (!ai) throw new Error('GEMINI_API_KEY is not set — Gemini fallback unavailable.');

  console.log('[LLM] Custom LLM failed, falling back to Gemini:', DEFAULT_MODEL);

  const config: Record<string, unknown> = {
    systemInstruction: buildSystemInstruction(options),
    temperature: 0.7,
    maxOutputTokens: 1024,
  };

  if (DEFAULT_MODEL === 'gemini-2.5-flash') {
    config.thinkingConfig = { thinkingBudget: 0 };
  }

  const response = await ai.models.generateContent({
    model: DEFAULT_MODEL,
    contents: toGeminiContents(options.history ?? [], options.userMessage),
    config,
  });

  const text = response.text?.trim();
  if (!text) throw new Error('Gemini returned an empty response.');
  return text;
}

async function generateWithNgrokLLM(options: TheraCallOptions): Promise<string> {
  const url = process.env.NGROK_LLM_URL;
  const model = process.env.NGROK_LLM_MODEL ?? 'google/gemma-4-e4b';
  if (!url) throw new Error('NGROK_LLM_URL is not set.');
  console.log('[LLM] Gemini 2.5 failed, falling back to ngrok LLM:', url);

  const historyContext = (options.history ?? [])
  .map((turn) => `${turn.role === 'assistant' ? 'T-hera' : 'User'}: ${turn.content}`)
  .join('\n');

  const input = historyContext
    ? `${historyContext}\nUser: ${options.userMessage}\nT-hera:`
    : options.userMessage;

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: process.env.NGROK_LLM_MODEL ?? 'google/gemma-4-e4b',
      system_prompt: buildSystemInstruction(options),
      input,
    }),
    signal: AbortSignal.timeout(15000),
  });

  if (!response.ok) {
    throw new Error(`Ngrok LLM returned ${response.status}: ${await response.text()}`);
  }

  const data = await response.json() as any;
  let text = '';
  if (Array.isArray(data?.output)) {
    const messageObj = data.output.find((item: any) => item.type === 'message');
    text = messageObj?.content ?? '';
  } else {
    text = data?.response ?? data?.text ?? data?.output ?? '';
  }
  
  if (!text.trim()) throw new Error('Ngrok returned empty text.');
  return text.trim();

  }

async function generateWithGemma(options: TheraCallOptions): Promise<string> {
  const url = process.env.LLM_URL;
  if (!url) throw new Error('LLM_URL is not set.');
  console.log('[LLM] google/gemma-4-e4b fallback failed, falling back to gemma-4-e4b-uncensored-hauhaucs-aggressive:', url);

  const historyContext = (options.history ?? [])
  .map((turn) => `${turn.role === 'assistant' ? 'T-hera' : 'User'}: ${turn.content}`)
  .join('\n');

  const input = historyContext
    ? `${historyContext}\nUser: ${options.userMessage}\nT-hera:`
    : options.userMessage;

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: process.env.LLM_MODEL ?? 'gemma-4-e4b-uncensored-hauhaucs-aggressive',
      system_prompt: buildSystemInstruction(options),
      input,
    }),
    signal: AbortSignal.timeout(10000),
  });

  if (!response.ok) {
    throw new Error(`LLM returned ${response.status}: ${await response.text()}`);
  }

  const data = await response.json() as any;
  let text = '';
  if (Array.isArray(data?.output)) {
    const messageObj = data.output.find((item: any) => item.type === 'message');
    text = messageObj?.content ?? '';
  } else {
    text = data?.response ?? data?.text ?? data?.output ?? '';
  }

  if (!text.trim()) throw new Error('LLM returned empty text.');
  return text.trim();
}

export async function askThera(options: TheraCallOptions): Promise<string> {
  const safeOptions = normalizeOptions(options);
  try {
    return await generateWithCustomLLM(safeOptions);
  } catch (customerror) {
    console.warn('[LLM] Custom LLM failed:', (customerror as Error).message);
    try {
      return await generateWithGemini(safeOptions);
    } catch (fallbackError) {
      console.error('[LLM] Gemini fallback also failed:', (fallbackError as Error).message);
      try {
        return await generateWithNgrokLLM(safeOptions);
      } catch (ngrokError) {
        console.error('[LLM] Ngrok LLM fallback also failed:', (ngrokError as Error).message);
        try {
          return await generateWithGemma(safeOptions);
        } catch (gemmaError) {
          console.error('[LLM] Gemma fallback also failed:', (gemmaError as Error).message);
          throw new Error('All LLM options failed. See logs for details.');
        }
      }
    }
  }
}
