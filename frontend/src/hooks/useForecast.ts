import { useApi } from './useApi';

export interface Forecast {
  available_now: number;
  reserved: number;
  planned_income: number;
  planned_expense: number;
  projected_balance: number;
  projected_available: number;
  horizon: string; // YYYY-MM-DD
}

export function useForecast() {
  return useApi<Forecast>('/api/forecast', 'forecast');
}
