/** Same-origin HTTP transport used by Agent Studio feature APIs. */

export interface SourceList<T> {
  items: T[]
  source: string
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { credentials: 'include', signal })
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<T>
}

export async function postJson<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<T>
}

export async function postNdjson<T>(
  path: string,
  body: object,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok) throw await responseError(response)
  if (!response.body) throw new Error('The Studio API did not provide a response stream.')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let pending = ''
  while (true) {
    const { done, value } = await reader.read()
    pending += decoder.decode(value, { stream: !done })
    const lines = pending.split('\n')
    pending = lines.pop() ?? ''
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line) as T)
    }
    if (done) break
  }
  if (pending.trim()) onEvent(JSON.parse(pending) as T)
}

export async function putJson<T>(path: string, body: object): Promise<T> {
  const response = await fetch(path, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<T>
}

export async function deleteJson<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(path, {
    method: 'DELETE',
    credentials: 'include',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<T>
}

export function queryString(values: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== '') query.set(key, String(value))
  })
  const encoded = query.toString()
  return encoded ? `?${encoded}` : ''
}

async function responseError(response: Response): Promise<Error> {
  let message = `Studio API request failed (${response.status})`
  try {
    const payload = await response.json() as { error?: string; detail?: string }
    message = payload.error || payload.detail || message
  } catch {
    // Keep the HTTP fallback when the response is not JSON.
  }
  return new Error(message)
}
