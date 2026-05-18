import { List, Section, Cell, Spinner, Title, Caption } from '@telegram-apps/telegram-ui';
import type { FC } from 'react';
import { useNavigate } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useBalances } from '@/hooks/useBalances';
import { useMainButton } from '@/hooks/useMainButton';
import { formatRub } from '@/lib/format';

const ACCOUNT_ICON: Record<string, string> = {
  card: '💳',
  cash: '💵',
  savings: '🏦',
  debt: '🤝',
  credit: '💳',
};

export const BalancesPage: FC = () => {
  const navigate = useNavigate();
  const { data, loading, error } = useBalances();

  useMainButton({
    text: '+ Транзакция',
    onClick: () => navigate('/add'),
  });

  const total = data?.reduce((acc, a) => acc + a.balance_minor, 0) ?? 0;

  return (
    <Page back={false}>
      <List>
        <Section header="Общий баланс">
          <div style={{ padding: '12px 16px' }}>
            <Title level="1">{formatRub(total)}</Title>
            {data && (
              <Caption level="1" weight="3" style={{ opacity: 0.6 }}>
                {data.length} {data.length === 1 ? 'счёт' : 'счетов'}
              </Caption>
            )}
          </div>
        </Section>

        <Section header="Счета">
          {loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {error && <Cell>Ошибка: {error}</Cell>}
          {data && data.length === 0 && <Cell>Нет счетов</Cell>}
          {data?.map((a) => (
            <Cell
              key={a.account_id}
              before={<span style={{ fontSize: 28 }}>{ACCOUNT_ICON[a.type] ?? '💼'}</span>}
              subtitle={a.type}
              after={<strong>{formatRub(a.balance_minor)}</strong>}
            >
              {a.name}
            </Cell>
          ))}
        </Section>
      </List>
    </Page>
  );
};
