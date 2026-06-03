import { List, Section, Cell, Input, Textarea, Select, Button } from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useAccounts } from '@/hooks/useAccounts';
import { useCategories } from '@/hooks/useCategories';
import { useMainButton } from '@/hooks/useMainButton';
import { ApiError, apiPost } from '@/lib/api';
import { parseRubInputToMinor, todayIso } from '@/lib/format';
import { getLastAccountId, setLastAccountId } from '@/lib/lastAccount';
import { bump } from '@/lib/refetch';

type Kind = 'expense' | 'income' | 'transfer' | 'adjustment';
type AdjustmentDir = 'in' | 'out';

// Поле счёта-источника, которое дефолтим последним использованным (quick-add).
// transfer: дефолтим «со счёта», «на счёт» юзер выбирает сам.
function sourceAccountField(kind: Kind, dir: AdjustmentDir): 'from_account_id' | 'to_account_id' {
  if (kind === 'income') return 'to_account_id';
  if (kind === 'adjustment') return dir === 'in' ? 'to_account_id' : 'from_account_id';
  return 'from_account_id';
}

interface FormState {
  kind: Kind;
  from_account_id: number | null;
  to_account_id: number | null;
  category_id: number | null;
  amount_input: string;
  occurred_at: string;
  note: string;
  adjustment_dir: AdjustmentDir;
}

const initialState: FormState = {
  kind: 'expense',
  from_account_id: null,
  to_account_id: null,
  category_id: null,
  amount_input: '',
  occurred_at: todayIso(),
  note: '',
  adjustment_dir: 'out',
};

const KIND_LABEL: Record<Kind, string> = {
  expense: 'Расход',
  income: 'Доход',
  transfer: 'Перевод',
  adjustment: 'Коррекция',
};

export const AddTransactionPage = () => {
  const navigate = useNavigate();
  const raw = useSignal(initData.raw);
  const { data: allAccounts } = useAccounts();
  const { data: allCategories } = useCategories();

  const [form, setForm] = useState<FormState>(initialState);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Client-side filter archived (must-fix C2): API возвращает всё для AccountsManagePage,
  // но в форме создания tx показываем только active.
  const accounts = useMemo(
    () => (allAccounts ?? []).filter((a) => a.archived_at === null),
    [allAccounts],
  );

  // Фильтр категорий по kind. transfer не использует категорию.
  const categoriesForKind = useMemo(() => {
    if (!allCategories) return [];
    const active = allCategories.filter((c) => c.archived_at === null);
    if (form.kind === 'expense') return active.filter((c) => c.kind === 'expense' || c.kind === 'both');
    if (form.kind === 'income') return active.filter((c) => c.kind === 'income' || c.kind === 'both');
    if (form.kind === 'adjustment') return active.filter((c) => c.kind === 'both');
    return [];
  }, [allCategories, form.kind]);

  // Дефолт счёта (quick-add ≤3 тапа): последний использованный, иначе первый active.
  const defaultAccountId = useMemo(
    () => getLastAccountId() ?? accounts[0]?.id ?? null,
    [accounts],
  );

  // Заполняем счёт-источник дефолтом, если поле пустое. Срабатывает на загрузку
  // счетов и на смену kind/direction (кнопки сбрасывают счёт в null).
  useEffect(() => {
    if (defaultAccountId === null) return;
    const field = sourceAccountField(form.kind, form.adjustment_dir);
    setForm((f) => (f[field] === null ? { ...f, [field]: defaultAccountId } : f));
  }, [defaultAccountId, form.kind, form.adjustment_dir]);

  // Whitelist body: explicit object, не {...formState}. Mass-assignment guard.
  function buildPayload(): Record<string, unknown> | null {
    const amount = parseRubInputToMinor(form.amount_input);
    if (amount === null || amount <= 0) return null;

    const base: Record<string, unknown> = {
      kind: form.kind,
      amount_minor: amount,
      occurred_at: new Date(form.occurred_at).toISOString(),
    };
    if (form.note.trim()) base.note = form.note.trim();

    if (form.kind === 'expense') {
      if (form.from_account_id === null || form.category_id === null) return null;
      base.from_account_id = form.from_account_id;
      base.category_id = form.category_id;
    } else if (form.kind === 'income') {
      if (form.to_account_id === null || form.category_id === null) return null;
      base.to_account_id = form.to_account_id;
      base.category_id = form.category_id;
    } else if (form.kind === 'transfer') {
      if (form.from_account_id === null || form.to_account_id === null) return null;
      if (form.from_account_id === form.to_account_id) return null;
      base.from_account_id = form.from_account_id;
      base.to_account_id = form.to_account_id;
    } else {
      // adjustment
      if (form.category_id === null) return null;
      if (form.adjustment_dir === 'in') {
        if (form.to_account_id === null) return null;
        base.to_account_id = form.to_account_id;
      } else {
        if (form.from_account_id === null) return null;
        base.from_account_id = form.from_account_id;
      }
      base.category_id = form.category_id;
    }
    return base;
  }

  async function submit() {
    if (submitting) return;
    const payload = buildPayload();
    if (payload === null) {
      setSubmitError('Заполни все обязательные поля и сумму > 0');
      return;
    }
    if (!raw) {
      setSubmitError('Нет initData (запусти из Telegram)');
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      await apiPost<unknown>('/api/transactions', raw, payload);
      const used = form[sourceAccountField(form.kind, form.adjustment_dir)];
      if (used !== null) setLastAccountId(used);
      bump('balances');
      bump('transactions');
      navigate(-1);
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setSubmitError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  useMainButton({ text: submitting ? 'Сохранение…' : 'Сохранить', onClick: submit, enabled: !submitting });

  return (
    <Page back={true}>
      <List>
        <Section header="Тип">
          <div className="pfd-button-row pfd-button-row-wrap">
            {(['expense', 'income', 'transfer', 'adjustment'] as Kind[]).map((k) => (
              <Button
                key={k}
                size="s"
                mode={form.kind === k ? 'filled' : 'plain'}
                onClick={() => setForm({ ...initialState, kind: k, occurred_at: form.occurred_at })}
              >
                {KIND_LABEL[k]}
              </Button>
            ))}
          </div>
        </Section>

        {form.kind === 'adjustment' && (
          <Section header="Направление">
            <div className="pfd-button-row">
              <Button
                size="s"
                mode={form.adjustment_dir === 'out' ? 'filled' : 'plain'}
                onClick={() => setForm({ ...form, adjustment_dir: 'out', to_account_id: null })}
              >
                − Минус
              </Button>
              <Button
                size="s"
                mode={form.adjustment_dir === 'in' ? 'filled' : 'plain'}
                onClick={() => setForm({ ...form, adjustment_dir: 'in', from_account_id: null })}
              >
                + Плюс
              </Button>
            </div>
          </Section>
        )}

        <Section header="Сумма">
          <Input
            type="text"
            inputMode="decimal"
            placeholder="0"
            value={form.amount_input}
            onChange={(e) => setForm({ ...form, amount_input: e.target.value })}
          />
        </Section>

        <Section header="Счёт">
          {(form.kind === 'expense' ||
            form.kind === 'transfer' ||
            (form.kind === 'adjustment' && form.adjustment_dir === 'out')) && (
            <Select
              header="Со счёта"
              value={form.from_account_id?.toString() ?? ''}
              onChange={(e) =>
                setForm({ ...form, from_account_id: e.target.value ? Number(e.target.value) : null })
              }
            >
              <option value="">—</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </Select>
          )}
          {(form.kind === 'income' ||
            form.kind === 'transfer' ||
            (form.kind === 'adjustment' && form.adjustment_dir === 'in')) && (
            <Select
              header="На счёт"
              value={form.to_account_id?.toString() ?? ''}
              onChange={(e) =>
                setForm({ ...form, to_account_id: e.target.value ? Number(e.target.value) : null })
              }
            >
              <option value="">—</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </Select>
          )}
        </Section>

        {form.kind !== 'transfer' && (
          <Section header="Категория">
            <Select
              header="Категория"
              value={form.category_id?.toString() ?? ''}
              onChange={(e) =>
                setForm({ ...form, category_id: e.target.value ? Number(e.target.value) : null })
              }
            >
              <option value="">—</option>
              {categoriesForKind.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
          </Section>
        )}

        <Section header="Дата">
          <Input
            type="date"
            value={form.occurred_at}
            onChange={(e) => setForm({ ...form, occurred_at: e.target.value })}
          />
        </Section>

        <Section header="Заметка (опционально)">
          <Textarea
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
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
