import type { ComponentType } from 'react';

import { AddTransactionPage } from '@/pages/AddTransactionPage/AddTransactionPage';
import { BalancesPage } from '@/pages/BalancesPage/BalancesPage';
import { MenuPage } from '@/pages/MenuPage/MenuPage';
import { TransactionsPage } from '@/pages/TransactionsPage/TransactionsPage';

interface Route {
  path: string;
  Component: ComponentType;
}

export const routes: Route[] = [
  { path: '/', Component: BalancesPage },
  { path: '/transactions', Component: TransactionsPage },
  { path: '/menu', Component: MenuPage },
  { path: '/add', Component: AddTransactionPage },
];
