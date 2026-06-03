import type { ComponentType } from 'react';

import { AddEnvelopePage } from '@/pages/AddEnvelopePage/AddEnvelopePage';
import { AddPlanPage } from '@/pages/AddPlanPage/AddPlanPage';
import { AddSharedWorkspacePage } from '@/pages/AddSharedWorkspacePage/AddSharedWorkspacePage';
import { AddTransactionPage } from '@/pages/AddTransactionPage/AddTransactionPage';
import { AuditHistoryPage } from '@/pages/AuditHistoryPage/AuditHistoryPage';
import { BalancesPage } from '@/pages/BalancesPage/BalancesPage';
import { EnvelopeDetailPage } from '@/pages/EnvelopeDetailPage/EnvelopeDetailPage';
import { EnvelopesPage } from '@/pages/EnvelopesPage/EnvelopesPage';
import { InviteAcceptPage } from '@/pages/InviteAcceptPage/InviteAcceptPage';
import { MenuPage } from '@/pages/MenuPage/MenuPage';
import { PinSetupPage } from '@/pages/PinSetupPage/PinSetupPage';
import { PlanningPage } from '@/pages/PlanningPage/PlanningPage';
import { RegistrationPage } from '@/pages/RegistrationPage/RegistrationPage';
import { RestorePage } from '@/pages/RestorePage/RestorePage';
import { TransactionsPage } from '@/pages/TransactionsPage/TransactionsPage';

interface Route {
  path: string;
  Component: ComponentType;
}

export const routes: Route[] = [
  { path: '/', Component: BalancesPage },
  { path: '/transactions', Component: TransactionsPage },
  { path: '/planning', Component: PlanningPage },
  { path: '/menu', Component: MenuPage },
  { path: '/add', Component: AddTransactionPage },
  { path: '/plan/add', Component: AddPlanPage },
  { path: '/envelopes', Component: EnvelopesPage },
  { path: '/envelopes/add', Component: AddEnvelopePage },
  { path: '/envelopes/:envelopeId', Component: EnvelopeDetailPage },
  // P7:
  { path: '/register', Component: RegistrationPage },
  { path: '/restore', Component: RestorePage },
  { path: '/invites/:token/accept', Component: InviteAcceptPage },
  { path: '/workspaces/new', Component: AddSharedWorkspacePage },
  { path: '/audit', Component: AuditHistoryPage },
  // PIN setup/change (Phase 8 + 1).
  { path: '/pin/setup', Component: PinSetupPage },
];
