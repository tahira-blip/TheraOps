export interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
}

interface ChatSession {
  turns: ChatTurn[];
  updatedAt: number;
}

const store = new Map<string, ChatSession>();
const MAX_TURNS = 8;
const MAX_SESSIONS = 200;
const SESSION_TTL_MS = 24 * 60 * 60 * 1000;

function pruneStore() {
  const cutoff = Date.now() - SESSION_TTL_MS;

  for (const [id, session] of store.entries()) {
    if (session.updatedAt < cutoff) {
      store.delete(id);
    }
  }

  while (store.size > MAX_SESSIONS) {
    const oldestSession = [...store.entries()].sort((a, b) => a[1].updatedAt - b[1].updatedAt)[0];
    if (!oldestSession) {
      break;
    }

    store.delete(oldestSession[0]);
  }
}

export const getHistory = (id: string) => {
  pruneStore();
  const session = store.get(id);
  if (!session) {
    return [];
  }

  session.updatedAt = Date.now();
  return [...session.turns];
};

export function addToHistory(id: string, role: 'user' | 'assistant', content: string) {
  const session = store.get(id) ?? { turns: [], updatedAt: Date.now() };
  session.turns.push({ role, content });
  if (session.turns.length > MAX_TURNS * 2) session.turns.splice(0, 2);
  session.updatedAt = Date.now();
  store.set(id, session);
  pruneStore();
}

export const clearHistory = (id: string) => store.delete(id);
