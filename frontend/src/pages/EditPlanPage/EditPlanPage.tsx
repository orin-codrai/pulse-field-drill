import {
  Cell,
  Input,
  List,
  Section,
  Select,
  Spinner,
  Textarea,
} from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useAccounts } from '@/hooks/useAccounts';
import { useCategories, type Category } from '@/hooks/useCategories';
import { useMainButton } from '@/hooks/useMainButton';
import { usePlanned, type PlannedOperation } from '@/hooks/usePlanned';
import { ApiError, apiDelete, apiPatch } from '@/lib/api';
import { formatRub, parseRubInputToMinor } from '@/lib/format';
import { bump } from '@/lib/refetch';

type Recurrence = PlannedOperation['recurrence'];

const RECURRENCE_LABEL: Record<Recurrence, string> = {
  once: 'Однократно',
  week: 'Раз в неделю',
  month: 'Раз в месяц',
  year: 'Раз в год',
};

const KIND_LABEL: Record<PlannedOperation['kind'], string> = {
  income: 'Доход',
  expense: 'Расход',
};

/**
 * Same flatten рутина что и в AddPlanPage — оставлено локально, чтобы каждый
 * файл был самодостаточным. Если потребуется третий потребитель — вынесем
 * в `lib/categories.ts`.
 */
function flattenCategories(
  cats: Category[],
  kind: PlannedOperation['kind'],
): Array<{ cat: Category; indent: number }> {
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

interface FormState {
  amount_input: string;
  category_id: number | null;
  account_id: number | null;
  first_date: string;
  recurrence: Recurrence;
  total_cycles_input: string;
  note: string;
}

export const EditPlanPage = () => {
  const navigate = useNavigate();
  const { planId: idStr } = useParams<{ planId: string }>();
  const planId = idStr ? Number(idStr) : null;
  const raw = useSignal(initData.raw);
  const { data: allPlans, loading: plansLoading } = usePlanned();
  const { data: allAccounts } = useAccounts();
  const { data: allCategories } = useCategories();

  const plan = useMemo(
    () => allPlans?.find((p) => p.id === planId) ?? null,
    [allPlans, planId],
  );

  const [form, setForm] = useState<FormState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Pre-fill один раз когда plan приехал.
  useEffect(() => {
    if (plan && form === null) {
      setForm({
        amount_input: (plan.amount_minor / 100).toString().replace('.', ','),
        category_id: plan.category_id,
        account_id: plan.account_id,
        first_date: plan.first_date,
        recurrence: plan.recurrence,
        total_cycles_input: plan.total_cycles?.toString() ?? '',
        note: plan.note ?? '',
      });
    }
  }, [plan, form]);

  const accounts = useMemo(
    () => (allAccounts ?? []).filter((a) => a.archived_at === null),
    [allAccounts],
  );

  const categoriesFlat = useMemo(
    () => (allCategories && plan ? flattenCategories(allCategories, plan.kind) : []),
    [allCategories, plan],
  );

  // MF2: после первого confirm нельзя менять first_date / recurrence.
  const frozenSchedule = (plan?.completed_cycles ?? 0) > 0;

  function buildPayload(): Record<string, unknown> | null {
    if (!form || !plan) return null;
    const amount = parseRubInputToMinor(form.amount_input);
    if (amount === null || amount <= 0) return null;
    if (form.category_id === null || form.account_id === null) return null;

    const body: Record<string, unknown> = {
      amount_minor: amount,
      category_id: form.category_id,
      account_id: form.account_id,
      note: form.note.trim() || null,
    };
    if (!frozenSchedule) {
      body.first_date = form.first_date;
      body.recurrence = form.recurrence;
    }
    if (form.recurrence !== 'once' && form.total_cycles_input.trim()) {
      const n = Number(form.total_cycles_input);
      if (Number.isInteger(n) && n >= 1) body.total_cycles = n;
      else body.total_cycles = null;
    } else if (form.recurrence === 'once') {
      // once → total_cycles нет смысла.
    } else {
      body.total_cycles = null;
    }
    return body;
  }

  async function save() {
    if (submitting || !plan || !raw) return;
    const payload = buildPayload();
    if (payload === null) {
      setError('Заполни сумму > 0, категорию и счёт');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await apiPatch(`/api/planned/${plan.id}`, raw, payload);
      bump('planned');
      bump('forecast');
      navigate(-1);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status} ${e.detail}` : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function archive() {
    if (!plan || !raw) return;
    if (!confirm('Архивировать план? Прогноз перестанет его учитывать. Подтверждённые транзакции сохранятся.')) return;
    setError(null);
    try {
      await apiPatch(`/api/planned/${plan.id}`, raw, {
        archived_at: new Date().toISOString(),
      });
      bump('planned');
      bump('forecast');
      navigate(-1);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status} ${e.detail}` : String(e));
    }
  }

  async function unarchive() {
    if (!plan || !raw) return;
    setError(null);
    try {
      await apiPatch(`/api/planned/${plan.id}`, raw, { archived_at: null });
      bump('planned');
      bump('forecast');
      navigate(-1);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status} ${e.detail}` : String(e));
    }
  }

  async function remove() {
    if (!plan || !raw) return;
    if (!confirm('Удалить план без следа? Если есть подтверждённые транзакции — будет ошибка, тогда архивируй.')) return;
    setError(null);
    try {
      await apiDelete(`/api/planned/${plan.id}`, raw);
      bump('planned');
      bump('forecast');
      navigate(-1);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status} ${e.detail}` : String(e));
    }
  }

  useMainButton({
    text: submitting ? 'Сохранение…' : 'Сохранить',
    onClick: save,
    enabled: !submitting && form !== null,
  });

  if (plansLoading || (!plan && allPlans === null)) {
    return (
      <Page back={true}>
        <List>
          <Section>
            <Cell before={<Spinner size="s" />}>Загрузка…</Cell>
          </Section>
        </List>
      </Page>
    );
  }
  if (!plan || !form) {
    return (
      <Page back={true}>
        <List>
          <Section header="План">
            <Cell>План не найден</Cell>
          </Section>
        </List>
      </Page>
    );
  }

  return (
    <Page back={true}>
      <List>
        <Section header="Тип" footer="Тип нельзя сменить после создания.">
          <Cell>
            {KIND_LABEL[plan.kind]} · текущая сумма {formatRub(plan.amount_minor)}
          </Cell>
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

        <Section header={plan.kind === 'income' ? 'Куда' : 'Откуда'}>
          <Select
            header={plan.kind === 'income' ? 'На счёт' : 'Со счёта'}
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

        <Section
          header="Первая дата"
          footer={frozenSchedule ? `Заморожено: уже ${plan.completed_cycles} подтверждённых вхождений.` : undefined}
        >
          <Input
            type="date"
            value={form.first_date}
            disabled={frozenSchedule}
            onChange={(e) => setForm({ ...form, first_date: e.target.value })}
          />
        </Section>

        <Section
          header="Периодичность"
          footer={frozenSchedule ? 'Заморожено после первого подтверждения.' : undefined}
        >
          <Select
            header="Периодичность"
            value={form.recurrence}
            disabled={frozenSchedule}
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
          <Section
            header="Сколько раз (опционально)"
            footer={`Выполнено: ${plan.completed_cycles}`}
          >
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

        <Section header="Действия">
          {plan.archived_at === null ? (
            <Cell onClick={archive}>
              <span className="pfd-text-warning">Архивировать</span>
            </Cell>
          ) : (
            <Cell onClick={unarchive}>Восстановить из архива</Cell>
          )}
          <Cell onClick={remove}>
            <span className="pfd-text-danger">Удалить без следа</span>
          </Cell>
        </Section>

        {error && (
          <Section>
            <Cell><span className="pfd-text-danger">{error}</span></Cell>
          </Section>
        )}
      </List>
    </Page>
  );
};
