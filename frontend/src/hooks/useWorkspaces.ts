import { useApi } from './useApi';

export type WorkspaceKind = 'personal' | 'shared';

export interface Workspace {
  id: number;
  name: string;
  kind: WorkspaceKind;
}

export function useWorkspaces() {
  return useApi<Workspace[]>('/api/workspaces', 'workspaces');
}
