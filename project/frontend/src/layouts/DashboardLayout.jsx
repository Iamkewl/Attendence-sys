import { NavLink, Outlet, Navigate } from 'react-router-dom';
import {
  LayoutDashboard,
  Users,
  GraduationCap,
  CalendarClock,
  Radio,
  FlaskConical,
  Settings,
  LogOut,
  Shield,
} from 'lucide-react';
import api from '../api/client';

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/students', icon: GraduationCap, label: 'Students' },
  { to: '/attendance', icon: CalendarClock, label: 'Attendance' },
  { to: '/live', icon: Radio, label: 'Live Feed' },
  { to: '/testing', icon: FlaskConical, label: 'Testing Lab' },
  { to: '/users', icon: Users, label: 'Users' },
  { to: '/settings', icon: Settings, label: 'Settings' },
];

export default function DashboardLayout() {
  if (!api.isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  const handleLogout = () => {
    api.logout();
    window.location.href = '/login';
  };

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ── Sidebar ──────────────────────── */}
      <aside className="w-64 flex-shrink-0 flex flex-col bg-surface-900 border-r border-surface-800">
        {/* Logo */}
        <div className="flex items-center gap-3 px-5 py-5 border-b border-surface-800">
          <div className="w-9 h-9 rounded-lg bg-primary-600 flex items-center justify-center">
            <Shield size={20} className="text-white" />
          </div>
          <div>
            <h1 className="text-sm font-bold text-surface-50 tracking-tight">AttendAI</h1>
            <p className="text-[10px] text-surface-500 font-medium tracking-widest uppercase">V2 Dashboard</p>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 cursor-pointer ${
                  isActive
                    ? 'bg-primary-600/15 text-primary-400 shadow-sm'
                    : 'text-surface-400 hover:text-surface-100 hover:bg-surface-800'
                }`
              }
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Logout */}
        <div className="px-3 py-4 border-t border-surface-800">
          <button
            onClick={handleLogout}
            className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm font-medium text-surface-400 hover:text-danger-400 hover:bg-surface-800 transition-all duration-200 cursor-pointer"
          >
            <LogOut size={18} />
            Logout
          </button>
        </div>
      </aside>

      {/* ── Main Content ─────────────────── */}
      <main className="flex-1 overflow-y-auto bg-surface-950">
        <div className="p-6 max-w-7xl mx-auto animate-fade-in">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
