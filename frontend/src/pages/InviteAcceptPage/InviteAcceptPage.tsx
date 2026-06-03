import { Cell, List, Section, Spinner } from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useInvitePreview } from '@/hooks/useInvites';
import { useMainButton } from '@/hooks/useMainButton';
import { ApiError, apiPost } from '@/lib/api';
import { bump } from '@/lib/refetch';

/**
 * Accept screen для invite deep-link. PIN-F UX: 410/409 подаются мягко
 * (не destructive), с пояснением что делать.
 */
export const InviteAcceptPage = () => {
  const navigate = useNavigate();
  const { token } = useParams<{ token: string }>();
  const raw = useSignal(initData.raw);
  const { data, loading, error } = useInvitePreview(token ?? null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  async function accept() {
    if (submitting || !raw || !token) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await apiPost(`/api/invites/${token}/accept`, raw, {});
      // PIN-B: backend ставит active_workspace_id на shared.
      sessionStorage.removeItem('pendingInviteToken');
      bump('me');
      bump('workspaces');
      bump('balances');
      bump('transactions');
      bump('forecast');
      navigate('/');
    } catch (e) {
      // PIN-F мягкая подача
      if (e instanceof ApiError) {
        sessionStorage.removeItem('pendingInviteToken');  // терминальный fail
        if (e.status === 410) {
          setSubmitError('Срок приглашения истёк. Попроси новое.');
        } else if (e.status === 409 && /capacity|shared workspaces/i.test(e.detail)) {
          setSubmitError('Достигнут максимум участников или своих совместных workspace.');
        } else if (e.status === 409 && /already/i.test(e.detail)) {
          navigate('/');
          return;
        } else if (e.status === 412) {
          setSubmitError('Сначала зарегистрируйся (имя + согласие).');
          navigate('/register');
          return;
        } else {
          setSubmitError(`${e.status} ${e.detail}`);
        }
      } else {
        setSubmitError(String(e));
      }
      setSubmitting(false);
    }
  }

  useMainButton({
    text: submitting ? '…' : 'Принять',
    onClick: accept,
    enabled: !submitting && data?.status === 'pending',
  });

  if (loading) {
    return (
      <Page back={true}>
        <List>
          <Section><Cell before={<Spinner size="s" />}>Загрузка…</Cell></Section>
        </List>
      </Page>
    );
  }
  if (error || !data) {
    return (
      <Page back={true}>
        <List>
          <Section header="Приглашение">
            <Cell>{error ?? 'Приглашение не найдено'}</Cell>
          </Section>
        </List>
      </Page>
    );
  }

  return (
    <Page back={true}>
      <List>
        <Section header="Приглашение">
          <div className="pfd-hero">
            <span style={{ fontSize: 'var(--pfd-text-18)', fontWeight: 600, lineHeight: 'var(--pfd-leading-tight)' }}>
              {data.workspace_name}
            </span>
            <span className="pfd-hero-meta">
              {data.inviter_display_name ?? 'Кто-то'} приглашает тебя в совместный workspace.
            </span>
            {data.status !== 'pending' && (
              <span className="pfd-text-meta">Статус: {data.status}</span>
            )}
          </div>
        </Section>
        {submitError && (
          <Section>
            <Cell><span className="pfd-text-danger">{submitError}</span></Cell>
          </Section>
        )}
      </List>
    </Page>
  );
};
