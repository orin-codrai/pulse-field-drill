import {
  Button,
  Cell,
  List,
  Section,
  Spinner,
} from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { Clock } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useCategories, type Category } from '@/hooks/useCategories';
import { useDuePlanned, usePlanned, type DuePlannedItem, type PlannedKind } from '@/hooks/usePlanned';
import { useForecast } from '@/hooks/useForecast';
import { useMainButton } from '@/hooks/useMainButton';
import { ApiError, apiPost } from '@/lib/api';
import { formatRub } from '@/lib/format';
import { bump } from '@/lib/refetch';

const KIND_STYLE: Record<PlannedKind, { sign: string; cls: string }> = {
  income:  { sign: '+', cls: 'pfd-text-success' },
  expense: { sign: '−', cls: 'pfd-text-danger' },
};

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

  const projected = forecast.data?.projected_available ?? 0;
  // Мягкая подача negative — hint-color, не destructive.
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
            <div className="pfd-hero">
              <span className="pfd-num-lg" style={{ color: projectedColor }}>
                {formatRub(projected)}
              </span>
              <span className="pfd-hero-meta">
                к {formatHorizon(forecast.data.horizon)}
              </span>
              <span className="pfd-text-meta">
                Сейчас: <span className="pfd-num">{formatRub(forecast.data.available_now)}</span>
                {forecast.data.planned_income > 0 && (
                  <> · доход <span className="pfd-num">{formatRub(forecast.data.planned_income)}</span></>
                )}
                {forecast.data.planned_expense > 0 && (
                  <> · расход <span className="pfd-num">{formatRub(forecast.data.planned_expense)}</span></>
                )}
                {forecast.data.planned_skim > 0 && (
                  <> · в конверт <span className="pfd-num">{formatRub(forecast.data.planned_skim)}</span></>
                )}
                {forecast.data.reserved > 0 && (
                  <> · резерв <span className="pfd-num">{formatRub(forecast.data.reserved)}</span></>
                )}
              </span>
            </div>
          )}
        </Section>

        <Section header="К подтверждению">
          {due.loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {due.error && <Cell>Ошибка: {due.error}</Cell>}
          {due.data && due.data.length === 0 && (
            <Cell subtitle="Все подтверждено">Нет ожидающих подтверждения</Cell>
          )}
          {due.data?.map((item) => {
            const k = KIND_STYLE[item.kind];
            return (
              <div className="pfd-row" key={item.planned_operation_id}>
                <Clock size={16} className="pfd-text-warning" />
                <div className="pfd-row-stack">
                  <span className={`pfd-num pfd-text-emphasized ${k.cls}`}>
                    {k.sign} {formatRub(item.amount_minor)}
                  </span>
                  <span className="pfd-text-meta">
                    {formatHorizon(item.scheduled_date)} · {catName(categories, item.category_id)}
                  </span>
                </div>
                <Button
                  size="s"
                  disabled={confirming === item.planned_operation_id}
                  onClick={() => confirmDue(item)}
                >
                  {confirming === item.planned_operation_id ? '…' : 'Подтвердить'}
                </Button>
              </div>
            );
          })}
          {confirmError && (
            <Cell><span className="pfd-text-danger">{confirmError}</span></Cell>
          )}
        </Section>

        <Section header="Предстоит">
          {planned.loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {planned.error && <Cell>Ошибка: {planned.error}</Cell>}
          {!planned.loading && upcoming.length === 0 && (
            <Cell subtitle="Добавь план через кнопку внизу">Пока пусто</Cell>
          )}
          {upcoming.map((p) => {
            const k = KIND_STYLE[p.kind];
            return (
              <div
                className="pfd-row"
                key={p.id}
                onClick={() => navigate(`/plan/${p.id}`)}
                role="button"
              >
                <span
                  className="pfd-cat-dot"
                  style={{ background: `var(--pfd-cat-${(p.category_id % 6) + 1})` }}
                />
                <div className="pfd-row-stack">
                  <span>{p.note ?? catName(categories, p.category_id)}</span>
                  <span className="pfd-text-meta">
                    {formatHorizon(p.first_date)} · {catName(categories, p.category_id)} · {p.recurrence}
                  </span>
                </div>
                <span className={`pfd-num pfd-text-emphasized ${k.cls}`}>
                  {k.sign} {formatRub(p.amount_minor)}
                </span>
              </div>
            );
          })}
        </Section>
      </List>
    </Page>
  );
};
