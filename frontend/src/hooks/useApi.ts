import { useEffect, useState } from 'react';
import { initData, useSignal } from '@tma.js/sdk-react';

import { ApiError, apiFetch } from '@/lib/api';
import { useRefetchKey, type RefetchKey } from '@/lib/refetch';

export interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/**
 * Generic «загрузить ресурс через apiFetch с авто-refetch на bump(key)».
 * Используется всеми useX-хуками (useAccounts/useBalances/useCategories/useTransactions/...).
 */
export function useApi<T>(path: string | null, key: RefetchKey): ApiState<T> {
  const raw = useSignal(initData.raw);
  const refetchN = useRefetchKey(key);
  const [state, setState] = useState<ApiState<T>>({
    data: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    if (path === null) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    if (!raw) {
      setState({ data: null, loading: false, error: 'no initData (not in Telegram?)' });
      return;
    }
    let cancelled = false;
    setState((prev) => ({ ...prev, loading: true }));
    apiFetch<T>(path, raw)
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
        setState({ data: null, loading: false, error: msg });
      });
    return () => {
      cancelled = true;
    };
  }, [path, raw, refetchN]);

  return state;
}
