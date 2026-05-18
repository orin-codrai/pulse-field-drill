/**
 * Tiny event bus для invalidation после mutation. Без React Query / SWR —
 * хуки depend'ят на счётчик per-key; mutation вызывает bump(key); хуки
 * re-fetch'ат.
 *
 * Решение «stale balance после tx»: navigate(-1) на BalancesPage не remount'ит
 * route (HashRouter), и useEffect не перезапустится. Через bump'нутый счётчик
 * useEffect зависит от него и re-fetch'ает.
 */

import { useEffect, useState } from 'react';

const listeners: Record<string, Set<() => void>> = {};

export type RefetchKey = 'balances' | 'transactions' | 'accounts' | 'categories' | 'goals' | 'budgets' | 'reports';

export function bump(key: RefetchKey): void {
  listeners[key]?.forEach((fn) => fn());
}

export function useRefetchKey(key: RefetchKey): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const fn = () => setN((x) => x + 1);
    if (!listeners[key]) listeners[key] = new Set();
    listeners[key].add(fn);
    return () => {
      listeners[key].delete(fn);
    };
  }, [key]);
  return n;
}
