import { Settings, Bell, Database, Shield, Cpu, Save } from 'lucide-react';

export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Settings</h1>
        <p className="text-surface-500 text-sm mt-0.5">System configuration and preferences</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* AI Pipeline Config */}
        <div className="card space-y-4">
          <div className="flex items-center gap-2 text-surface-200">
            <Cpu size={16} />
            <h3 className="text-sm font-semibold">AI Pipeline</h3>
          </div>
          <div className="space-y-3">
            <div>
              <label className="block text-sm text-surface-400 mb-1">Confidence Threshold</label>
              <input type="number" defaultValue="0.85" step="0.01" min="0.5" max="1.0" className="input w-32" />
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Primary Model</label>
              <select className="input" defaultValue="arcface">
                <option value="arcface">ArcFace (R100)</option>
                <option value="adaface">AdaFace (IR-101)</option>
              </select>
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Min Enrollment Photos</label>
              <input type="number" defaultValue="5" min="3" max="10" className="input w-32" />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Enable Super-Resolution</span>
              <input type="checkbox" defaultChecked className="accent-primary-600 w-4 h-4 cursor-pointer" />
            </div>
          </div>
        </div>

        {/* Security */}
        <div className="card space-y-4">
          <div className="flex items-center gap-2 text-surface-200">
            <Shield size={16} />
            <h3 className="text-sm font-semibold">Security</h3>
          </div>
          <div className="space-y-3">
            <div>
              <label className="block text-sm text-surface-400 mb-1">Access Token TTL</label>
              <input type="text" defaultValue="15 min" className="input w-40" disabled />
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Refresh Token TTL</label>
              <input type="text" defaultValue="7 days" className="input w-40" disabled />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">HMAC Device Auth</span>
              <span className="badge badge-success">Enabled</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Rate Limiting</span>
              <span className="badge badge-success">Active</span>
            </div>
          </div>
        </div>

        {/* Notifications */}
        <div className="card space-y-4">
          <div className="flex items-center gap-2 text-surface-200">
            <Bell size={16} />
            <h3 className="text-sm font-semibold">Notifications</h3>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Low attendance alerts</span>
              <input type="checkbox" defaultChecked className="accent-primary-600 w-4 h-4 cursor-pointer" />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Liveness failure alerts</span>
              <input type="checkbox" defaultChecked className="accent-primary-600 w-4 h-4 cursor-pointer" />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Device offline alerts</span>
              <input type="checkbox" defaultChecked className="accent-primary-600 w-4 h-4 cursor-pointer" />
            </div>
          </div>
        </div>

        {/* System Info */}
        <div className="card space-y-4">
          <div className="flex items-center gap-2 text-surface-200">
            <Database size={16} />
            <h3 className="text-sm font-semibold">System</h3>
          </div>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-surface-500">Version</span>
              <span className="text-surface-200 font-mono">2.0.0</span>
            </div>
            <div className="flex justify-between">
              <span className="text-surface-500">Backend</span>
              <span className="text-surface-200 font-mono">FastAPI + Celery</span>
            </div>
            <div className="flex justify-between">
              <span className="text-surface-500">Database</span>
              <span className="text-surface-200 font-mono">PostgreSQL + pgvector</span>
            </div>
            <div className="flex justify-between">
              <span className="text-surface-500">Cache</span>
              <span className="text-surface-200 font-mono">Redis 7</span>
            </div>
          </div>
        </div>
      </div>

      <button className="btn-primary">
        <Save size={16} />
        Save Settings
      </button>
    </div>
  );
}
