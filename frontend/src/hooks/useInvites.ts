import { useApi } from './useApi';

export type InviteStatus = 'pending' | 'accepted' | 'revoked' | 'expired';

export interface Invite {
  id: number;
  workspace_id: number;
  token: string;
  status: InviteStatus;
  expires_at: string;
  created_at: string;
  accepted_at: string | null;
}

export interface InvitePreview {
  workspace_id: number;
  workspace_name: string;
  workspace_kind: 'personal' | 'shared';
  status: InviteStatus;
  expires_at: string;
  inviter_display_name: string | null;
}

export function useWorkspaceInvites(workspaceId: number | null) {
  return useApi<Invite[]>(
    workspaceId === null ? null : `/api/workspaces/${workspaceId}/invites`,
    'invites',
  );
}

export function useInvitePreview(token: string | null) {
  return useApi<InvitePreview>(
    token === null ? null : `/api/invites/${token}`,
    'invites',
  );
}
