import { List, Section, Cell } from '@telegram-apps/telegram-ui';
import type { FC } from 'react';
import { useNavigate } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';

export const MenuPage: FC = () => {
  const navigate = useNavigate();
  return (
    <Page back={false}>
      <List>
        <Section header="Меню">
          <Cell
            before={<span style={{ fontSize: 22 }}>✉️</span>}
            subtitle="Pay-yourself-first: процент дохода авто-в конверт"
            onClick={() => navigate('/envelopes')}
          >
            Конверты
          </Cell>
        </Section>
      </List>
    </Page>
  );
};
