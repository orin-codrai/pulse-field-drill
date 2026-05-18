import { useApi } from './useApi';

export type TransactionKind = 'expense' | 'income' | 'transfer' | 'adjustment';

export interface Transaction {
  id: number;
  kind: TransactionKind;
  amount_minor: number;
  currency: string;
  from_account_id: number | null;
  to_account_id: number | null;
  category_id: number | null;
  receipt_id: number | null;
  occurred_at: string;
  note: string | null;
  created_at: string;
}

/**
 * Phase 2: без аргументов, использует server default limit=50.
 * Phase 3: расширится до useTransactions({kind?, from?, to?, account_id?, offset?})
 * — accept signature break, без compat shim.
 */
export function useTransactions() {
  return useApi<Transaction[]>('/api/transactions', 'transactions');
}
