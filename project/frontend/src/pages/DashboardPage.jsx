import { useEffect, useMemo, useState } from 'react';
import {
  Users,
  Camera,
  TrendingUp,
  AlertTriangle,
  CheckCircle2,
  Radio,
  RefreshCw,
} from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
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

function StatCard({ icon: Icon, label, value, trend, trendLabel, color }) {
  const colorMap = {
    primary: 'bg-primary-600/10 text-primary-400',
    accent: 'bg-accent-500/10 text-accent-400',
    warning: 'bg-warning-500/10 text-warning-400',
    danger: 'bg-danger-500/10 text-danger-400',
  };

  return (
    <div className="stat-card group hover:border-surface-600 transition-all duration-200">
      <div className="flex items-center justify-between">
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${colorMap[color]}`}>
          <Icon size={20} />
        </div>
        {trend && (
          <div className="flex items-center gap-1 text-xs text-accent-400 font-medium">
            <TrendingUp size={12} />
            {trendLabel}
          </div>
        )}
      </div>
      <div className="stat-value text-surface-50">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function ConfidenceBadge({ confidence }) {
  const pct = Math.round(confidence * 100);
  const cls = pct >= 95 ? 'badge-success' : pct >= 85 ? 'badge-warning' : 'badge-danger';
  return <span className={`badge ${cls}`}>{pct}%</span>;
}

export default function DashboardPage() {
  const DETECTIONS_PAGE_SIZE = 12;
  const [summary, setSummary] = useState({
    total_students: 0,
    present_today: 0,
    active_cameras: 0,
    liveness_failures: 0,
    attendance_rate: 0,
  });
  const [trendData, setTrendData] = useState([]);
  const [recentDetections, setRecentDetections] = useState([]);
  const [detectionsOffset, setDetectionsOffset] = useState(0);
  const [aiStatus, setAiStatus] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const aiRows = useMemo(
    () => [
      { name: 'YOLO Detector', status: aiStatus?.detector_loaded ? 'ready' : 'error' },
      { name: 'LVFace', status: aiStatus?.lvface_available ? 'ready' : 'warning' },
      { name: 'Anti-Spoof', status: aiStatus?.liveness_enabled ? 'ready' : 'idle' },
      {
        name: 'Super-Resolution',
        status: aiStatus?.super_resolution_enabled && aiStatus?.sr_func_available ? 'ready' : 'idle',
      },
    ],
    [aiStatus]
  );

  const loadRecentDetections = async (offset = 0) => {
    const detectionsResp = await api.getRecentDetections({
      limit: DETECTIONS_PAGE_SIZE,
      offset,
    });
    setRecentDetections(detectionsResp || []);
  };

  const loadDashboard = async () => {
    setLoading(true);
    setError('');
    try {
      const [summaryResp, trendResp, aiResp] = await Promise.all([
        api.getDashboardSummary(),
        api.getDashboardTrend({ hours: 4, bucket_minutes: 30 }),
        api.getAIStatus(),
      ]);
      setSummary(summaryResp || {});
      setTrendData((trendResp && trendResp.length > 0) ? trendResp : [{ time: '00:00', present: 0 }]);
      setAiStatus(aiResp || {});
      setDetectionsOffset(0);
      await loadRecentDetections(0);
    } catch (err) {
      setError(await parseApiError(err, 'Failed to load dashboard data.'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDashboard();
  }, []);

  const canGoPrev = detectionsOffset > 0;
  const canGoNext = recentDetections.length === DETECTIONS_PAGE_SIZE;

  const goPrevDetections = async () => {
    if (!canGoPrev) return;
    const nextOffset = Math.max(0, detectionsOffset - DETECTIONS_PAGE_SIZE);
    setDetectionsOffset(nextOffset);
    try {
      await loadRecentDetections(nextOffset);
    } catch (err) {
      setError(await parseApiError(err, 'Failed to load previous detections page.'));
    }
  };

  const goNextDetections = async () => {
    if (!canGoNext) return;
    const nextOffset = detectionsOffset + DETECTIONS_PAGE_SIZE;
    setDetectionsOffset(nextOffset);
    try {
      await loadRecentDetections(nextOffset);
    } catch (err) {
      setError(await parseApiError(err, 'Failed to load next detections page.'));
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Dashboard</h1>
          <p className="text-surface-500 text-sm mt-0.5">Real-time attendance monitoring</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-secondary !px-2.5 !py-1.5" onClick={loadDashboard} disabled={loading}>
            <RefreshCw size={14} />
            Refresh
          </button>
          <div className="pulse-dot" />
          <span className="text-xs text-accent-400 font-medium">System Online</span>
        </div>
      </div>

      {error ? (
        <div className="px-3 py-2 rounded-lg bg-danger-500/10 border border-danger-500/20 text-danger-300 text-sm">
          {error}
        </div>
      ) : null}

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Users} label="Total Students" value={summary.total_students || 0} color="primary" />
        <StatCard
          icon={CheckCircle2}
          label="Present Today"
          value={summary.present_today || 0}
          trend
          trendLabel={`${summary.attendance_rate || 0}%`}
          color="accent"
        />
        <StatCard icon={Camera} label="Active Cameras" value={summary.active_cameras || 0} color="warning" />
        <StatCard icon={AlertTriangle} label="Liveness Failures" value={summary.liveness_failures || 0} color="danger" />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Attendance Trend */}
        <div className="col-span-2 card">
          <h3 className="text-sm font-semibold text-surface-200 mb-4">Attendance Trend — Today</h3>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={trendData}>
              <defs>
                <linearGradient id="gradientPresent" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#6366f1" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="time"
                axisLine={false}
                tickLine={false}
                tick={{ fill: '#64748b', fontSize: 12 }}
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fill: '#64748b', fontSize: 12 }}
              />
              <Tooltip
                contentStyle={{
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '8px',
                  boxShadow: '0 4px 24px rgba(0,0,0,0.3)',
                }}
                labelStyle={{ color: '#94a3b8' }}
                itemStyle={{ color: '#a5b4fc' }}
              />
              <Area
                type="monotone"
                dataKey="present"
                stroke="#6366f1"
                strokeWidth={2}
                fill="url(#gradientPresent)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* AI Status */}
        <div className="card space-y-4">
          <h3 className="text-sm font-semibold text-surface-200">AI Pipeline</h3>
          {aiRows.map(({ name, status }) => (
            <div key={name} className="flex items-center justify-between">
              <span className="text-sm text-surface-300">{name}</span>
              <span className={`badge ${status === 'ready' ? 'badge-success' : status === 'error' ? 'badge-danger' : 'badge-warning'}`}>
                {status}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Recent Detections */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-surface-200">Recent Detections</h3>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <Radio size={14} className="text-accent-400" />
              <span className="text-xs text-accent-400 font-medium">Live</span>
            </div>
            <div className="flex items-center gap-1">
              <button
                className="btn-secondary !px-2 !py-1 text-xs"
                onClick={goPrevDetections}
                disabled={!canGoPrev || loading}
              >
                Prev
              </button>
              <button
                className="btn-secondary !px-2 !py-1 text-xs"
                onClick={goNextDetections}
                disabled={!canGoNext || loading}
              >
                Next
              </button>
            </div>
          </div>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Student</th>
              <th>Course</th>
              <th>Time</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody>
            {recentDetections.map((det) => (
              <tr key={det.id}>
                <td className="font-medium text-surface-100">{det.name}</td>
                <td className="text-surface-400">{det.course}</td>
                <td className="text-surface-400">{det.time ? new Date(det.time).toLocaleTimeString() : '-'}</td>
                <td><ConfidenceBadge confidence={det.confidence} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && recentDetections.length === 0 ? (
          <div className="text-center py-8 text-surface-500 text-sm">No detections yet.</div>
        ) : null}
      </div>
    </div>
  );
}
