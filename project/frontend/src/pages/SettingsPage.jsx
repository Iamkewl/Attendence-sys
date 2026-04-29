import { useEffect, useState } from 'react';
import { Bell, Database, Shield, Cpu, Save, RefreshCw } from 'lucide-react';
import api from '../api/client';

async function parseApiError(err, fallback) {
  if (!err?.response) return err?.message || fallback;
  try {
    const payload = await err.response.json();
    return payload?.detail?.message || payload?.detail || payload?.message || fallback;
  } catch {
    return fallback;
  }
}

export default function SettingsPage() {
  const [settings, setSettings] = useState({
    confidence_threshold: 0.85,
    face_match_relaxed_threshold: 0.78,
    face_match_margin: 0.08,
    primary_model: 'lvface',
    min_face_size_px: 48,
    min_face_area_ratio: 0.0025,
    min_blur_variance: 45,
    min_face_quality_score: 0.18,
    min_enrollment_photos: 5,
    enable_super_resolution: true,
    notify_low_attendance: true,
    notify_liveness_failures: true,
    notify_device_offline: true,
    access_token_ttl_minutes: 15,
    refresh_token_ttl_days: 7,
    hmac_device_auth_enabled: true,
    rate_limiting_enabled: true,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [rollingBackId, setRollingBackId] = useState(null);
  const [rollingBackTemplateId, setRollingBackTemplateId] = useState(null);
  const [history, setHistory] = useState([]);
  const [governance, setGovernance] = useState({
    fairness: { available: false, report: null },
    template_age_histogram: [],
    template_refresh: { next_auto_refresh_due: null, recent: [] },
    retention: {},
    drift: { alerts: [] },
  });
  const [governanceLoading, setGovernanceLoading] = useState(true);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  const loadSettingsHistory = async () => {
    try {
      const items = await api.getSystemSettingsHistory({ limit: 12 });
      setHistory(items || []);
    } catch {
      setHistory([]);
    }
  };

  const loadGovernanceOverview = async () => {
    try {
      const data = await api.getGovernanceOverview();
      setGovernance(data || {
        fairness: { available: false, report: null },
        template_age_histogram: [],
        template_refresh: { next_auto_refresh_due: null, recent: [] },
        retention: {},
        drift: { alerts: [] },
      });
      return data;
    } catch {
      setGovernance({
        fairness: { available: false, report: null },
        template_age_histogram: [],
        template_refresh: { next_auto_refresh_due: null, recent: [] },
        retention: {},
        drift: { alerts: [] },
      });
      return null;
    } finally {
      setGovernanceLoading(false);
    }
  };

  const loadSettings = async () => {
    setLoading(true);
    setGovernanceLoading(true);
    setError('');
    setSaved(false);
    try {
      const [data] = await Promise.all([
        api.getSystemSettings(),
        loadSettingsHistory(),
        loadGovernanceOverview(),
      ]);
      setSettings((prev) => ({ ...prev, ...data }));
    } catch (err) {
      setError(await parseApiError(err, 'Failed to load settings.'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSettings();
  }, []);

  const saveSettings = async () => {
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const payload = {
        confidence_threshold: Number(settings.confidence_threshold),
        face_match_relaxed_threshold: Number(settings.face_match_relaxed_threshold),
        face_match_margin: Number(settings.face_match_margin),
        primary_model: settings.primary_model,
        min_face_size_px: Number(settings.min_face_size_px),
        min_face_area_ratio: Number(settings.min_face_area_ratio),
        min_blur_variance: Number(settings.min_blur_variance),
        min_face_quality_score: Number(settings.min_face_quality_score),
        min_enrollment_photos: Number(settings.min_enrollment_photos),
        enable_super_resolution: Boolean(settings.enable_super_resolution),
        notify_low_attendance: Boolean(settings.notify_low_attendance),
        notify_liveness_failures: Boolean(settings.notify_liveness_failures),
        notify_device_offline: Boolean(settings.notify_device_offline),
      };
      const data = await api.updateSystemSettings(payload);
      setSettings((prev) => ({ ...prev, ...data }));
      setSaved(true);
      await loadSettingsHistory();
    } catch (err) {
      setError(await parseApiError(err, 'Failed to save settings.'));
    } finally {
      setSaving(false);
    }
  };

  const rollbackSettings = async (revisionId) => {
    setRollingBackId(revisionId);
    setError('');
    setSaved(false);
    try {
      const response = await api.rollbackSystemSettings(revisionId);
      const next = response?.settings || {};
      setSettings((prev) => ({ ...prev, ...next }));
      setSaved(true);
      await loadSettingsHistory();
    } catch (err) {
      setError(await parseApiError(err, 'Failed to rollback settings.'));
    } finally {
      setRollingBackId(null);
    }
  };

  const rollbackTemplateRefresh = async (auditLogId) => {
    setRollingBackTemplateId(auditLogId);
    setError('');
    setSaved(false);
    try {
      await api.rollbackTemplateRefresh(auditLogId);
      await loadGovernanceOverview();
      setSaved(true);
    } catch (err) {
      setError(await parseApiError(err, 'Failed to rollback template refresh.'));
    } finally {
      setRollingBackTemplateId(null);
    }
  };

  const formatPercent = (value) => {
    if (value === null || value === undefined) return '-';
    const numeric = Number(value);
    if (Number.isNaN(numeric)) return '-';
    return `${(numeric * 100).toFixed(2)}%`;
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Settings</h1>
          <p className="text-surface-500 text-sm mt-0.5">System configuration and preferences</p>
        </div>
        <button className="btn-secondary" onClick={loadSettings} disabled={loading || saving}>
          <RefreshCw size={15} />
          Refresh
        </button>
      </div>

      {error ? (
        <div className="px-3 py-2 rounded-lg bg-danger-500/10 border border-danger-500/20 text-danger-300 text-sm">
          {error}
        </div>
      ) : null}

      {saved ? (
        <div className="px-3 py-2 rounded-lg bg-accent-500/10 border border-accent-500/20 text-accent-300 text-sm">
          Settings saved successfully.
        </div>
      ) : null}

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
              <input
                type="number"
                value={settings.confidence_threshold}
                onChange={(e) => setSettings((prev) => ({ ...prev, confidence_threshold: e.target.value }))}
                step="0.01"
                min="0.5"
                max="1.0"
                className="input w-32"
                disabled={loading || saving}
              />
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Relaxed Match Threshold</label>
              <input
                type="number"
                value={settings.face_match_relaxed_threshold}
                onChange={(e) => setSettings((prev) => ({ ...prev, face_match_relaxed_threshold: e.target.value }))}
                step="0.01"
                min="0.5"
                max="1.0"
                className="input w-32"
                disabled={loading || saving}
              />
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Required Match Margin</label>
              <input
                type="number"
                value={settings.face_match_margin}
                onChange={(e) => setSettings((prev) => ({ ...prev, face_match_margin: e.target.value }))}
                step="0.01"
                min="0"
                max="0.25"
                className="input w-32"
                disabled={loading || saving}
              />
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Primary Model</label>
              <select
                className="input"
                value={settings.primary_model}
                onChange={(e) => setSettings((prev) => ({ ...prev, primary_model: e.target.value }))}
                disabled={loading || saving}
              >
                <option value="lvface">LVFace (ViT, 512d)</option>
              </select>
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Min Face Size (px)</label>
              <input
                type="number"
                value={settings.min_face_size_px}
                onChange={(e) => setSettings((prev) => ({ ...prev, min_face_size_px: e.target.value }))}
                step="1"
                min="24"
                max="256"
                className="input w-32"
                disabled={loading || saving}
              />
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Min Face Area Ratio</label>
              <input
                type="number"
                value={settings.min_face_area_ratio}
                onChange={(e) => setSettings((prev) => ({ ...prev, min_face_area_ratio: e.target.value }))}
                step="0.0001"
                min="0.0005"
                max="0.08"
                className="input w-32"
                disabled={loading || saving}
              />
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Min Blur Variance</label>
              <input
                type="number"
                value={settings.min_blur_variance}
                onChange={(e) => setSettings((prev) => ({ ...prev, min_blur_variance: e.target.value }))}
                step="1"
                min="5"
                max="500"
                className="input w-32"
                disabled={loading || saving}
              />
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Min Face Quality Score</label>
              <input
                type="number"
                value={settings.min_face_quality_score}
                onChange={(e) => setSettings((prev) => ({ ...prev, min_face_quality_score: e.target.value }))}
                step="0.01"
                min="0"
                max="1"
                className="input w-32"
                disabled={loading || saving}
              />
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Min Enrollment Photos</label>
              <input
                type="number"
                value={settings.min_enrollment_photos}
                onChange={(e) => setSettings((prev) => ({ ...prev, min_enrollment_photos: e.target.value }))}
                min="3"
                max="10"
                className="input w-32"
                disabled={loading || saving}
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Enable Super-Resolution</span>
              <input
                type="checkbox"
                checked={Boolean(settings.enable_super_resolution)}
                onChange={(e) => setSettings((prev) => ({ ...prev, enable_super_resolution: e.target.checked }))}
                className="accent-primary-600 w-4 h-4 cursor-pointer"
                disabled={loading || saving}
              />
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
              <input type="text" value={`${settings.access_token_ttl_minutes} min`} className="input w-40" disabled />
            </div>
            <div>
              <label className="block text-sm text-surface-400 mb-1">Refresh Token TTL</label>
              <input type="text" value={`${settings.refresh_token_ttl_days} days`} className="input w-40" disabled />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">HMAC Device Auth</span>
              <span className={`badge ${settings.hmac_device_auth_enabled ? 'badge-success' : 'badge-danger'}`}>
                {settings.hmac_device_auth_enabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Rate Limiting</span>
              <span className={`badge ${settings.rate_limiting_enabled ? 'badge-success' : 'badge-danger'}`}>
                {settings.rate_limiting_enabled ? 'Active' : 'Disabled'}
              </span>
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
              <input
                type="checkbox"
                checked={Boolean(settings.notify_low_attendance)}
                onChange={(e) => setSettings((prev) => ({ ...prev, notify_low_attendance: e.target.checked }))}
                className="accent-primary-600 w-4 h-4 cursor-pointer"
                disabled={loading || saving}
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Liveness failure alerts</span>
              <input
                type="checkbox"
                checked={Boolean(settings.notify_liveness_failures)}
                onChange={(e) => setSettings((prev) => ({ ...prev, notify_liveness_failures: e.target.checked }))}
                className="accent-primary-600 w-4 h-4 cursor-pointer"
                disabled={loading || saving}
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Device offline alerts</span>
              <input
                type="checkbox"
                checked={Boolean(settings.notify_device_offline)}
                onChange={(e) => setSettings((prev) => ({ ...prev, notify_device_offline: e.target.checked }))}
                className="accent-primary-600 w-4 h-4 cursor-pointer"
                disabled={loading || saving}
              />
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

        {/* Settings History */}
        <div className="card space-y-4 lg:col-span-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-surface-200">Settings History</h3>
            <span className="text-xs text-surface-500">Latest revisions</span>
          </div>
          {history.length === 0 ? (
            <p className="text-sm text-surface-500">No settings revisions yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="table min-w-[720px]">
                <thead>
                  <tr>
                    <th>Revision</th>
                    <th>Action</th>
                    <th>Actor</th>
                    <th>When</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((item) => (
                    <tr key={item.id}>
                      <td className="font-medium text-surface-100">#{item.id}</td>
                      <td className="text-surface-300">{item.action}</td>
                      <td className="text-surface-300">{item.actor_user_id ?? '-'}</td>
                      <td className="text-surface-400">
                        {item.timestamp ? new Date(item.timestamp).toLocaleString() : '-'}
                      </td>
                      <td>
                        <button
                          className="btn-secondary !px-2 !py-1 text-xs"
                          onClick={() => rollbackSettings(item.id)}
                          disabled={loading || saving || rollingBackId === item.id}
                        >
                          {rollingBackId === item.id ? 'Rolling back...' : 'Rollback'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card space-y-4 lg:col-span-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-surface-200">Governance Dashboard</h3>
            <span className="text-xs text-surface-500">Fairness, retention, drift, lifecycle</span>
          </div>

          {governanceLoading ? (
            <p className="text-sm text-surface-500">Loading governance status...</p>
          ) : (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                <div className="rounded-lg border border-surface-700/70 p-3 space-y-2">
                  <p className="text-xs uppercase tracking-wide text-surface-500">Fairness Disparity Ratios</p>
                  {governance?.fairness?.available && governance?.fairness?.report ? (
                    <div className="space-y-1 text-sm">
                      <div className="flex justify-between">
                        <span className="text-surface-500">Department Recall</span>
                        <span className="text-surface-200 font-mono">{(governance.fairness.report?.disparity_ratios?.department?.recall || '-')}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-surface-500">Department FNMR</span>
                        <span className="text-surface-200 font-mono">{(governance.fairness.report?.disparity_ratios?.department?.fnmr || '-')}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-surface-500">Enrollment-Year Recall</span>
                        <span className="text-surface-200 font-mono">{(governance.fairness.report?.disparity_ratios?.enrollment_year?.recall || '-')}</span>
                      </div>
                    </div>
                  ) : (
                    <p className="text-sm text-surface-500">No fairness audit report yet.</p>
                  )}
                </div>

                <div className="rounded-lg border border-surface-700/70 p-3 space-y-2">
                  <p className="text-xs uppercase tracking-wide text-surface-500">Template Age Histogram</p>
                  {(governance?.template_age_histogram || []).length === 0 ? (
                    <p className="text-sm text-surface-500">No active templates found.</p>
                  ) : (
                    <div className="space-y-1 text-sm">
                      {(governance.template_age_histogram || []).map((item) => (
                        <div key={item.bucket} className="flex justify-between">
                          <span className="text-surface-500">{item.bucket} days</span>
                          <span className="text-surface-200 font-mono">{item.count}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <p className="text-xs text-surface-500">
                    Next auto-refresh due: {governance?.template_refresh?.next_auto_refresh_due || '-'}
                  </p>
                </div>

                <div className="rounded-lg border border-surface-700/70 p-3 space-y-2">
                  <p className="text-xs uppercase tracking-wide text-surface-500">Retention and Drift</p>
                  <div className="space-y-1 text-sm">
                    <div className="flex justify-between">
                      <span className="text-surface-500">Pending Embeddings</span>
                      <span className="text-surface-200 font-mono">{governance?.retention?.pending_embedding_count ?? 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-surface-500">Pending Snapshots</span>
                      <span className="text-surface-200 font-mono">{governance?.retention?.pending_snapshot_count ?? 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-surface-500">Drift Alerts</span>
                      <span className="text-surface-200 font-mono">{(governance?.drift?.alerts || []).length}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-surface-500">Drift Threshold</span>
                      <span className="text-surface-200 font-mono">{formatPercent(governance?.drift?.threshold)}</span>
                    </div>
                  </div>
                  <p className="text-xs text-surface-500">
                    Next purge: {governance?.retention?.next_purge_at ? new Date(governance.retention.next_purge_at).toLocaleString() : '-'}
                  </p>
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold text-surface-200">Camera Drift Alerts</h4>
                  <span className="text-xs text-surface-500">Most recent alerts</span>
                </div>
                {(governance?.drift?.alerts || []).length === 0 ? (
                  <p className="text-sm text-surface-500">No drift alerts in the current window.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="table min-w-[760px]">
                      <thead>
                        <tr>
                          <th>ID</th>
                          <th>Camera</th>
                          <th>Current Rate</th>
                          <th>Baseline Rate</th>
                          <th>Drop Ratio</th>
                          <th>When</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(governance?.drift?.alerts || []).slice(0, 10).map((alert) => (
                          <tr key={alert.id}>
                            <td className="font-medium text-surface-100">#{alert.id}</td>
                            <td className="text-surface-300">{alert.camera_id}</td>
                            <td className="text-surface-300">{formatPercent(alert.current_rate)}</td>
                            <td className="text-surface-300">{formatPercent(alert.baseline_rate)}</td>
                            <td className="text-surface-300">{formatPercent(alert.drop_ratio)}</td>
                            <td className="text-surface-400">
                              {alert.detected_at ? new Date(alert.detected_at).toLocaleString() : '-'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold text-surface-200">Auto-Refresh Activity Log</h4>
                  <span className="text-xs text-surface-500">Recent refresh + rollback actions</span>
                </div>
                {(governance?.template_refresh?.recent || []).length === 0 ? (
                  <p className="text-sm text-surface-500">No auto-refresh activity recorded yet.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="table min-w-[760px]">
                      <thead>
                        <tr>
                          <th>ID</th>
                          <th>Student</th>
                          <th>Action</th>
                          <th>Confidence</th>
                          <th>Quality</th>
                          <th>When</th>
                          <th>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(governance?.template_refresh?.recent || []).map((item) => (
                          <tr key={item.id}>
                            <td className="font-medium text-surface-100">#{item.id}</td>
                            <td className="text-surface-300">{item.student_id}</td>
                            <td className="text-surface-300">{item.action}</td>
                            <td className="text-surface-300">{formatPercent(item.refresh_confidence)}</td>
                            <td className="text-surface-300">{formatPercent(item.refresh_quality)}</td>
                            <td className="text-surface-400">
                              {item.refreshed_at ? new Date(item.refreshed_at).toLocaleString() : '-'}
                            </td>
                            <td>
                              {item.action === 'refresh' ? (
                                <button
                                  className="btn-secondary !px-2 !py-1 text-xs"
                                  onClick={() => rollbackTemplateRefresh(item.id)}
                                  disabled={loading || saving || rollingBackTemplateId === item.id}
                                >
                                  {rollingBackTemplateId === item.id ? 'Rolling back...' : 'Rollback'}
                                </button>
                              ) : (
                                <span className="text-xs text-surface-500">-</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      <button className="btn-primary" onClick={saveSettings} disabled={loading || saving}>
        <Save size={16} />
        {saving ? 'Saving...' : 'Save Settings'}
      </button>
    </div>
  );
}
