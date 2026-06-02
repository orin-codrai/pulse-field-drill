import { Cell, List, Section, Spinner } from '@telegram-apps/telegram-ui';

import { Page } from '@/components/Page.tsx';
import { useAudit } from '@/hooks/useAudit';
import { useMe } from '@/hooks/useMe';

const ENTITY_LABEL: Record<string, string> = {
  transaction: 'транзакция',
  account: 'счёт',
};

const ACTION_LABEL: Record<string, string> = {
  create: 'создал(а)',
  update: 'изменил(а)',
  delete: 'удалил(а)',
};

function formatRelative(iso: string): string {
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'только что';
  if (diffMin < 60) return `${diffMin} мин назад`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH} ч назад`;
  const diffD = Math.floor(diffH / 24);
  if (diffD < 7) return `${diffD} дн назад`;
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}

export const AuditHistoryPage = () => {
  const { user } = useMe();
  const { data, loading, error } = useAudit();

  return (
    <Page back={true}>
      <List>
        <Section
          header="История изменений"
          footer="Кто и когда менял данные в этом совместном workspace."
        >
          {loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {error && <Cell>Ошибка: {error}</Cell>}
          {data && data.length === 0 && (
            <Cell subtitle="Пока никто ничего не менял">Пусто</Cell>
          )}
          {data?.map((e) => {
            // MF15-3 «вы»-маркер через internal_id (единственное место использования).
            const isMe = e.actor_user_id === user?.internal_id;
            const actor = e.actor_display_name ?? 'бывший участник';
            return (
              <Cell
                key={e.id}
                subtitle={`${formatRelative(e.created_at)}`}
              >
                {isMe ? 'Вы' : actor}
                {' '}{ACTION_LABEL[e.action]}
                {' '}{ENTITY_LABEL[e.entity_type]}
              </Cell>
            );
          })}
        </Section>
      </List>
    </Page>
  );
};
