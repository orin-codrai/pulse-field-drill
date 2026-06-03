import { useEffect, useState } from 'react';

import { LOCK_TIMEOUT_MS, pinExists, subscribePinChanged } from '@/lib/pin';

/**
 * Глобальный PIN state. Cold start → если PIN set → locked. Visibility hidden
 * → save timestamp; visible → если прошло > LOCK_TIMEOUT_MS И PIN всё ещё
 * установлен → re-lock.
 */
export function usePin() {
  const [hasPin, setHasPin] = useState(() => pinExists());
  const [locked, setLocked] = useState(() => pinExists());

  useEffect(() => {
    return subscribePinChanged(() => {
      const exists = pinExists();
      setHasPin(exists);
      // setPin (новый/смена) → unlocked для текущей сессии.
      // clearPin → unlocked (нечего блокировать).
      setLocked(false);
    });
  }, []);

  useEffect(() => {
    let hiddenAt: number | null = null;
    function onVisChange() {
      if (document.hidden) {
        hiddenAt = Date.now();
      } else if (
        hiddenAt !== null
        && Date.now() - hiddenAt > LOCK_TIMEOUT_MS
        && pinExists()
      ) {
        setLocked(true);
      }
    }
    document.addEventListener('visibilitychange', onVisChange);
    return () => document.removeEventListener('visibilitychange', onVisChange);
  }, []);

  return {
    hasPin,
    locked,
    unlock: () => setLocked(false),
  };
}
