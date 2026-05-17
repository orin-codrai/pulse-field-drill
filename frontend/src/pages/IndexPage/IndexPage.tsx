import { Section, Cell, Image, List } from '@telegram-apps/telegram-ui';
import type { FC } from 'react';

import { Link } from '@/components/Link/Link.tsx';
import { Page } from '@/components/Page.tsx';
import { useMe } from '@/hooks/useMe';

import tonSvg from './ton.svg';

const Greeting: FC = () => {
  const { user, loading, error } = useMe();
  let body: string;
  if (loading) body = '…';
  else if (error) body = `auth error: ${error}`;
  else if (user) body = `Hello, ${user.first_name} (#${user.id})`;
  else body = 'unknown';
  return (
    <Section header="Backend auth">
      <Cell subtitle={body}>/api/me</Cell>
    </Section>
  );
};

export const IndexPage: FC = () => {
  return (
    <Page back={false}>
      <List>
        <Greeting/>
        <Section
          header="Features"
          footer="You can use these pages to learn more about features, provided by Telegram Mini Apps and other useful projects"
        >
          <Link to="/ton-connect">
            <Cell
              before={<Image src={tonSvg} style={{ backgroundColor: '#007AFF' }}/>}
              subtitle="Connect your TON wallet"
            >
              TON Connect
            </Cell>
          </Link>
        </Section>
        <Section
          header="Application Launch Data"
          footer="These pages help developer to learn more about current launch information"
        >
          <Link to="/init-data">
            <Cell subtitle="User data, chat information, technical data">Init Data</Cell>
          </Link>
          <Link to="/launch-params">
            <Cell subtitle="Platform identifier, Mini Apps version, etc.">Launch Parameters</Cell>
          </Link>
          <Link to="/theme-params">
            <Cell subtitle="Telegram application palette information">Theme Parameters</Cell>
          </Link>
        </Section>
      </List>
    </Page>
  );
};
