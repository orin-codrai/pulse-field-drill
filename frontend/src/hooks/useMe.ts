import { useEffect, useState } from 'react';
import { initData, useSignal } from '@tma.js/sdk-react';

import { ApiError, apiFetch } from '@/lib/api';
import { useRefetchKey } from '@/lib/refetch';

/**
 * Backend MeOut (P7 расширенный). `id` = tg_id (backward compat с frontend body.id),
 * `internal_id` = ORM User.id (MF14-6 renamed). `internal_id` используется ТОЛЬКО
 * в AuditHistoryPage для «вы»-маркера через сравнение с audit.actor_user_id;
 * сервер никогда не accept'ит его в URL/body.
 */
export interface MeUser {
  id: number;                       // tg_id
  first_name: string;
  last_name: string | null;
  username: string | null;
  language_code: string | null;
  is_premium: boolean | null;
  photo_url: string | null;
  internal_id: number;              // ORM User.id
  active_workspace_id: number | null;
  display_name: string | null;
  email: string | null;
  consent_at: string | null;
  deleted_at: string | null;
  registration_required: boolean;
}

interface UseMeState {
  user: MeUser | null;
  loading: boolean;
  error: string | null;
}

export function useMe(): UseMeState {
  const raw = useSignal(initData.raw);
  const refetchN = useRefetchKey('me');
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
    return () => { cancelled = true; };
  }, [raw, refetchN]);

  return state;
}
