import { Button, Cell, List, Section } from '@telegram-apps/telegram-ui';
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
          <div className="pfd-hero">
            <span className="pfd-num-lg">{daysLeft}</span>
            <span className="pfd-hero-meta">
              {daysLeft === 1 ? 'день' : 'дней'} до безвозвратного удаления
            </span>
            <span className="pfd-text-meta">
              Совместные workspace'ы не вернутся — попроси партнёра пригласить заново.
            </span>
          </div>
        </Section>

        <Section>
          <div style={{ padding: '0 16px 12px' }}>
            <Button stretched disabled={submitting} onClick={restore}>
              {submitting ? '…' : 'Восстановить'}
            </Button>
          </div>
          {error && (
            <Cell><span className="pfd-text-danger">{error}</span></Cell>
          )}
        </Section>
      </List>
    </Page>
  );
};
