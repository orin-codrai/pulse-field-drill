import {
  Button,
  Caption,
  Cell,
  List,
  Section,
  Spinner,
  Title,
} from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useCategories, type Category } from '@/hooks/useCategories';
import { useDuePlanned, usePlanned, type DuePlannedItem } from '@/hooks/usePlanned';
import { useForecast } from '@/hooks/useForecast';
import { useMainButton } from '@/hooks/useMainButton';
import { ApiError, apiPost } from '@/lib/api';
import { formatRub } from '@/lib/format';
import { bump } from '@/lib/refetch';

function formatHorizon(iso: string): string {
  const d = new Date(iso + 'T00:00:00Z');
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}

function catName(cats: Category[] | null, id: number): string {
  return cats?.find((c) => c.id === id)?.name ?? `#${id}`;
}

export const PlanningPage = () => {
  const navigate = useNavigate();
  const raw = useSignal(initData.raw);
  const forecast = useForecast();
  const due = useDuePlanned();
  const planned = usePlanned();
  const { data: categories } = useCategories();

  const [confirming, setConfirming] = useState<number | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  useMainButton({
    text: '+ План',
    onClick: () => navigate('/plan/add'),
  });

  async function confirmDue(item: DuePlannedItem) {
    if (!raw || confirming !== null) return;
    setConfirming(item.planned_operation_id);
    setConfirmError(null);
    try {
      await apiPost(`/api/planned/${item.planned_operation_id}/confirm`, raw, {});
      bump('planned');
      bump('forecast');
      bump('balances');
      bump('transactions');
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setConfirmError(msg);
    } finally {
      setConfirming(null);
    }
  }

  const dueIds = new Set(due.data?.map((d) => d.planned_operation_id) ?? []);
  // «Предстоит» = active planned, у которых ещё нет due-вхождения сегодня/в прошлом.
  const upcoming = (planned.data ?? []).filter(
    (p) => p.status === 'planned' && p.archived_at === null && !dueIds.has(p.id),
  );

  // Подача проектируемого баланса: мягкая для negative, не красная.
  const projected = forecast.data?.projected_available ?? 0;
  const projectedColor = projected >= 0
    ? 'var(--tg-theme-text-color)'
    : 'var(--tg-theme-hint-color)';

  return (
    <Page back={false}>
      <List>
        <Section header="Прогноз к концу периода">
          {forecast.loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {forecast.error && <Cell>Ошибка: {forecast.error}</Cell>}
          {forecast.data && (
            <div style={{ padding: '12px 16px' }}>
              <Title level="1" style={{ color: projectedColor }}>
                {formatRub(projected)}
              </Title>
              <Caption level="1" weight="3" style={{ opacity: 0.6 }}>
                к {formatHorizon(forecast.data.horizon)}
              </Caption>
              <div style={{ marginTop: 8, fontSize: 13, opacity: 0.7 }}>
                Сейчас: {formatRub(forecast.data.available_now)}
                {forecast.data.planned_income > 0 && (
                  <> · доход {formatRub(forecast.data.planned_income)}</>
                )}
                {forecast.data.planned_expense > 0 && (
                  <> · расход {formatRub(forecast.data.planned_expense)}</>
                )}
                {forecast.data.reserved > 0 && (
                  <> · резерв {formatRub(forecast.data.reserved)}</>
                )}
              </div>
            </div>
          )}
        </Section>

        <Section header="К подтверждению">
          {due.loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {due.error && <Cell>Ошибка: {due.error}</Cell>}
          {due.data && due.data.length === 0 && (
            <Cell subtitle="Все подтверждено">Нет ожидающих подтверждения</Cell>
          )}
          {due.data?.map((item) => (
            <Cell
              key={item.planned_operation_id}
              subtitle={`${formatHorizon(item.scheduled_date)} · ${catName(categories, item.category_id)}`}
              after={
                <Button
                  size="s"
                  disabled={confirming === item.planned_operation_id}
                  onClick={() => confirmDue(item)}
                >
                  {confirming === item.planned_operation_id ? '…' : 'Подтвердить'}
                </Button>
              }
            >
              {item.kind === 'income' ? '+ ' : '− '}
              {formatRub(item.amount_minor)}
            </Cell>
          ))}
          {confirmError && (
            <Cell style={{ color: 'var(--tg-theme-destructive-text-color, red)' }}>
              {confirmError}
            </Cell>
          )}
        </Section>

        <Section header="Предстоит">
          {planned.loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {planned.error && <Cell>Ошибка: {planned.error}</Cell>}
          {!planned.loading && upcoming.length === 0 && (
            <Cell subtitle="Добавь план через кнопку внизу">Пока пусто</Cell>
          )}
          {upcoming.map((p) => (
            <Cell
              key={p.id}
              subtitle={`${formatHorizon(p.first_date)} · ${catName(categories, p.category_id)} · ${p.recurrence}`}
              after={<strong>{p.kind === 'income' ? '+' : '−'}{formatRub(p.amount_minor)}</strong>}
            >
              {p.note ?? catName(categories, p.category_id)}
            </Cell>
          ))}
        </Section>
      </List>
    </Page>
  );
};
