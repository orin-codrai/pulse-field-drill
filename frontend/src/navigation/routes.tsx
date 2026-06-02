import type { ComponentType } from 'react';

import { AddEnvelopePage } from '@/pages/AddEnvelopePage/AddEnvelopePage';
import { AddPlanPage } from '@/pages/AddPlanPage/AddPlanPage';
import { AddTransactionPage } from '@/pages/AddTransactionPage/AddTransactionPage';
import { BalancesPage } from '@/pages/BalancesPage/BalancesPage';
import { EnvelopeDetailPage } from '@/pages/EnvelopeDetailPage/EnvelopeDetailPage';
import { EnvelopesPage } from '@/pages/EnvelopesPage/EnvelopesPage';
import { MenuPage } from '@/pages/MenuPage/MenuPage';
import { PlanningPage } from '@/pages/PlanningPage/PlanningPage';
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
];
