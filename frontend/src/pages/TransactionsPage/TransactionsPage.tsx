import { List, Section, Cell } from '@telegram-apps/telegram-ui';
import type { FC } from 'react';

import { Page } from '@/components/Page.tsx';

export const TransactionsPage: FC = () => {
  // Полное тело в commit 9 (Phase 2).
  return (
    <Page back={false}>
      <List>
        <Section header="Список транзакций">
          <Cell>скоро</Cell>
        </Section>
      </List>
    </Page>
  );
};
