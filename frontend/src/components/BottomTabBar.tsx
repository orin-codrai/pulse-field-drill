import { Tabbar } from '@telegram-apps/telegram-ui';
import { useLocation, useNavigate } from 'react-router-dom';

const TABS = [
  { path: '/', label: 'Балансы', icon: '💼' },
  { path: '/transactions', label: 'Список', icon: '📋' },
  { path: '/planning', label: 'Планы', icon: '📅' },
  { path: '/menu', label: 'Меню', icon: '⚙️' },
];

// Modal-like / detail-страницы прячут таб-бар: формы (юзер не должен уехать
// случайным тапом), envelope-detail (focus на одном конверте, leдgers).
// EnvelopesPage / EnvelopeDetailPage open'аются из Меню → back возвращает туда.
const MODAL_PATHS = new Set([
  '/add',
  '/plan/add',
  '/envelopes',
  '/envelopes/add',
]);

function isModalPath(pathname: string): boolean {
  if (MODAL_PATHS.has(pathname)) return true;
  // /envelopes/{id} (envelope detail) — тоже modal-like.
  if (/^\/envelopes\/\d+$/.test(pathname)) return true;
  return false;
}

export function BottomTabBar() {
  const navigate = useNavigate();
  const location = useLocation();
  if (isModalPath(location.pathname)) return null;

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
