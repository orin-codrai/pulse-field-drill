import { Tabbar } from '@telegram-apps/telegram-ui';
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

const TABS = [
  { path: '/', label: 'Балансы', icon: '💼' },
  { path: '/transactions', label: 'Список', icon: '📋' },
  { path: '/planning', label: 'Планы', icon: '📅' },
  { path: '/menu', label: 'Меню', icon: '⚙️' },
];

// C3 — fallback icon-only на узких экранах: текст 4 табов на iOS SE (320px)
// перестаёт влезать. ~340px эмпирическое отсечение (тестировать на устройстве).
const ICON_ONLY_BREAKPOINT = 340;

// Modal-like страницы прячут таб-бар, чтобы юзер не уехал из формы случайным
// тапом по табу. /plan/add — новая, добавлена с Phase 5.E.
const MODAL_PATHS = new Set(['/add', '/plan/add']);

export function BottomTabBar() {
  const navigate = useNavigate();
  const location = useLocation();

  const [iconOnly, setIconOnly] = useState(
    () => typeof window !== 'undefined' && window.innerWidth < ICON_ONLY_BREAKPOINT,
  );
  useEffect(() => {
    const onResize = () => setIconOnly(window.innerWidth < ICON_ONLY_BREAKPOINT);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  if (MODAL_PATHS.has(location.pathname)) return null;

  return (
    <Tabbar>
      {TABS.map((t) => {
        const selected = location.pathname === t.path;
        return (
          <Tabbar.Item
            key={t.path}
            text={iconOnly && !selected ? '' : t.label}
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
