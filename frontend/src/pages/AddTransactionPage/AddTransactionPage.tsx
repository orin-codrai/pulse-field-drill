import { List, Section, Cell } from '@telegram-apps/telegram-ui';
import type { FC } from 'react';

import { Page } from '@/components/Page.tsx';

export const AddTransactionPage: FC = () => {
  // Полное тело + MainButton wiring в commit 10 (Phase 2).
  return (
    <Page back={true}>
      <List>
        <Section header="Новая транзакция">
          <Cell>скоро</Cell>
        </Section>
      </List>
    </Page>
  );
};
