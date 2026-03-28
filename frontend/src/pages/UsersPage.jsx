import { Users, Shield, Plus, MoreHorizontal } from 'lucide-react';

const mockUsers = [
  { id: 1, name: 'Admin User', email: 'admin@attendai.io', role: 'admin', status: 'active', lastLogin: '2 min ago' },
  { id: 2, name: 'Dr. Ahmad', email: 'ahmad@university.edu', role: 'instructor', status: 'active', lastLogin: '15 min ago' },
  { id: 3, name: 'Dr. Fatima', email: 'fatima@university.edu', role: 'instructor', status: 'active', lastLogin: '1 hour ago' },
  { id: 4, name: 'Lab Camera A', email: 'cam-a@device.local', role: 'device', status: 'active', lastLogin: '30 sec ago' },
  { id: 5, name: 'Prof. Sarah', email: 'sarah@university.edu', role: 'instructor', status: 'inactive', lastLogin: '3 days ago' },
];

const roleBadgeMap = {
  admin: 'bg-primary-600/15 text-primary-400',
  instructor: 'bg-accent-500/15 text-accent-400',
  student: 'bg-surface-600/15 text-surface-400',
  device: 'bg-warning-500/15 text-warning-400',
};

export default function UsersPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Users</h1>
          <p className="text-surface-500 text-sm mt-0.5">{mockUsers.length} system users</p>
        </div>
        <button className="btn-primary">
          <Plus size={16} />
          Add User
        </button>
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="table">
          <thead>
            <tr>
              <th>User</th>
              <th>Email</th>
              <th>Role</th>
              <th>Status</th>
              <th>Last Login</th>
              <th className="w-10"></th>
            </tr>
          </thead>
          <tbody>
            {mockUsers.map((user) => (
              <tr key={user.id}>
                <td>
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-surface-700 flex items-center justify-center text-surface-400">
                      {user.role === 'device' ? <Shield size={14} /> : <Users size={14} />}
                    </div>
                    <span className="font-medium text-surface-100">{user.name}</span>
                  </div>
                </td>
                <td className="text-surface-400">{user.email}</td>
                <td>
                  <span className={`badge ${roleBadgeMap[user.role]}`}>{user.role}</span>
                </td>
                <td>
                  <span className={`badge ${user.status === 'active' ? 'badge-success' : 'badge-danger'}`}>
                    {user.status}
                  </span>
                </td>
                <td className="text-surface-500 text-sm">{user.lastLogin}</td>
                <td>
                  <button className="p-1.5 rounded-md hover:bg-surface-700 text-surface-500 hover:text-surface-300 transition-colors cursor-pointer" aria-label="More options">
                    <MoreHorizontal size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
