export type Role = 'employee' | 'quality';
export type Account = { user_id: string; name: string; role: Role; password_required?: boolean };
export type AuthSession = { user: Account; token: string };

export type Citation = {
  chunk_id: string;
  source_type: 'regulation' | 'qa_entry';
  doc_id: string;
  doc_title: string;
  section: string;
  quote: string;
  directorate?: string | null;
};

export type Answer = {
  message_id: string;
  status: 'answered' | 'partial' | 'needs_clarification' | 'escalated' | 'conflict' | 'refused';
  answer: string;
  citations: Citation[];
  clarification?: { question: string; options?: string[] } | null;
  ticket?: { ticket_id: string; created: boolean; message: string } | null;
};

export type Message = {
  message_id: string;
  role: 'user' | 'assistant' | 'quality';
  content: string;
  result?: Answer | null;
  ticket_id?: string;
};
export type Session = { session_id: string; created_at: string; title?: string | null };
export type Ticket = { ticket_id: string; question: string; status: 'open' | 'answered' | 'published'; created_at: string };
export type MyTicket = Ticket & {
  session_id: string;
  answer: string | null;
  doc_id: string | null;
  section: string | null;
};
export type Entry = {
  qa_entry_id: string;
  answer: string;
  doc_id: string | null;
  section: string | null;
  status: 'draft' | 'confirmed';
  confirmed_by?: string | null;
};

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function errorFrom(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => ({})) as { detail?: unknown };
  return new ApiError(typeof body.detail === 'string' ? body.detail : `Сервис ответил ошибкой (${response.status}).`, response.status);
}

export async function request<T>(path: string, token?: string, payload?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: payload === undefined ? 'GET' : 'POST',
    signal,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(payload === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  if (!response.ok) throw await errorFrom(response);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export function login(userId: string, password?: string): Promise<AuthSession> {
  return request<AuthSession>('/auth/login', undefined, { user_id: userId, password });
}

export async function askQuestion(token: string, sessionId: string, question: string, onStep: (title: string) => void): Promise<Answer> {
  const response = await fetch('/api/chat/ask/stream', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, question }),
  });
  if (!response.ok) throw await errorFrom(response);
  if (!response.body) throw new Error('Сервис не открыл поток ответа.');
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';

  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += chunk.value;
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';
      for (const frame of frames) {
        const lines = frame.split(/\r?\n/);
        const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim();
        const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
        if (!data) continue;
        if (event === 'step') onStep((JSON.parse(data) as { title: string }).title);
        if (event === 'result') return JSON.parse(data) as Answer;
        if (event === 'error') throw new Error((JSON.parse(data) as { message?: string }).message || 'Ошибка агента.');
      }
    }
    throw new Error('Поток завершился до получения ответа.');
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
