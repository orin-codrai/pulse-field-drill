import { Button, Cell, List, Section, Title } from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { useState } from 'react';

import { Page } from '@/components/Page.tsx';
import { useMe } from '@/hooks/useMe';
import { ApiError, apiPost } from '@/lib/api';
import { bump } from '@/lib/refetch';

export const RestorePage = () => {
  const raw = useSignal(initData.raw);
  const { user } = useMe();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function restore() {
    if (submitting || !raw) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiPost('/api/me/restore', raw, {});
      bump('me');  // App.tsx redirect перенесёт на /
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setError(msg);
      setSubmitting(false);
    }
  }

  const deletedDate = user?.deleted_at ? new Date(user.deleted_at) : null;
  const daysSinceDelete = deletedDate
    ? Math.floor((Date.now() - deletedDate.getTime()) / (24 * 3600 * 1000))
    : 0;
  const daysLeft = Math.max(0, 30 - daysSinceDelete);

  return (
    <Page back={false}>
      <List>
        <Section header="Аккаунт удалён">
          <div style={{ padding: '12px 16px' }}>
            <Title level="2">Восстановить?</Title>
            <div style={{ marginTop: 8, opacity: 0.7 }}>
              Через {daysLeft} {daysLeft === 1 ? 'день' : 'дней'} данные будут удалены безвозвратно.
              Совместные workspace'ы не вернутся — попроси партнёра пригласить заново.
            </div>
          </div>
        </Section>

        <Section>
          <div style={{ padding: '0 16px 12px' }}>
            <Button stretched disabled={submitting} onClick={restore}>
              {submitting ? '…' : 'Восстановить'}
            </Button>
          </div>
          {error && (
            <Cell style={{ color: 'var(--tg-theme-destructive-text-color, red)' }}>
              {error}
            </Cell>
          )}
        </Section>
      </List>
    </Page>
  );
};
