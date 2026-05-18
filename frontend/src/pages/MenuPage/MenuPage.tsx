import { List, Section, Cell } from '@telegram-apps/telegram-ui';
import type { FC } from 'react';

import { Page } from '@/components/Page.tsx';

export const MenuPage: FC = () => {
  return (
    <Page back={false}>
      <List>
        <Section
          header="Меню"
          footer="В Phase 4 здесь появятся: цели, бюджеты, управление счетами и категориями."
        >
          <Cell>скоро</Cell>
        </Section>
      </List>
    </Page>
  );
};
