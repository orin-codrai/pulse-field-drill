import { Cell, Input, List, Section } from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useMainButton } from '@/hooks/useMainButton';
import { ApiError, apiPost } from '@/lib/api';
import { bump } from '@/lib/refetch';

interface WorkspaceCreated {
  id: number;
  name: string;
  kind: string;
}

export const AddSharedWorkspacePage = () => {
  const navigate = useNavigate();
  const raw = useSignal(initData.raw);
  const [name, setName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (submitting || !raw) return;
    if (!name.trim()) { setError('Название обязательно'); return; }
    setSubmitting(true);
    setError(null);
    try {
      const ws = await apiPost<WorkspaceCreated>(
        '/api/workspaces', raw, { name: name.trim() },
      );
      bump('workspaces');
      bump('me');
      // Переключаемся на новый workspace.
      await apiPost('/api/workspaces/active', raw, { workspace_id: ws.id });
      bump('balances');
      bump('transactions');
      bump('forecast');
      bump('envelopes');
      navigate('/');
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setError(msg);
      setSubmitting(false);
    }
  }

  useMainButton({
    text: submitting ? 'Создание…' : 'Создать',
    onClick: submit,
    enabled: !submitting,
  });

  return (
    <Page back={true}>
      <List>
        <Section
          header="Совместный workspace"
          footer="После создания пригласи партнёра через ссылку (на следующем шаге)."
        >
          <Input
            header="Название"
            placeholder="Семейный / Квартира"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Section>
        {error && (
          <Section>
            <Cell style={{ color: 'var(--tg-theme-destructive-text-color, red)' }}>
              {error}
            </Cell>
          </Section>
        )}
      </List>
    </Page>
  );
};
