import { useEffect, useRef } from 'react';
import { mainButton } from '@tma.js/sdk-react';

/**
 * MainButton wiring с защитой от stale closure (plan v2 must-fix #6).
 *
 * Правила (must-fix #5):
 * 1. Каждый компонент с MainButton делает setParams + onClick + cleanup сам.
 *    Не предполагать, что предыдущая страница cleanup'нула — setParams перебивает.
 * 2. onClick возвращает unsubscribe — храним, вызываем в cleanup.
 * 3. НЕ вызывать hide() без парного show() в том же компоненте.
 * 4. useEffect mount-only ([]); handler читает state через useRef, не closure —
 *    иначе stale snapshot и submit пошлёт mount-time empty values.
 * 5. Динамическая смена text/enabled — отдельный effect без re-binding onClick.
 */
export function useMainButton(opts: {
  text: string;
  onClick: () => void;
  enabled?: boolean;
}): void {
  // Refresh handler ref на каждом рендере → onClick callback ниже видит
  // свежий closure, не mount-time.
  const onClickRef = useRef(opts.onClick);
  onClickRef.current = opts.onClick;

  // Mount-only: setParams (показывает кнопку) + bind onClick через ref.
  useEffect(() => {
    if (!mainButton.mount.isAvailable()) return;
    mainButton.setParams({
      text: opts.text,
      isVisible: true,
      isEnabled: opts.enabled ?? true,
    });
    const off = mainButton.onClick(() => onClickRef.current());
    return () => {
      off();
      mainButton.hide();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Динамические text/enabled — без re-bind onClick.
  useEffect(() => {
    if (!mainButton.mount.isAvailable()) return;
    mainButton.setParams({
      text: opts.text,
      isVisible: true,
      isEnabled: opts.enabled ?? true,
    });
  }, [opts.text, opts.enabled]);
}
