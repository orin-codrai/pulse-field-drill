import { Tabbar } from '@telegram-apps/telegram-ui';
import { useLocation, useNavigate } from 'react-router-dom';

const TABS = [
  { path: '/', label: 'Балансы', icon: '💼' },
  { path: '/transactions', label: 'Список', icon: '📋' },
  { path: '/menu', label: 'Меню', icon: '⚙️' },
];

export function BottomTabBar() {
  const navigate = useNavigate();
  const location = useLocation();
  // AddTransaction (modal-like) не подсвечивает таб; TabBar скрыт на /add.
  if (location.pathname === '/add') return null;

  return (
    <Tabbar>
      {TABS.map((t) => (
        <Tabbar.Item
          key={t.path}
          text={t.label}
          selected={location.pathname === t.path}
          onClick={() => navigate(t.path)}
        >
          <span style={{ fontSize: 22 }}>{t.icon}</span>
        </Tabbar.Item>
      ))}
    </Tabbar>
  );
}
