import { Cell, List, Section, Spinner } from '@telegram-apps/telegram-ui';
import { ChevronRight } from 'lucide-react';
import type { FC } from 'react';
import { useNavigate } from 'react-router-dom';

import { AccountIcon } from '@/components/icons/AccountIcon';
import { Page } from '@/components/Page.tsx';
import { useBalances } from '@/hooks/useBalances';
import { useEnvelopes } from '@/hooks/useEnvelopes';
import { useForecast } from '@/hooks/useForecast';
import { useMainButton } from '@/hooks/useMainButton';
import { formatRub } from '@/lib/format';

export const BalancesPage: FC = () => {
  const navigate = useNavigate();
  const { data, loading, error } = useBalances();
  const { data: forecast } = useForecast();
  const { data: envelopes } = useEnvelopes();

  useMainButton({
    text: '+ Транзакция',
    onClick: () => navigate('/add'),
  });

  const total = data?.reduce((acc, a) => acc + a.balance_minor, 0) ?? 0;
  const reserved = forecast?.reserved ?? 0;
  const available = total - reserved;

  // Top-2 активных конвертов по reserved_minor (archived backend уже отфильтровал
  // в active-выдаче — см. B2 query-filter; на всякий случай ещё раз тут).
  const topEnvelopes = (envelopes ?? [])
    .filter((e) => e.archived_at === null)
    .slice()
    .sort((a, b) => b.reserved_minor - a.reserved_minor)
    .slice(0, 2);

  return (
    <Page back={false}>
      <List>
        <Section header="Доступно к трате">
          <div className="pfd-hero">
            <span className="pfd-num-lg">{formatRub(available)}</span>
            <span className="pfd-hero-meta">
              из <span className="pfd-num">{formatRub(total)}</span>
              {reserved > 0 && (
                <> · в конвертах <span className="pfd-num">{formatRub(reserved)}</span></>
              )}
            </span>
          </div>
        </Section>

        <Section header="Счета">
          {loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {error && <Cell>Ошибка: {error}</Cell>}
          {data && data.length === 0 && <Cell>Нет счетов</Cell>}
          {data?.map((a) => (
            <div className="pfd-row" key={a.account_id}>
              <AccountIcon type={a.type} size={20} className="pfd-text-neutral" />
              <span className="pfd-row-title">{a.name}</span>
              <span className="pfd-num pfd-text-emphasized">
                {formatRub(a.balance_minor)}
              </span>
            </div>
          ))}
        </Section>

        {topEnvelopes.length > 0 && (
          <Section header="Конверты">
            {topEnvelopes.map((e, i) => (
              <div
                className="pfd-row"
                key={e.id}
                onClick={() => navigate(`/envelopes/${e.id}`)}
                role="button"
              >
                <span
                  className="pfd-cat-dot"
                  style={{ background: `var(--pfd-cat-${(i % 6) + 1})` }}
                />
                <span className="pfd-row-title">{e.name}</span>
                {e.percent !== null && (
                  <span className="pfd-text-meta">{e.percent}%</span>
                )}
                <span className="pfd-num pfd-text-emphasized">
                  {formatRub(e.reserved_minor)}
                </span>
                <ChevronRight size={16} className="pfd-text-neutral" />
              </div>
            ))}
            <Cell onClick={() => navigate('/envelopes')}>Все конверты</Cell>
          </Section>
        )}
      </List>
    </Page>
  );
};
