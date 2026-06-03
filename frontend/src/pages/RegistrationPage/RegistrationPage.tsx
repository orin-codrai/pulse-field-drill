import { Cell, Input, List, Section } from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { useState } from 'react';

import { Page } from '@/components/Page.tsx';
import { useMainButton } from '@/hooks/useMainButton';
import { ApiError, apiPost } from '@/lib/api';
import { bump } from '@/lib/refetch';

/**
 * P7 registration form. C16-3 + C17-1: НЕ делаем navigate сами; только bump('me').
 * App.tsx useEffect видит обновлённое registration_required=false и разруливает
 * целевой path (pending invite или /). Single navigate без flicker'a главной.
 *
 * MainButton disabled во время in-flight POST (C17-1: latency POST + refetch =
 * окно неопределённости, юзер не понимает обработался ли клик).
 */
export const RegistrationPage = () => {
  const raw = useSignal(initData.raw);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [consent, setConsent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (submitting) return;
    if (!name.trim()) { setError('Имя обязательно'); return; }
    if (!consent) { setError('Подтверди согласие'); return; }
    if (!raw) { setError('Нет initData'); return; }
    setSubmitting(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        display_name: name.trim(),
        consent: true,
      };
      if (email.trim()) body.email = email.trim();
      await apiPost('/api/me/register', raw, body);
      bump('me');
      // НЕ навигируем сами — App.tsx redirect logic подхватит me.refetch
      // и пошлёт на pending invite или /.
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setError(msg);
      setSubmitting(false);
    }
  }

  useMainButton({
    text: submitting ? 'Сохранение…' : 'Продолжить',
    onClick: submit,
    enabled: !submitting,
  });

  return (
    <Page back={false}>
      <List>
        <Section
          header="Регистрация"
          footer="Имя видно партнёру в совместном workspace. Email — для compliance, не используется для входа."
        >
          <Input
            header="Имя"
            placeholder="Как тебя зовут"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Input
            header="Email (опционально)"
            type="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Section>

        <Section>
          <Cell
            Component="label"
            before={
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
                style={{ width: 22, height: 22 }}
              />
            }
            multiline
          >
            Я согласен на обработку имени и email для работы приложения.
          </Cell>
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
