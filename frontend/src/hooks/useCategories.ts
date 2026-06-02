import { useApi } from './useApi';

export interface Category {
  id: number;
  workspace_id: number | null;
  parent_id: number | null;
  name: string;
  kind: 'expense' | 'income' | 'both';
  icon: string | null;
  archived_at: string | null;
  created_at: string;
}

export function useCategories() {
  return useApi<Category[]>('/api/categories', 'categories');
}
