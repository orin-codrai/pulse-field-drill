import {
  Button,
  Cell,
  Input,
  List,
  Section,
  Select,
  Textarea,
} from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useAccounts } from '@/hooks/useAccounts';
import { useCategories, type Category } from '@/hooks/useCategories';
import { useMainButton } from '@/hooks/useMainButton';
import { ApiError, apiPost } from '@/lib/api';
import { parseRubInputToMinor, todayIso } from '@/lib/format';
import { bump } from '@/lib/refetch';

type Kind = 'income' | 'expense';
type Recurrence = 'once' | 'week' | 'month' | 'year';

const KIND_LABEL: Record<Kind, string> = { expense: 'Расход', income: 'Доход' };
const RECURRENCE_LABEL: Record<Recurrence, string> = {
  once: 'Однократно',
  week: 'Раз в неделю',
  month: 'Раз в месяц',
  year: 'Раз в год',
};

interface FormState {
  kind: Kind;
  amount_input: string;
  category_id: number | null;
  account_id: number | null;
  first_date: string;
  recurrence: Recurrence;
  total_cycles_input: string;
  note: string;
}

const initialState: FormState = {
  kind: 'expense',
  amount_input: '',
  category_id: null,
  account_id: null,
  first_date: todayIso(),
  recurrence: 'month',
  total_cycles_input: '',
  note: '',
};

/**
 * Дерево категорий → плоский список с indent для уровня 2.
 * Сортировка: parent → его children по id → следующий parent.
 * Системные (workspace_id IS NULL) идут первыми, пользовательские — после.
 */
function flattenCategories(cats: Category[], kind: Kind): Array<{ cat: Category; indent: number }> {
  // Категории совместимого kind: 'both' пускает любого, иначе строгое совпадение.
  // Подкатегория не появится без своего parent в той же выборке (kind-inheritance
  // в _validate_parent_ref): child='expense' → parent='expense'|'both'.
  const active = cats.filter((c) => c.archived_at === null);
  const compat = active.filter((c) => c.kind === 'both' || c.kind === kind);

  const byParent = new Map<number | null, Category[]>();
  for (const c of compat) {
    const k = c.parent_id;
    if (!byParent.has(k)) byParent.set(k, []);
    byParent.get(k)!.push(c);
  }
  for (const arr of byParent.values()) {
    arr.sort((a, b) => {
      // Системные (workspace_id null) первыми.
      const ax = a.workspace_id === null ? 0 : 1;
      const bx = b.workspace_id === null ? 0 : 1;
      if (ax !== bx) return ax - bx;
      return a.id - b.id;
    });
  }

  const out: Array<{ cat: Category; indent: number }> = [];
  const roots = byParent.get(null) ?? [];
  for (const root of roots) {
    out.push({ cat: root, indent: 0 });
    for (const child of byParent.get(root.id) ?? []) {
      out.push({ cat: child, indent: 1 });
    }
  }
  return out;
}

export const AddPlanPage = () => {
  const navigate = useNavigate();
  const raw = useSignal(initData.raw);
  const { data: allAccounts } = useAccounts();
  const { data: allCategories } = useCategories();

  const [form, setForm] = useState<FormState>(initialState);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const accounts = useMemo(
    () => (allAccounts ?? []).filter((a) => a.archived_at === null),
    [allAccounts],
  );

  const categoriesFlat = useMemo(
    () => (allCategories ? flattenCategories(allCategories, form.kind) : []),
    [allCategories, form.kind],
  );

  function buildPayload(): Record<string, unknown> | null {
    const amount = parseRubInputToMinor(form.amount_input);
    if (amount === null || amount <= 0) return null;
    if (form.category_id === null) return null;
    if (form.account_id === null) return null;

    const body: Record<string, unknown> = {
      kind: form.kind,
      amount_minor: amount,
      category_id: form.category_id,
      account_id: form.account_id,
      first_date: form.first_date,
      recurrence: form.recurrence,
    };
    if (form.note.trim()) body.note = form.note.trim();
    if (form.recurrence !== 'once' && form.total_cycles_input.trim()) {
      const n = Number(form.total_cycles_input);
      if (Number.isInteger(n) && n >= 1) body.total_cycles = n;
    }
    return body;
  }

  async function submit() {
    if (submitting) return;
    const payload = buildPayload();
    if (payload === null) {
      setSubmitError('Заполни сумму, категорию и счёт');
      return;
    }
    if (!raw) {
      setSubmitError('Нет initData (запусти из Telegram)');
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      await apiPost('/api/planned', raw, payload);
      bump('planned');
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
        <Section header="Тип">
          <div style={{ display: 'flex', gap: 4, padding: '8px 16px' }}>
            {(['expense', 'income'] as Kind[]).map((k) => (
              <Button
                key={k}
                size="s"
                mode={form.kind === k ? 'filled' : 'plain'}
                onClick={() =>
                  setForm({ ...initialState, kind: k, first_date: form.first_date })
                }
              >
                {KIND_LABEL[k]}
              </Button>
            ))}
          </div>
        </Section>

        <Section header="Сумма">
          <Input
            type="text"
            inputMode="decimal"
            placeholder="0"
            value={form.amount_input}
            onChange={(e) => setForm({ ...form, amount_input: e.target.value })}
          />
        </Section>

        <Section header="Категория">
          <Select
            header="Категория"
            value={form.category_id?.toString() ?? ''}
            onChange={(e) =>
              setForm({ ...form, category_id: e.target.value ? Number(e.target.value) : null })
            }
          >
            <option value="">—</option>
            {categoriesFlat.map(({ cat, indent }) => (
              <option key={cat.id} value={cat.id}>
                {indent > 0 ? '— ' : ''}
                {cat.name}
              </option>
            ))}
          </Select>
        </Section>

        <Section header={form.kind === 'income' ? 'Куда' : 'Откуда'}>
          <Select
            header={form.kind === 'income' ? 'На счёт' : 'Со счёта'}
            value={form.account_id?.toString() ?? ''}
            onChange={(e) =>
              setForm({ ...form, account_id: e.target.value ? Number(e.target.value) : null })
            }
          >
            <option value="">—</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </Select>
        </Section>

        <Section header="Первая дата">
          <Input
            type="date"
            value={form.first_date}
            onChange={(e) => setForm({ ...form, first_date: e.target.value })}
          />
        </Section>

        <Section header="Периодичность">
          <Select
            header="Периодичность"
            value={form.recurrence}
            onChange={(e) =>
              setForm({ ...form, recurrence: e.target.value as Recurrence })
            }
          >
            {(['once', 'week', 'month', 'year'] as Recurrence[]).map((r) => (
              <option key={r} value={r}>{RECURRENCE_LABEL[r]}</option>
            ))}
          </Select>
        </Section>

        {form.recurrence !== 'once' && (
          <Section header="Сколько раз (опционально)">
            <Input
              type="number"
              inputMode="numeric"
              placeholder="без ограничения"
              value={form.total_cycles_input}
              onChange={(e) => setForm({ ...form, total_cycles_input: e.target.value })}
            />
          </Section>
        )}

        <Section header="Заметка (опционально)">
          <Textarea
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
        </Section>

        {submitError && (
          <Section>
            <Cell style={{ color: 'var(--tg-theme-destructive-text-color, red)' }}>
              {submitError}
            </Cell>
          </Section>
        )}
      </List>
    </Page>
  );
};
