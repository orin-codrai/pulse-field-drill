import { Cell, List, Section, Spinner } from '@telegram-apps/telegram-ui';
import { Mail } from 'lucide-react';
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
            <div className="pfd-hero">
              <span className="pfd-num-lg">{formatRub(forecast.reserved)}</span>
              <span className="pfd-hero-meta">из «доступно к трате»</span>
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
            <div
              className="pfd-row"
              key={e.id}
              onClick={() => navigate(`/envelopes/${e.id}`)}
              role="button"
            >
              {e.icon ? (
                <span style={{ fontSize: '20px', lineHeight: 1, width: 20, textAlign: 'center' }}>
                  {e.icon}
                </span>
              ) : (
                <span
                  className="pfd-cat-dot"
                  style={{ background: `var(--pfd-cat-${(e.id % 6) + 1})` }}
                />
              )}
              <div className="pfd-row-stack">
                <span>{e.name}</span>
                <span className="pfd-text-meta">
                  {e.percent !== null
                    ? `${e.percent}% от каждого дохода`
                    : 'Ручной (без авто-скима)'}
                </span>
              </div>
              <span className="pfd-num pfd-text-emphasized">
                {formatRub(e.reserved_minor)}
              </span>
            </div>
          ))}
        </Section>
      </List>
    </Page>
  );
};
