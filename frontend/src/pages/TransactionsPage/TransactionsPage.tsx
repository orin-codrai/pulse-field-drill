import { List, Section, Cell, Spinner } from '@telegram-apps/telegram-ui';
import type { FC } from 'react';
import { useMemo } from 'react';

import { Page } from '@/components/Page.tsx';
import { useAccounts } from '@/hooks/useAccounts';
import { useCategories } from '@/hooks/useCategories';
import { useTransactions, type Transaction } from '@/hooks/useTransactions';
import { formatDate, formatRub } from '@/lib/format';

const KIND_SIGN: Record<Transaction['kind'], string> = {
  expense: '−',
  income: '+',
  transfer: '↔',
  adjustment: '±',
};

const KIND_LABEL: Record<Transaction['kind'], string> = {
  expense: 'Расход',
  income: 'Доход',
  transfer: 'Перевод',
  adjustment: 'Корректировка',
};

export const TransactionsPage: FC = () => {
  const { data: txs, loading, error } = useTransactions();
  const { data: cats } = useCategories();
  const { data: accs } = useAccounts();

  const catName = useMemo(() => {
    const m = new Map<number, string>();
    cats?.forEach((c) => m.set(c.id, c.name));
    return m;
  }, [cats]);

  const accName = useMemo(() => {
    const m = new Map<number, string>();
    accs?.forEach((a) => m.set(a.id, a.name));
    return m;
  }, [accs]);

  function renderTitle(tx: Transaction): string {
    if (tx.category_id !== null) {
      return catName.get(tx.category_id) ?? KIND_LABEL[tx.kind];
    }
    if (tx.kind === 'transfer') {
      const from = tx.from_account_id ? accName.get(tx.from_account_id) ?? '?' : '?';
      const to = tx.to_account_id ? accName.get(tx.to_account_id) ?? '?' : '?';
      return `${from} → ${to}`;
    }
    return KIND_LABEL[tx.kind];
  }

  function renderSubtitle(tx: Transaction): string {
    const date = formatDate(tx.occurred_at);
    if (tx.note) return `${date} · ${tx.note}`;
    return date;
  }

  return (
    <Page back={false}>
      <List>
        <Section header="Список транзакций">
          {loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {error && <Cell>Ошибка: {error}</Cell>}
          {txs && txs.length === 0 && <Cell>Пусто. Добавь первую через кнопку «+ Транзакция».</Cell>}
          {txs?.map((tx) => (
            <Cell
              key={tx.id}
              subtitle={renderSubtitle(tx)}
              after={
                <strong>
                  {KIND_SIGN[tx.kind]} {formatRub(tx.amount_minor)}
                </strong>
              }
            >
              {renderTitle(tx)}
            </Cell>
          ))}
        </Section>
      </List>
    </Page>
  );
};
