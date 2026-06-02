import { useEffect } from 'react';
import { HashRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { useLaunchParams, useSignal, miniApp } from '@tma.js/sdk-react';
import { AppRoot } from '@telegram-apps/telegram-ui';

import { BottomTabBar } from '@/components/BottomTabBar';
import { useMe } from '@/hooks/useMe';
import { routes } from '@/navigation/routes.tsx';

/**
 * Redirect orchestration (P7):
 *
 *   priority 1: deleted_at  →  /restore
 *   priority 2: registration_required  →  /register
 *   priority 3: pendingInviteToken in sessionStorage  →  /invites/{token}/accept
 *
 * Pages /restore, /register, /invites/.../accept не редиректят сами на /; они
 * только refetch me, App.tsx подхватывает обновлённое состояние и решает где
 * быть (C16-3: single navigate без flicker'a главной).
 */
function RedirectGuard() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user } = useMe();

  useEffect(() => {
    if (!user) return;

    if (user.deleted_at && location.pathname !== '/restore') {
      navigate('/restore', { replace: true });
      return;
    }
    if (
      !user.deleted_at
      && user.registration_required
      && location.pathname !== '/register'
    ) {
      navigate('/register', { replace: true });
      return;
    }
    if (
      !user.deleted_at
      && !user.registration_required
    ) {
      const pending = sessionStorage.getItem('pendingInviteToken');
      if (pending) {
        sessionStorage.removeItem('pendingInviteToken');
        navigate(`/invites/${pending}/accept`, { replace: true });
        return;
      }
      // Юзер зарегистрирован, не удалён — но он на /register или /restore.
      // Перенесём на /.
      if (location.pathname === '/register' || location.pathname === '/restore') {
        navigate('/', { replace: true });
      }
    }
  }, [user, location.pathname, navigate]);

  return null;
}

export function App() {
  const lp = useLaunchParams();
  const isDark = useSignal(miniApp.isDark);

  return (
    <AppRoot
      appearance={isDark ? 'dark' : 'light'}
      platform={['macos', 'ios'].includes(lp.tgWebAppPlatform) ? 'ios' : 'base'}
    >
      <HashRouter>
        <RedirectGuard />
        <div style={{ paddingBottom: 72 /* высота Tabbar */ }}>
          <Routes>
            {routes.map((route) => <Route key={route.path} {...route} />)}
            <Route path="*" element={<Navigate to="/"/>}/>
          </Routes>
        </div>
        <BottomTabBar />
      </HashRouter>
    </AppRoot>
  );
}
