import { Banknote, CreditCard, HandCoins, PiggyBank, Wallet } from 'lucide-react';
import type { FC } from 'react';

const MAP = {
  card: CreditCard,
  credit: CreditCard,
  cash: Banknote,
  savings: PiggyBank,
  debt: HandCoins,
} as const;

interface Props {
  type: string;
  size?: number;
  className?: string;
}

export const AccountIcon: FC<Props> = ({ type, size = 20, className }) => {
  const Comp = (MAP as Record<string, typeof Wallet>)[type] ?? Wallet;
  return <Comp size={size} strokeWidth={2} className={className} />;
};
