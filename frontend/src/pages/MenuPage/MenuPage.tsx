import { Cell, List, Section, Spinner } from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { Mail } from 'lucide-react';
import type { FC } from 'react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useMe } from '@/hooks/useMe';
import { useWorkspaces } from '@/hooks/useWorkspaces';
import { ApiError, apiPatch, apiPost } from '@/lib/api';
import { bump } from '@/lib/refetch';

const BOT_USERNAME = import.meta.env.VITE_BOT_USERNAME ?? 'pulse_drill_bot';

export const MenuPage: FC = () => {
  const navigate = useNavigate();
  const raw = useSignal(initData.raw);
  const { user } = useMe();
  const { data: workspaces, loading } = useWorkspaces();
  const [switching, setSwitching] = useState(false);
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const activeWs = workspaces?.find((w) => w.id === user?.active_workspace_id);

  async function switchTo(workspaceId: number) {
    if (switching || !raw || workspaceId === user?.active_workspace_id) return;
    setSwitching(true);
    try {
      await apiPatch('/api/workspaces/active', raw, { workspace_id: workspaceId });
      bump('me');
      bump('balances');
      bump('transactions');
      bump('forecast');
      bump('envelopes');
      bump('planned');
      bump('audit');
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setError(msg);
    } finally {
      setSwitching(false);
    }
  }

  async function createInvite() {
    if (!raw || !activeWs || activeWs.kind !== 'shared') return;
    setError(null);
    try {
      const inv = await apiPost<{ token: string }>(
        `/api/workspaces/${activeWs.id}/invites`, raw, {},
      );
      bump('invites');
      const url = `https://t.me/${BOT_USERNAME}?startapp=invite_${inv.token}`;
      setInviteUrl(url);
      try {
        await navigator.clipboard?.writeText(url);
      } catch {
        // clipboard может быть заблокирован → ссылку показываем в UI ниже
      }
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setError(msg);
    }
  }

  async function softDelete() {
    if (!raw) return;
    if (!confirm('Удалить аккаунт? Личные данные удалятся через 30 дней. Совместные останутся у партнёра.')) return;
    try {
      await apiPost('/api/me/delete', raw, {});
      bump('me');
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setError(msg);
    }
  }

  return (
    <Page back={false}>
      <List>
        <Section header="Рабочая область">
          {loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {workspaces?.map((ws) => (
            <Cell
              key={ws.id}
              after={ws.id === user?.active_workspace_id ? '✓' : ''}
              subtitle={ws.kind === 'shared' ? 'Совместный' : 'Личный'}
              onClick={() => switchTo(ws.id)}
            >
              {ws.name}
            </Cell>
          ))}
          <Cell onClick={() => navigate('/workspaces/new')}>
            + Создать совместный
          </Cell>
        </Section>

        {activeWs?.kind === 'shared' && (
          <Section header="Совместный" footer={inviteUrl ? 'Ссылка скопирована в буфер обмена.' : 'Пригласи партнёра по ссылке.'}>
            <Cell onClick={createInvite}>+ Пригласить</Cell>
            {inviteUrl && (
              <Cell multiline>
                <span className="pfd-text-meta" style={{ wordBreak: 'break-all' }}>
                  {inviteUrl}
                </span>
              </Cell>
            )}
            <Cell onClick={() => navigate('/audit')}>История изменений</Cell>
          </Section>
        )}

        <Section header="Управление">
          <Cell
            before={<Mail size={20} className="pfd-text-neutral" />}
            onClick={() => navigate('/envelopes')}
          >
            Конверты
          </Cell>
          <Cell onClick={softDelete}>
            <span className="pfd-text-danger">Удалить аккаунт</span>
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
