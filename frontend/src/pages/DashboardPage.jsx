import { useState, useEffect } from 'react';
import {
  Users,
  GraduationCap,
  CalendarClock,
  Camera,
  TrendingUp,
  AlertTriangle,
  CheckCircle2,
  Radio,
} from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

// Mock data — will be replaced by API calls
const mockChartData = [
  { time: '08:00', present: 12 },
  { time: '08:30', present: 28 },
  { time: '09:00', present: 45 },
  { time: '09:30', present: 52 },
  { time: '10:00', present: 48 },
  { time: '10:30', present: 60 },
  { time: '11:00', present: 55 },
  { time: '11:30', present: 62 },
];

const mockRecentDetections = [
  { id: 1, name: 'Ahmed Hassan', course: 'CS-301', time: '11:28 AM', confidence: 0.97 },
  { id: 2, name: 'Fatima Ali', course: 'CS-301', time: '11:27 AM', confidence: 0.94 },
  { id: 3, name: 'Omar Khalil', course: 'MATH-201', time: '11:25 AM', confidence: 0.91 },
  { id: 4, name: 'Sara Ibrahim', course: 'CS-301', time: '11:23 AM', confidence: 0.96 },
  { id: 5, name: 'Yusuf Noor', course: 'ENG-102', time: '11:20 AM', confidence: 0.89 },
];

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
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Dashboard</h1>
          <p className="text-surface-500 text-sm mt-0.5">Real-time attendance monitoring</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="pulse-dot" />
          <span className="text-xs text-accent-400 font-medium">System Online</span>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Users} label="Total Students" value="248" trend trendLabel="+12 this week" color="primary" />
        <StatCard icon={CheckCircle2} label="Present Today" value="186" trend trendLabel="75%" color="accent" />
        <StatCard icon={Camera} label="Active Cameras" value="8" color="warning" />
        <StatCard icon={AlertTriangle} label="Liveness Failures" value="3" color="danger" />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Attendance Trend */}
        <div className="col-span-2 card">
          <h3 className="text-sm font-semibold text-surface-200 mb-4">Attendance Trend — Today</h3>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={mockChartData}>
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
          {[
            { name: 'YOLO Detector', status: 'ready' },
            { name: 'ArcFace', status: 'ready' },
            { name: 'AdaFace', status: 'ready' },
            { name: 'Anti-Spoof CNN', status: 'ready' },
            { name: 'Super-Resolution', status: 'idle' },
          ].map(({ name, status }) => (
            <div key={name} className="flex items-center justify-between">
              <span className="text-sm text-surface-300">{name}</span>
              <span className={`badge ${status === 'ready' ? 'badge-success' : 'badge-warning'}`}>
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
          <div className="flex items-center gap-2">
            <Radio size={14} className="text-accent-400" />
            <span className="text-xs text-accent-400 font-medium">Live</span>
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
            {mockRecentDetections.map((det) => (
              <tr key={det.id}>
                <td className="font-medium text-surface-100">{det.name}</td>
                <td className="text-surface-400">{det.course}</td>
                <td className="text-surface-400">{det.time}</td>
                <td><ConfidenceBadge confidence={det.confidence} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
