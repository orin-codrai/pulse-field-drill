import { useEffect, useState } from 'react';
import { initData, useSignal } from '@telegram-apps/sdk-react';

import { ApiError, apiFetch } from '@/lib/api';

export interface MeUser {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  language_code?: string;
  is_premium?: boolean;
  photo_url?: string;
}

interface UseMeState {
  user: MeUser | null;
  loading: boolean;
  error: string | null;
}

export function useMe(): UseMeState {
  const raw = useSignal(initData.raw);
  const [state, setState] = useState<UseMeState>({ user: null, loading: true, error: null });

  useEffect(() => {
    if (!raw) {
      setState({ user: null, loading: false, error: 'no initData (not in Telegram?)' });
      return;
    }
    let cancelled = false;
    apiFetch<MeUser>('/api/me', raw)
      .then((user) => {
        if (!cancelled) setState({ user, loading: false, error: null });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
        setState({ user: null, loading: false, error: msg });
      });
    return () => {
      cancelled = true;
    };
  }, [raw]);

  return state;
}
