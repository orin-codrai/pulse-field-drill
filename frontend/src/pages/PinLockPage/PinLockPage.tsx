import { Delete } from 'lucide-react';
import type { FC } from 'react';
import { useCallback, useEffect, useState } from 'react';

import { clearPin, getAttempts, MAX_ATTEMPTS, PIN_LENGTH, pinExists, verifyPin } from '@/lib/pin';

interface Props {
  onUnlock: () => void;
}

const KEYS: Array<string | 'del' | null> = [
  '1', '2', '3',
  '4', '5', '6',
  '7', '8', '9',
  null, '0', 'del',
];

export const PinLockPage: FC<Props> = ({ onUnlock }) => {
  const [input, setInput] = useState('');
  const [errorFlash, setErrorFlash] = useState(false);
  const [attempts, setAttempts] = useState(() => getAttempts());

  const check = useCallback(async (pin: string) => {
    const ok = await verifyPin(pin);
    if (ok) {
      onUnlock();
      return;
    }
    // После verifyPin: либо attempts++ либо clearPin (если хватило).
    if (!pinExists()) {
      // Soft reset triggered — PinGuard subscribePinChanged переключит на
      // setup автоматически. Здесь ничего не делаем кроме clear input'a.
      setInput('');
      return;
    }
    setAttempts(getAttempts());
    setErrorFlash(true);
    setInput('');
    setTimeout(() => setErrorFlash(false), 600);
  }, [onUnlock]);

  function press(k: string | 'del') {
    if (k === 'del') {
      setInput((s) => s.slice(0, -1));
      return;
    }
    setInput((s) => (s.length < PIN_LENGTH ? s + k : s));
  }

  useEffect(() => {
    if (input.length === PIN_LENGTH) {
      check(input);
    }
  }, [input, check]);

  function forgotPin() {
    if (confirm('Удалить PIN и настроить заново? Данные на сервере не пострадают.')) {
      clearPin();  // → notify → PinGuard видит pinExists=false → unlocked → setup.
    }
  }

  const remaining = MAX_ATTEMPTS - attempts;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'var(--tg-theme-bg-color)',
        color: 'var(--tg-theme-text-color)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--pfd-space-6)',
        padding: 'var(--pfd-space-6)',
        zIndex: 1000,
      }}
    >
      <div className="pfd-text-meta" style={{ textAlign: 'center' }}>
        Введите PIN-код
      </div>

      <div style={{ display: 'flex', gap: 'var(--pfd-space-3)' }}>
        {Array.from({ length: PIN_LENGTH }).map((_, i) => {
          const filled = i < input.length;
          return (
            <span
              key={i}
              style={{
                width: 16,
                height: 16,
                borderRadius: '50%',
                background: filled
                  ? (errorFlash ? 'var(--pfd-color-danger)' : 'var(--tg-theme-text-color)')
                  : 'transparent',
                border: `2px solid ${errorFlash ? 'var(--pfd-color-danger)' : 'var(--tg-theme-hint-color)'}`,
                transition: 'background 0.1s, border-color 0.1s',
              }}
            />
          );
        })}
      </div>

      {attempts > 0 && (
        <div className="pfd-text-meta pfd-text-danger" style={{ textAlign: 'center' }}>
          Осталось попыток: {remaining}
        </div>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 72px)',
          gap: 'var(--pfd-space-3)',
        }}
      >
        {KEYS.map((k, i) => {
          if (k === null) return <div key={i} />;
          const isDel = k === 'del';
          return (
            <button
              key={i}
              type="button"
              onClick={() => press(k)}
              style={{
                width: 72,
                height: 72,
                borderRadius: '50%',
                border: 'none',
                background: 'var(--tg-theme-secondary-bg-color)',
                color: 'var(--tg-theme-text-color)',
                fontSize: 'var(--pfd-text-24)',
                fontFamily: 'var(--pfd-font-mono)',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              {isDel ? <Delete size={24} strokeWidth={2} /> : k}
            </button>
          );
        })}
      </div>

      <button
        type="button"
        onClick={forgotPin}
        style={{
          background: 'transparent',
          border: 'none',
          color: 'var(--tg-theme-link-color)',
          fontSize: 'var(--pfd-text-13)',
          cursor: 'pointer',
          padding: 'var(--pfd-space-2)',
        }}
      >
        Забыл PIN — настроить заново
      </button>
    </div>
  );
};
