import { useApi } from './useApi';

export interface AccountBalance {
  account_id: number;
  name: string;
  type: string;
  balance_minor: number;
}

export function useBalances() {
  return useApi<AccountBalance[]>('/api/accounts/balances', 'balances');
}
