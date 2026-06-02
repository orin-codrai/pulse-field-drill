import {
  Caption,
  Cell,
  List,
  Section,
  Spinner,
  Title,
} from '@telegram-apps/telegram-ui';
import type { FC } from 'react';
import { useNavigate } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useEnvelopes } from '@/hooks/useEnvelopes';
import { useForecast } from '@/hooks/useForecast';
import { useMainButton } from '@/hooks/useMainButton';
import { formatRub } from '@/lib/format';

export const EnvelopesPage: FC = () => {
  const navigate = useNavigate();
  const { data: envelopes, loading, error } = useEnvelopes();
  const { data: forecast } = useForecast();

  useMainButton({
    text: '+ Конверт',
    onClick: () => navigate('/envelopes/add'),
  });

  return (
    <Page back={true}>
      <List>
        {forecast && (
          <Section header="Зарезервировано всего">
            <div style={{ padding: '12px 16px' }}>
              <Title level="2">{formatRub(forecast.reserved)}</Title>
              <Caption level="1" weight="3" style={{ opacity: 0.6 }}>
                из «доступно к трате»
              </Caption>
            </div>
          </Section>
        )}

        <Section header="Конверты">
          {loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {error && <Cell>Ошибка: {error}</Cell>}
          {envelopes && envelopes.length === 0 && (
            <Cell subtitle="Добавь через кнопку внизу">Конвертов пока нет</Cell>
          )}
          {envelopes?.map((e) => (
            <Cell
              key={e.id}
              before={<span style={{ fontSize: 28 }}>{e.icon ?? '✉️'}</span>}
              subtitle={
                e.percent !== null
                  ? `${e.percent}% от каждого дохода`
                  : 'Ручной (без авто-скима)'
              }
              after={<strong>{formatRub(e.reserved_minor)}</strong>}
              onClick={() => navigate(`/envelopes/${e.id}`)}
            >
              {e.name}
            </Cell>
          ))}
        </Section>
      </List>
    </Page>
  );
};
