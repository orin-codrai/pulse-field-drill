import type { FC, ReactNode } from 'react';

import { usePin } from '@/hooks/usePin';
import { PinLockPage } from '@/pages/PinLockPage/PinLockPage';

interface Props {
  children: ReactNode;
}

/**
 * Оборачивает приложение. Если PIN set + locked → показывает PinLockPage
 * вместо children. После успешного unlock → render children как обычно.
 *
 * Routes /pin/setup сами не блокируются: locked=true только если pinExists()
 * на cold start. Wipe-on-forgot или clearPin() → notifyPinChanged → locked=false
 * → setup доступен.
 */
export const PinGuard: FC<Props> = ({ children }) => {
  const { locked, hasPin, unlock } = usePin();

  if (locked && hasPin) {
    return <PinLockPage onUnlock={unlock} />;
  }
  return <>{children}</>;
};
