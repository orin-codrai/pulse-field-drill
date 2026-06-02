import { useApi } from './useApi';

export interface AuditEntry {
  id: number;
  actor_user_id: number | null;
  actor_display_name: string | null;
  entity_type: 'transaction' | 'account';
  entity_id: number;
  action: 'create' | 'update' | 'delete';
  created_at: string;
}

export function useAudit() {
  return useApi<AuditEntry[]>('/api/audit', 'audit');
}
