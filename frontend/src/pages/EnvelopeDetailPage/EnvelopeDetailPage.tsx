import {
  Button,
  Caption,
  Cell,
  Input,
  List,
  Section,
  Spinner,
  Title,
} from '@telegram-apps/telegram-ui';
import { initData, useSignal } from '@tma.js/sdk-react';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { Page } from '@/components/Page.tsx';
import { useEnvelopeEntries, useEnvelopes } from '@/hooks/useEnvelopes';
import { ApiError, apiPost } from '@/lib/api';
import { formatRub, formatSignedRub, parseRubInputToMinor } from '@/lib/format';
import { bump } from '@/lib/refetch';

const KIND_LABEL: Record<string, string> = {
  skim: 'Ауто-ским',
  manual: 'Вручную',
  withdraw: 'Забрано',
};

export const EnvelopeDetailPage = () => {
  const navigate = useNavigate();
  const { envelopeId: idStr } = useParams<{ envelopeId: string }>();
  const envelopeId = idStr ? Number(idStr) : null;
  const raw = useSignal(initData.raw);
  const { data: envelopes } = useEnvelopes();
  const { data: entries, loading } = useEnvelopeEntries(envelopeId);

  const envelope = envelopes?.find((e) => e.id === envelopeId) ?? null;

  const [kind, setKind] = useState<'manual' | 'withdraw'>('manual');
  const [amountInput, setAmountInput] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  async function submit() {
    if (envelopeId === null || submitting) return;
    const amount = parseRubInputToMinor(amountInput);
    if (amount === null || amount <= 0) {
      setSubmitError('Введи сумму > 0');
      return;
    }
    if (!raw) {
      setSubmitError('Нет initData (запусти из Telegram)');
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      await apiPost(`/api/envelopes/${envelopeId}/entries`, raw, {
        kind,
        amount_minor: amount,
      });
      bump('envelopes');
      bump('forecast');
      setAmountInput('');
    } catch (e) {
      // PIN-F мягкая подача 409: история неизменяема, объясняем
      // что делать.
      if (e instanceof ApiError && e.status === 409) {
        setSubmitError(
          envelope?.archived_at
            ? 'Конверт в архиве. Разархивируй, потом добавляй.'
            : e.detail,
        );
      } else {
        const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
        setSubmitError(msg);
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (!envelope) {
    return (
      <Page back={true}>
        <List>
          <Section>
            <Cell>
              <Spinner size="s" />
            </Cell>
          </Section>
        </List>
      </Page>
    );
  }

  return (
    <Page back={true}>
      <List>
        <Section header={envelope.name}>
          <div style={{ padding: '12px 16px' }}>
            <Title level="1">{formatRub(envelope.reserved_minor)}</Title>
            <Caption level="1" weight="3" style={{ opacity: 0.6 }}>
              зарезервировано
              {envelope.percent !== null && ` · ${envelope.percent}% авто-ским`}
              {envelope.target_amount_minor !== null &&
                ` · цель ${formatRub(envelope.target_amount_minor)}`}
            </Caption>
          </div>
        </Section>

        <Section
          header={kind === 'manual' ? 'Положить ещё' : 'Забрать'}
          footer="Manual растит резерв; withdraw уменьшает (запись в леджер, история сохраняется)."
        >
          <div style={{ display: 'flex', gap: 4, padding: '8px 16px' }}>
            <Button
              size="s"
              mode={kind === 'manual' ? 'filled' : 'plain'}
              onClick={() => setKind('manual')}
            >
              + Положить
            </Button>
            <Button
              size="s"
              mode={kind === 'withdraw' ? 'filled' : 'plain'}
              onClick={() => setKind('withdraw')}
            >
              − Забрать
            </Button>
          </div>
          <Input
            type="text"
            inputMode="decimal"
            placeholder="Сумма, ₽"
            value={amountInput}
            onChange={(e) => setAmountInput(e.target.value)}
          />
          <div style={{ padding: '0 16px 12px' }}>
            <Button
              size="m"
              stretched
              disabled={submitting}
              onClick={submit}
            >
              {submitting ? 'Отправка…' : 'Записать'}
            </Button>
          </div>
          {submitError && (
            <Cell style={{ color: 'var(--tg-theme-destructive-text-color, red)' }}>
              {submitError}
            </Cell>
          )}
        </Section>

        <Section
          header="История"
          footer="Леджер: иммутабельные строки. Скимы создаются автоматически при подтверждении дохода."
        >
          {loading && <Cell before={<Spinner size="s" />}>Загрузка…</Cell>}
          {entries && entries.length === 0 && (
            <Cell>Пока пусто</Cell>
          )}
          {entries?.map((e) => (
            <Cell
              key={e.id}
              subtitle={`${KIND_LABEL[e.kind]} · ${new Date(e.created_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}`}
              after={
                <strong
                  style={{
                    color:
                      e.amount_minor < 0
                        ? 'var(--tg-theme-destructive-text-color, #e53935)'
                        : 'var(--tg-theme-text-color)',
                  }}
                >
                  {formatSignedRub(e.amount_minor)}
                </strong>
              }
            >
              {e.kind === 'skim' && e.source_transaction_id
                ? `Из дохода #${e.source_transaction_id}`
                : KIND_LABEL[e.kind]}
            </Cell>
          ))}
        </Section>

        <Section>
          <Cell onClick={() => navigate('/envelopes')}>← К списку конвертов</Cell>
        </Section>
      </List>
    </Page>
  );
};
