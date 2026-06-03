import { Tabbar } from '@telegram-apps/telegram-ui';
import { CalendarClock, Receipt, Settings, Wallet } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';

const TABS = [
  { path: '/', label: 'Балансы', Icon: Wallet },
  { path: '/transactions', label: 'Список', Icon: Receipt },
  { path: '/planning', label: 'Планы', Icon: CalendarClock },
  { path: '/menu', label: 'Меню', Icon: Settings },
];

// Modal-like / detail-страницы прячут таб-бар: формы (юзер не должен уехать
// случайным тапом), envelope-detail (focus на одном конверте, leдgers).
// EnvelopesPage / EnvelopeDetailPage open'аются из Меню → back возвращает туда.
const MODAL_PATHS = new Set([
  '/add',
  '/plan/add',
  '/envelopes',
  '/envelopes/add',
  // P7 full-page без TabBar:
  '/register',
  '/restore',
  '/workspaces/new',
  '/audit',
  '/pin/setup',
]);

function isModalPath(pathname: string): boolean {
  if (MODAL_PATHS.has(pathname)) return true;
  // /envelopes/{id} (envelope detail) — тоже modal-like.
  if (/^\/envelopes\/\d+$/.test(pathname)) return true;
  // /invites/{token}/accept (P7) — modal-like.
  if (/^\/invites\/[^/]+\/accept$/.test(pathname)) return true;
  return false;
}

export function BottomTabBar() {
  const navigate = useNavigate();
  const location = useLocation();
  if (isModalPath(location.pathname)) return null;

  // C3: только активная вкладка с подписью; неактивные — иконки. См. журнал
  // 2026-06-02: viewport-breakpoint не сработал, упрощено до «всегда icon-only
  // для неактивных», чтобы 4 таба влезали на любом устройстве.
  return (
    <Tabbar>
      {TABS.map(({ path, label, Icon }) => {
        const selected = location.pathname === path;
        return (
          <Tabbar.Item
            key={path}
            text={selected ? label : ''}
            selected={selected}
            onClick={() => navigate(path)}
          >
            <Icon size={24} strokeWidth={2} />
          </Tabbar.Item>
        );
      })}
    </Tabbar>
  );
}
