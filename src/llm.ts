// Minimal client for any OpenAI-compatible chat endpoint (Ollama locally, or a
// hosted free tier). Swap providers by changing a few environment variables.
//
// Why a small free model: ShelterApp has no AI budget, and paid models charge for
// every word, every conversation. Mandy uses the model as little as possible (one
// call to understand a message, one safety check, one short caring sentence when
// needed); everything else is plain code. See "Why we built it this way" in DESIGN.md.

export interface ChatMessage { role: 'system' | 'user' | 'assistant'; content: string }

export async function chat(messages: ChatMessage[], opts: { json?: boolean; maxTokens?: number } = {}): Promise<string> {
  // Read settings on each call, so values loaded from .env after startup are used.
  const base = process.env.LLM_BASE_URL || 'http://localhost:11434/v1';
  const model = process.env.LLM_MODEL || 'gemma4:e2b';
  const key = process.env.LLM_API_KEY ?? '';
  // "none" turns off slow "thinking" in models like Gemma 4 on Ollama. Set it empty
  // for providers that don't accept the setting.
  const effort = process.env.LLM_REASONING_EFFORT ?? 'none';

  const r = await fetch(`${base}/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(key ? { Authorization: `Bearer ${key}` } : {}) },
    body: JSON.stringify({
      model,
      messages,
      temperature: 0.2,
      max_tokens: opts.maxTokens ?? 300,
      ...(opts.json ? { response_format: { type: 'json_object' } } : {}),
      ...(effort ? { reasoning_effort: effort } : {}),
    }),
    signal: AbortSignal.timeout(60000),
  });
  if (!r.ok) throw new Error(`model HTTP ${r.status}`);
  const data = await r.json() as any;
  return String(data.choices?.[0]?.message?.content ?? '').trim();
}
