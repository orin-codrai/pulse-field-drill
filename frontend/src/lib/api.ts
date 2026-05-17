/**
 * Thin fetch wrapper that injects `Authorization: tma <initDataRaw>` on every
 * request to the backend. Caller passes `raw` explicitly so the function stays
 * testable without React/SDK in scope.
 */

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`api ${status}: ${detail}`);
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  raw: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `tma ${raw}`,
      ...(init?.headers ?? {}),
    },
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // body was not JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}
