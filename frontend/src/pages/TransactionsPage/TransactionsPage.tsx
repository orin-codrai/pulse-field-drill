import { Cell, List, Section, Spinner } from '@telegram-apps/telegram-ui';
import type { FC } from 'react';
import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useAccounts } from '@/hooks/useAccounts';
import { useCategories } from '@/hooks/useCategories';
import { useMainButton } from '@/hooks/useMainButton';
import { useTransactions, type Transaction, type TransactionKind } from '@/hooks/useTransactions';
import { formatDate, formatRub } from '@/lib/format';

const KIND_STYLE: Record<TransactionKind, { sign: string; cls: string }> = {
  expense:    { sign: '−', cls: 'pfd-text-danger' },
  income:     { sign: '+', cls: 'pfd-text-success' },
  transfer:   { sign: '↔', cls: 'pfd-text-neutral' },
  adjustment: { sign: '±', cls: 'pfd-text-warning' },
};

const KIND_LABEL: Record<TransactionKind, string> = {
  expense: 'Расход',
  income: 'Доход',
  transfer: 'Перевод',
  adjustment: 'Корректировка',
};

export const TransactionsPage: FC = () => {
  const navigate = useNavigate();
  const { data: txs, loading, error } = useTransactions();
  const { data: cats } = useCategories();
  const { data: accs } = useAccounts();

  useMainButton({
    text: '+ Транзакция',
    onClick: () => navigate('/add'),
  });

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

  function dotColor(tx: Transaction): string {
    if (tx.category_id === null) return 'var(--pfd-color-neutral)';
    return `var(--pfd-cat-${(tx.category_id % 6) + 1})`;
  }

  return (
    <Page back={false}>
      <List>
        <Section header="Список транзакций">
          {loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {error && <Cell>Ошибка: {error}</Cell>}
          {txs && txs.length === 0 && <Cell>Пусто. Добавь первую через кнопку «+ Транзакция».</Cell>}
          {txs?.map((tx) => {
            const k = KIND_STYLE[tx.kind];
            return (
              <div className="pfd-row" key={tx.id}>
                <span className="pfd-cat-dot" style={{ background: dotColor(tx) }} />
                <div className="pfd-row-stack">
                  <span>{renderTitle(tx)}</span>
                  <span className="pfd-text-meta">{renderSubtitle(tx)}</span>
                </div>
                <span className={`pfd-num pfd-text-emphasized ${k.cls}`}>
                  {k.sign} {formatRub(tx.amount_minor)}
                </span>
              </div>
            );
          })}
        </Section>
      </List>
    </Page>
  );
};
