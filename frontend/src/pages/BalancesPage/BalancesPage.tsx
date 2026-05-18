import { List, Section, Cell } from '@telegram-apps/telegram-ui';
import type { FC } from 'react';

import { Page } from '@/components/Page.tsx';

export const BalancesPage: FC = () => {
  // Полное тело в commit 8 (Phase 2).
  return (
    <Page back={false}>
      <List>
        <Section header="Балансы">
          <Cell>скоро</Cell>
        </Section>
      </List>
    </Page>
  );
};
