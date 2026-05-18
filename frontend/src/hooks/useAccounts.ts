import { useApi } from './useApi';

export interface Account {
  id: number;
  name: string;
  type: 'card' | 'cash' | 'savings' | 'debt' | 'credit';
  currency: string;
  initial_balance_minor: number;
  icon: string | null;
  archived_at: string | null;
  created_at: string;
}

export function useAccounts() {
  return useApi<Account[]>('/api/accounts', 'accounts');
}
