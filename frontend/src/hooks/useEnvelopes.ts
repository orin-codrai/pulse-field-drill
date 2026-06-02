import { useApi } from './useApi';

export interface Envelope {
  id: number;
  workspace_id: number;
  name: string;
  percent: number | null;
  target_amount_minor: number | null;
  currency: string;
  target_date: string | null;
  icon: string | null;
  archived_at: string | null;
  created_at: string;
  /** Σ entries; для archived — 0 в активной выдаче (B2 query-filter). */
  reserved_minor: number;
}

export interface EnvelopeEntry {
  id: number;
  envelope_id: number;
  workspace_id: number;
  /** Signed: withdraw < 0, manual/skim > 0. Frontend применяет formatSignedRub. */
  amount_minor: number;
  kind: 'skim' | 'manual' | 'withdraw';
  source_transaction_id: number | null;
  created_at: string;
}

export function useEnvelopes() {
  return useApi<Envelope[]>('/api/envelopes', 'envelopes');
}

export function useEnvelopeEntries(envelopeId: number | null) {
  return useApi<EnvelopeEntry[]>(
    envelopeId === null ? null : `/api/envelopes/${envelopeId}/entries`,
    'envelopes',
  );
}
