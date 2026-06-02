import { useApi } from './useApi';

export type PlannedKind = 'income' | 'expense';
export type PlannedRecurrence = 'once' | 'week' | 'month' | 'year';
export type PlannedStatus = 'planned' | 'paused' | 'done';

export interface PlannedOperation {
  id: number;
  workspace_id: number;
  kind: PlannedKind;
  amount_minor: number;
  currency: string;
  category_id: number;
  account_id: number;
  first_date: string;
  recurrence: PlannedRecurrence;
  total_cycles: number | null;
  completed_cycles: number;
  status: PlannedStatus;
  note: string | null;
  created_at: string;
  archived_at: string | null;
}

export interface DuePlannedItem {
  planned_operation_id: number;
  scheduled_date: string;
  amount_minor: number;
  kind: PlannedKind;
  currency: string;
  category_id: number;
  account_id: number;
  note: string | null;
}

export function usePlanned() {
  return useApi<PlannedOperation[]>('/api/planned', 'planned');
}

export function useDuePlanned() {
  return useApi<DuePlannedItem[]>('/api/planned/due', 'planned');
}
