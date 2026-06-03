import {
  Cell,
  Input,
  List,
  Section,
} from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useMainButton } from '@/hooks/useMainButton';
import { ApiError, apiPost } from '@/lib/api';
import { parseRubInputToMinor } from '@/lib/format';
import { bump } from '@/lib/refetch';

interface FormState {
  name: string;
  percent_input: string; // строка для optional input
  target_amount_input: string;
  target_date: string;
  icon: string;
}

const initialState: FormState = {
  name: '',
  percent_input: '',
  target_amount_input: '',
  target_date: '',
  icon: '',
};

export const AddEnvelopePage = () => {
  const navigate = useNavigate();
  const raw = useSignal(initData.raw);
  const [form, setForm] = useState<FormState>(initialState);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  function buildPayload(): Record<string, unknown> | null {
    if (!form.name.trim()) return null;
    const body: Record<string, unknown> = { name: form.name.trim() };
    if (form.percent_input.trim()) {
      const p = Number(form.percent_input);
      if (!Number.isInteger(p) || p < 1 || p > 100) return null;
      body.percent = p;
    }
    if (form.target_amount_input.trim()) {
      const m = parseRubInputToMinor(form.target_amount_input);
      if (m === null || m <= 0) return null;
      body.target_amount_minor = m;
    }
    if (form.target_date.trim()) body.target_date = form.target_date;
    if (form.icon.trim()) body.icon = form.icon.trim();
    return body;
  }

  async function submit() {
    if (submitting) return;
    const payload = buildPayload();
    if (payload === null) {
      setSubmitError('Введи название; процент 1–100; сумма цели > 0');
      return;
    }
    if (!raw) {
      setSubmitError('Нет initData (запусти из Telegram)');
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      await apiPost('/api/envelopes', raw, payload);
      bump('envelopes');
      bump('forecast');
      navigate(-1);
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setSubmitError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  useMainButton({
    text: submitting ? 'Сохранение…' : 'Сохранить',
    onClick: submit,
    enabled: !submitting,
  });

  return (
    <Page back={true}>
      <List>
        <Section header="Название">
          <Input
            placeholder="НЗ / Отпуск / Подушка"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </Section>

        <Section
          header="Процент авто-скима (опционально)"
          footer="Каждый подтверждённый доход отщипывает эту долю в конверт. Пусто = ручной конверт."
        >
          <Input
            type="number"
            inputMode="numeric"
            placeholder="напр. 10"
            value={form.percent_input}
            onChange={(e) => setForm({ ...form, percent_input: e.target.value })}
          />
        </Section>

        <Section header="Цель (опционально)">
          <Input
            type="text"
            inputMode="decimal"
            placeholder="Сумма цели, ₽"
            value={form.target_amount_input}
            onChange={(e) =>
              setForm({ ...form, target_amount_input: e.target.value })
            }
          />
          <Input
            type="date"
            value={form.target_date}
            onChange={(e) => setForm({ ...form, target_date: e.target.value })}
          />
        </Section>

        <Section header="Иконка (эмодзи, опционально)">
          <Input
            placeholder="💰"
            value={form.icon}
            onChange={(e) => setForm({ ...form, icon: e.target.value })}
          />
        </Section>

        {submitError && (
          <Section>
            <Cell><span className="pfd-text-danger">{submitError}</span></Cell>
          </Section>
        )}
      </List>
    </Page>
  );
};
