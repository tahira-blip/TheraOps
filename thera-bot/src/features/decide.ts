import { RespondFn } from '@slack/bolt';
import { askThera } from '../lib/llm';
import { getHistory, addToHistory } from '../lib/context';

export async function decisionSupport(
  question: string,
  userId: string,
  options?: { opsMode?: boolean },
  respond?: RespondFn,
): Promise<string | null> {
  const history = getHistory(userId);
  addToHistory(userId, 'user', question);
  const opsMode = options?.opsMode ?? false;

  try {
    const response = await askThera({
      userMessage: question,
      history,
      scaffoldMode: !opsMode,  // DMs and non-ops flows still ask the user's view first
      opsMode,
      taskContext: opsMode
        ? `This is an ops decision support request. Lead with the strongest hypothesis or framing first. Then map options, surface unstated assumptions, explicitly flag the factors you cannot assess, suggest one action, and end with one clarifying question.`
        : `This is a decision support request. Ask what the user thinks first. Then map options and surface unstated assumptions. Explicitly flag the factors you cannot assess. Return ownership to the user at the end.`
    });

    addToHistory(userId, 'assistant', response);
    return response;
  } catch (error) {
    console.error('[decisionSupport] All LLM options failed:', error);
    if (respond) {
      await respond({
        text: '⚠️ *All AI models are currently offline or unreachable.* Custom LLM timed out, and Gemini is currently overloaded. Please try again in a few minutes.',
        response_type: 'ephemeral',
      });
    }
    return null;
  }
}
