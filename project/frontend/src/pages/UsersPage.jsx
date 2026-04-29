import { useEffect, useMemo, useState } from 'react';
import { Users, Shield, Plus, RefreshCw, Pencil, Trash2, Server } from 'lucide-react';
import api from '../api/client';

const roleBadgeMap = {
  admin: 'bg-primary-600/15 text-primary-400',
  instructor: 'bg-accent-500/15 text-accent-400',
  student: 'bg-surface-600/30 text-surface-300',
  device: 'bg-warning-500/15 text-warning-400',
};

const roleTabs = [
  { value: '', label: 'All' },
  { value: 'admin', label: 'Admin' },
  { value: 'instructor', label: 'Instructor' },
  { value: 'student', label: 'Student' },
  { value: 'device', label: 'Device' },
];

function StatCard({ icon: Icon, label, value, colorClass }) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs uppercase tracking-wider text-surface-500">{label}</span>
        <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${colorClass}`}>
          <Icon size={14} />
        </div>
      </div>
      <p className="text-2xl font-bold text-surface-50 tracking-tight">{value}</p>
    </div>
  );
}

function ModalShell({ title, children, onClose }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center px-4" onClick={onClose}>
      <div className="w-full max-w-lg card-glass border border-surface-700 animate-fade-in" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-surface-100">{title}</h2>
          <button onClick={onClose} className="btn-secondary !px-2.5 !py-1.5" aria-label="Close modal">Close</button>
        </div>
        {children}
      </div>
    </div>
  );
}

function getCurrentUserId() {
  try {
    const token = sessionStorage.getItem('access_token');
    if (!token) return null;
    const payload = JSON.parse(atob(token.split('.')[1]));
    return Number(payload.sub);
  } catch {
    return null;
  }
}

async function parseApiError(err, fallback) {
  if (!err?.response) return err?.message || fallback;
  try {
    const payload = await err.response.json();
    return payload?.detail?.message || payload?.detail || payload?.message || fallback;
  } catch {
    return fallback;
  }
}

export default function UsersPage() {
  const [users, setUsers] = useState([]);
  const [allUsers, setAllUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [roleFilter, setRoleFilter] = useState('');

  const [createOpen, setCreateOpen] = useState(false);
  const [editUser, setEditUser] = useState(null);
  const [deleteUser, setDeleteUser] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const [createForm, setCreateForm] = useState({
    email: '',
    password: '',
    role: 'student',
    is_active: true,
  });

  const [editForm, setEditForm] = useState({
    email: '',
    role: 'student',
    is_active: true,
  });

  const currentUserId = useMemo(() => getCurrentUserId(), []);

  const stats = useMemo(() => {
    const total = allUsers.length;
    const active = allUsers.filter((u) => u.is_active).length;
    const privileged = allUsers.filter((u) => u.role === 'admin' || u.role === 'instructor').length;
    const device = allUsers.filter((u) => u.role === 'device').length;
    return { total, active, privileged, device };
  }, [allUsers]);

  const loadUsers = async () => {
    setLoading(true);
    setError('');
    try {
      const [filtered, all] = await Promise.all([
        api.getUsers({ role: roleFilter || undefined, limit: 200 }),
        api.getUsers({ limit: 200 }),
      ]);
      setUsers(filtered);
      setAllUsers(all);
    } catch (err) {
      setError(await parseApiError(err, 'Failed to load users.'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, [roleFilter]);

  const submitCreate = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await api.createUser({
        email: createForm.email.trim().toLowerCase(),
        password: createForm.password,
        role: createForm.role,
        is_active: createForm.is_active,
      });
      setCreateOpen(false);
      setCreateForm({ email: '', password: '', role: 'student', is_active: true });
      await loadUsers();
    } catch (err) {
      setError(await parseApiError(err, 'Failed to create user.'));
    } finally {
      setSubmitting(false);
    }
  };

  const openEdit = (user) => {
    setEditUser(user);
    setEditForm({
      email: user.email,
      role: user.role,
      is_active: user.is_active,
    });
  };

  const submitEdit = async (e) => {
    e.preventDefault();
    if (!editUser) return;
    setSubmitting(true);
    setError('');
    try {
      await api.updateUser(editUser.id, {
        email: editForm.email.trim().toLowerCase(),
        role: editForm.role,
        is_active: editForm.is_active,
      });
      setEditUser(null);
      await loadUsers();
    } catch (err) {
      setError(await parseApiError(err, 'Failed to update user.'));
    } finally {
      setSubmitting(false);
    }
  };

  const submitDelete = async () => {
    if (!deleteUser) return;
    setSubmitting(true);
    setError('');
    try {
      await api.deleteUser(deleteUser.id);
      setDeleteUser(null);
      await loadUsers();
    } catch (err) {
      setError(await parseApiError(err, 'Failed to delete user.'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="relative card-glass border border-surface-800 overflow-hidden">
        <div className="absolute -top-16 -right-16 w-56 h-56 rounded-full bg-primary-600/10 blur-3xl" />
        <div className="relative flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Users</h1>
            <p className="text-surface-400 text-sm mt-1">Real account directory and role management</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center p-1 bg-surface-900/80 rounded-lg border border-surface-700">
              {roleTabs.map((tab) => (
                <button
                  key={tab.value || 'all'}
                  onClick={() => setRoleFilter(tab.value)}
                  className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors cursor-pointer ${
                    roleFilter === tab.value
                      ? 'bg-primary-600/20 text-primary-300'
                      : 'text-surface-400 hover:text-surface-100'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <button className="btn-secondary" onClick={loadUsers} aria-label="Refresh users">
              <RefreshCw size={15} />
              Refresh
            </button>
            <button className="btn-primary" onClick={() => setCreateOpen(true)}>
              <Plus size={16} />
              Add User
            </button>
          </div>
        </div>
      </div>

      {error ? (
        <div className="px-3 py-2 rounded-lg bg-danger-500/10 border border-danger-500/20 text-danger-300 text-sm">
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard icon={Users} label="Total Users" value={stats.total} colorClass="bg-primary-600/15 text-primary-400" />
        <StatCard icon={Shield} label="Active Users" value={stats.active} colorClass="bg-accent-500/15 text-accent-400" />
        <StatCard icon={Server} label="Privileged" value={stats.privileged} colorClass="bg-surface-600/20 text-surface-300" />
        <StatCard icon={Users} label="Device Accounts" value={stats.device} colorClass="bg-warning-500/15 text-warning-400" />
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="table min-w-[760px]">
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Created</th>
                <th className="w-[170px]">Actions</th>
              </tr>
            </thead>
            <tbody>
              {!loading && users.map((user) => (
                <tr key={user.id}>
                  <td className="font-medium text-surface-100">{user.email}</td>
                  <td>
                    <span className={`badge ${roleBadgeMap[user.role] || 'bg-surface-700 text-surface-300'}`}>
                      {user.role}
                    </span>
                  </td>
                  <td>
                    <span className={`badge ${user.is_active ? 'badge-success' : 'badge-danger'}`}>
                      {user.is_active ? 'active' : 'inactive'}
                    </span>
                  </td>
                  <td className="text-surface-500 text-xs">
                    {new Date(user.created_at).toLocaleDateString()}
                  </td>
                  <td>
                    <div className="flex items-center gap-2">
                      <button className="btn-secondary !px-2.5 !py-1.5" onClick={() => openEdit(user)}>
                        <Pencil size={13} />
                        Edit
                      </button>
                      <button
                        className="btn-secondary !px-2.5 !py-1.5 text-danger-300 border-danger-500/30 hover:bg-danger-500/15"
                        onClick={() => setDeleteUser(user)}
                        disabled={currentUserId === user.id}
                        title={currentUserId === user.id ? 'You cannot delete your own account' : 'Delete user'}
                      >
                        <Trash2 size={13} />
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {!loading && users.length === 0 ? (
          <div className="text-center py-14 text-surface-500">
            <Users size={36} className="mx-auto mb-3 opacity-40" />
            <p className="font-medium">No users found</p>
            <p className="text-sm mt-1">Try another role filter or create a new user</p>
          </div>
        ) : null}

        {loading ? (
          <div className="text-center py-12 text-surface-500">Loading users...</div>
        ) : null}
      </div>

      {createOpen ? (
        <ModalShell title="Create User" onClose={() => setCreateOpen(false)}>
          <form className="space-y-4" onSubmit={submitCreate}>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Email</label>
              <input
                type="email"
                required
                value={createForm.email}
                onChange={(e) => setCreateForm((prev) => ({ ...prev, email: e.target.value }))}
                className="input"
              />
            </div>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Password</label>
              <input
                type="password"
                required
                minLength={8}
                value={createForm.password}
                onChange={(e) => setCreateForm((prev) => ({ ...prev, password: e.target.value }))}
                className="input"
              />
            </div>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Role</label>
              <select
                value={createForm.role}
                onChange={(e) => setCreateForm((prev) => ({ ...prev, role: e.target.value }))}
                className="input"
              >
                <option value="admin">admin</option>
                <option value="instructor">instructor</option>
                <option value="student">student</option>
                <option value="device">device</option>
              </select>
            </div>
            <label className="flex items-center justify-between text-sm text-surface-300">
              Active user
              <input
                type="checkbox"
                checked={createForm.is_active}
                onChange={(e) => setCreateForm((prev) => ({ ...prev, is_active: e.target.checked }))}
                className="accent-primary-600 w-4 h-4 cursor-pointer"
              />
            </label>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button type="button" className="btn-secondary" onClick={() => setCreateOpen(false)}>Cancel</button>
              <button type="submit" className="btn-primary" disabled={submitting}>
                {submitting ? 'Creating...' : 'Create User'}
              </button>
            </div>
          </form>
        </ModalShell>
      ) : null}

      {editUser ? (
        <ModalShell title="Edit User" onClose={() => setEditUser(null)}>
          <form className="space-y-4" onSubmit={submitEdit}>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Email</label>
              <input
                type="email"
                required
                value={editForm.email}
                onChange={(e) => setEditForm((prev) => ({ ...prev, email: e.target.value }))}
                className="input"
              />
            </div>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Role</label>
              <select
                value={editForm.role}
                onChange={(e) => setEditForm((prev) => ({ ...prev, role: e.target.value }))}
                className="input"
              >
                <option value="admin">admin</option>
                <option value="instructor">instructor</option>
                <option value="student">student</option>
                <option value="device">device</option>
              </select>
            </div>
            <label className="flex items-center justify-between text-sm text-surface-300">
              Active user
              <input
                type="checkbox"
                checked={editForm.is_active}
                onChange={(e) => setEditForm((prev) => ({ ...prev, is_active: e.target.checked }))}
                className="accent-primary-600 w-4 h-4 cursor-pointer"
              />
            </label>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button type="button" className="btn-secondary" onClick={() => setEditUser(null)}>Cancel</button>
              <button type="submit" className="btn-primary" disabled={submitting}>
                {submitting ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          </form>
        </ModalShell>
      ) : null}

      {deleteUser ? (
        <ModalShell title="Delete User" onClose={() => setDeleteUser(null)}>
          <div className="space-y-5">
            <p className="text-sm text-surface-300">
              Delete account <span className="font-semibold text-surface-100">{deleteUser.email}</span>? This action cannot be undone.
            </p>
            <div className="flex items-center justify-end gap-2">
              <button className="btn-secondary" onClick={() => setDeleteUser(null)}>Cancel</button>
              <button className="btn-primary bg-danger-600 hover:bg-danger-500" onClick={submitDelete} disabled={submitting}>
                {submitting ? 'Deleting...' : 'Delete User'}
              </button>
            </div>
          </div>
        </ModalShell>
      ) : null}
    </div>
  );
}
