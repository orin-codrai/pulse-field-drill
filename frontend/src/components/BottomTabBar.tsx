import { Tabbar } from '@telegram-apps/telegram-ui';
import { useLocation, useNavigate } from 'react-router-dom';

const TABS = [
  { path: '/', label: 'Балансы', icon: '💼' },
  { path: '/transactions', label: 'Список', icon: '📋' },
  { path: '/planning', label: 'Планы', icon: '📅' },
  { path: '/menu', label: 'Меню', icon: '⚙️' },
];

// Modal-like страницы прячут таб-бар, чтобы юзер не уехал из формы случайным
// тапом по табу.
const MODAL_PATHS = new Set(['/add', '/plan/add']);

export function BottomTabBar() {
  const navigate = useNavigate();
  const location = useLocation();
  if (MODAL_PATHS.has(location.pathname)) return null;

  // C3: только активная вкладка с подписью; неактивные — иконки.
  // viewport-breakpoint не сработал на скриншоте 2026-06-02 («Балан...» обрезалось),
  // упрощаем — всегда icon-only для неактивных, чтобы 4 таба влезали на любом
  // устройстве без обрезания.
  return (
    <Tabbar>
      {TABS.map((t) => {
        const selected = location.pathname === t.path;
        return (
          <Tabbar.Item
            key={t.path}
            text={selected ? t.label : ''}
            selected={selected}
            onClick={() => navigate(t.path)}
          >
            <span style={{ fontSize: 22 }}>{t.icon}</span>
          </Tabbar.Item>
        );
      })}
    </Tabbar>
  );
}
