import { useState } from 'react';
import { Radio, Camera, Clock, User, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { useAttendanceSSE } from '../hooks/useAttendanceSSE';

const mockCameras = [
  { id: 'cam-1', room: 'Lab A-204', status: 'active', fps: 24 },
  { id: 'cam-2', room: 'Lab A-204', status: 'active', fps: 22 },
  { id: 'cam-3', room: 'Hall B-101', status: 'active', fps: 30 },
  { id: 'cam-4', room: 'Room C-302', status: 'offline', fps: 0 },
];

function LiveEventCard({ event }) {
  const isDetection = event.type === 'detection';
  return (
    <div className="flex items-start gap-3 px-4 py-3 border-b border-surface-800 last:border-0 animate-fade-in">
      <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${
        isDetection ? 'bg-accent-500/10 text-accent-400' : 'bg-primary-600/10 text-primary-400'
      }`}>
        {isDetection ? <User size={14} /> : <Camera size={14} />}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-surface-100">
          {isDetection
            ? `Student #${event.student_id} detected`
            : `Snapshot #${event.snapshot_id} processed`}
        </p>
        <div className="flex items-center gap-3 mt-0.5 text-xs text-surface-500">
          {isDetection && <span>Confidence: {Math.round((event.confidence || 0) * 100)}%</span>}
          {event.camera_id && <span>Camera: {event.camera_id}</span>}
          <span>{new Date().toLocaleTimeString()}</span>
        </div>
      </div>
    </div>
  );
}

export default function LiveFeedPage() {
  const [selectedSchedule] = useState(1);
  const { events, connected } = useAttendanceSSE(selectedSchedule);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Live Feed</h1>
          <p className="text-surface-500 text-sm mt-0.5">Real-time attendance stream</p>
        </div>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${connected ? 'bg-accent-500' : 'bg-danger-500'}`} />
          <span className={`text-xs font-medium ${connected ? 'text-accent-400' : 'text-danger-400'}`}>
            {connected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Live Event Stream */}
        <div className="col-span-2 card p-0">
          <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700">
            <div className="flex items-center gap-2">
              <Radio size={14} className="text-accent-400" />
              <h3 className="text-sm font-semibold text-surface-200">Event Stream</h3>
            </div>
            <span className="text-xs text-surface-500">{events.length} events</span>
          </div>

          <div className="max-h-[600px] overflow-y-auto">
            {events.length > 0 ? (
              events.map((event, i) => <LiveEventCard key={i} event={event} />)
            ) : (
              <div className="flex flex-col items-center justify-center py-16 text-surface-500">
                <Radio size={32} className="mb-3 opacity-20" />
                <p className="font-medium">Waiting for events…</p>
                <p className="text-xs mt-1">Detection events will appear here in real time</p>
              </div>
            )}
          </div>
        </div>

        {/* Camera Status Panel */}
        <div className="space-y-4">
          <div className="card">
            <h3 className="text-sm font-semibold text-surface-200 mb-3">Camera Status</h3>
            <div className="space-y-3">
              {mockCameras.map((cam) => (
                <div key={cam.id} className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <Camera size={14} className={cam.status === 'active' ? 'text-accent-400' : 'text-danger-400'} />
                    <div>
                      <p className="text-sm text-surface-200">{cam.room}</p>
                      <p className="text-xs text-surface-500">{cam.id}</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <span className={`badge ${cam.status === 'active' ? 'badge-success' : 'badge-danger'}`}>
                      {cam.status}
                    </span>
                    {cam.fps > 0 && (
                      <p className="text-[10px] text-surface-600 mt-0.5">{cam.fps} FPS</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Quick Stats */}
          <div className="card space-y-3">
            <h3 className="text-sm font-semibold text-surface-200">Session Stats</h3>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Detections</span>
              <span className="text-sm font-semibold text-surface-100">
                {events.filter(e => e.type === 'detection').length}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Snapshots</span>
              <span className="text-sm font-semibold text-surface-100">
                {events.filter(e => e.type === 'snapshot_complete').length}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-surface-400">Liveness Fails</span>
              <span className="text-sm font-semibold text-danger-400">0</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
