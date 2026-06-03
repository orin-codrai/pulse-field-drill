import { useApi } from './useApi';

export interface Forecast {
  available_now: number;
  reserved: number;
  planned_income: number;
  planned_expense: number;
  /** Predicted envelope auto-skim из future income (ADR-0008 v1.1). */
  planned_skim: number;
  projected_balance: number;
  projected_available: number;
  horizon: string; // YYYY-MM-DD
}

export function useForecast() {
  return useApi<Forecast>('/api/forecast', 'forecast');
}
