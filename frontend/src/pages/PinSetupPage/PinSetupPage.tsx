import { Cell, List, Section } from '@telegram-apps/telegram-ui';
import { Delete } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { PIN_LENGTH, pinExists, setPin, verifyPin } from '@/lib/pin';

type Step = 'verify-old' | 'new' | 'confirm';

const KEYS: Array<string | 'del' | null> = [
  '1', '2', '3',
  '4', '5', '6',
  '7', '8', '9',
  null, '0', 'del',
];

/**
 * /pin/setup — создание / смена PIN'a.
 * Query `mode=change` → требует ввод текущего PIN'а сначала.
 */
export const PinSetupPage = () => {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const isChange = params.get('mode') === 'change' && pinExists();

  const [step, setStep] = useState<Step>(isChange ? 'verify-old' : 'new');
  const [input, setInput] = useState('');
  const [newPin, setNewPin] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const title = step === 'verify-old'
    ? 'Введите текущий PIN'
    : step === 'new'
      ? 'Новый PIN-код'
      : 'Повторите PIN-код';

  function press(k: string | 'del') {
    if (k === 'del') {
      setInput((s) => s.slice(0, -1));
      return;
    }
    setInput((s) => (s.length < PIN_LENGTH ? s + k : s));
  }

  useEffect(() => {
    if (input.length !== PIN_LENGTH) return;
    if (step === 'verify-old') {
      verifyPin(input).then((ok) => {
        if (ok) {
          setStep('new');
          setInput('');
          setError(null);
        } else {
          setError('Неверный PIN');
          setInput('');
        }
      });
    } else if (step === 'new') {
      setNewPin(input);
      setStep('confirm');
      setInput('');
      setError(null);
    } else {
      // confirm
      if (input === newPin) {
        setPin(input).then(() => {
          // notifyPinChanged → usePin → unlocked → main app.
          navigate(-1);
        });
      } else {
        setError('PIN-коды не совпадают, попробуй заново');
        setNewPin(null);
        setStep('new');
        setInput('');
      }
    }
  }, [input, step, newPin, navigate]);

  return (
    <Page back={true}>
      <List>
        <Section header={title}>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 'var(--pfd-space-4)',
              padding: 'var(--pfd-space-6) var(--pfd-space-4)',
            }}
          >
            <div style={{ display: 'flex', gap: 'var(--pfd-space-3)' }}>
              {Array.from({ length: PIN_LENGTH }).map((_, i) => (
                <span
                  key={i}
                  style={{
                    width: 16,
                    height: 16,
                    borderRadius: '50%',
                    background: i < input.length
                      ? 'var(--tg-theme-text-color)'
                      : 'transparent',
                    border: '2px solid var(--tg-theme-hint-color)',
                  }}
                />
              ))}
            </div>

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
          </div>
        </Section>

        {error && (
          <Section>
            <Cell><span className="pfd-text-danger">{error}</span></Cell>
          </Section>
        )}
      </List>
    </Page>
  );
};
