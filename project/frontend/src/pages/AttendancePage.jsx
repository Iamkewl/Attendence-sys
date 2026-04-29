import { useEffect, useMemo, useState } from 'react';
import { CalendarClock, Download } from 'lucide-react';
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

function formatTime(t) {
  if (!t) return '-';
  return String(t).slice(0, 5);
}

export default function AttendancePage() {
  const [schedules, setSchedules] = useState([]);
  const [coursesMap, setCoursesMap] = useState({});
  const [selectedScheduleId, setSelectedScheduleId] = useState(null);

  const [report, setReport] = useState(null);
  const [loadingMeta, setLoadingMeta] = useState(true);
  const [loadingReport, setLoadingReport] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState('');

  const selectedSchedule = useMemo(
    () => schedules.find((s) => s.id === selectedScheduleId) || null,
    [schedules, selectedScheduleId]
  );

  const loadMeta = async () => {
    setLoadingMeta(true);
    setError('');
    try {
      const [scheduleList, courseList] = await Promise.all([
        api.getSchedules(),
        api.getCourses(),
      ]);

      setSchedules(scheduleList || []);
      const map = {};
      (courseList || []).forEach((course) => {
        map[course.id] = course;
      });
      setCoursesMap(map);

      if ((scheduleList || []).length > 0) {
        setSelectedScheduleId(scheduleList[0].id);
      }
    } catch (err) {
      setError(await parseApiError(err, 'Failed to load schedules.'));
    } finally {
      setLoadingMeta(false);
    }
  };

  const loadReport = async (scheduleId) => {
    if (!scheduleId) return;
    setLoadingReport(true);
    setError('');
    try {
      const data = await api.getAttendance(scheduleId);
      setReport(data);
    } catch (err) {
      setError(await parseApiError(err, 'Failed to load attendance report.'));
      setReport(null);
    } finally {
      setLoadingReport(false);
    }
  };

  useEffect(() => {
    loadMeta();
  }, []);

  useEffect(() => {
    if (selectedScheduleId) {
      loadReport(selectedScheduleId);
    }
  }, [selectedScheduleId]);

  const exportCsv = async () => {
    if (!selectedScheduleId) return;

    setExporting(true);
    setError('');
    try {
      const { blob, filename } = await api.exportAttendanceCsv(selectedScheduleId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(await parseApiError(err, 'Failed to export CSV.'));
    } finally {
      setExporting(false);
    }
  };

  const records = report?.records || [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Attendance</h1>
          <p className="text-surface-500 text-sm mt-0.5">View and manage attendance records</p>
        </div>
        <button className="btn-secondary" onClick={exportCsv} disabled={!selectedScheduleId || exporting || loadingReport}>
          <Download size={16} />
          {exporting ? 'Exporting...' : 'Export CSV'}
        </button>
      </div>

      {error ? (
        <div className="px-3 py-2 rounded-lg bg-danger-500/10 border border-danger-500/20 text-danger-300 text-sm">
          {error}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-3">
        {loadingMeta ? (
          <div className="text-surface-500 text-sm">Loading schedules...</div>
        ) : schedules.length === 0 ? (
          <div className="text-surface-500 text-sm">No schedules available.</div>
        ) : (
          schedules.map((sched) => {
            const course = coursesMap[sched.course_id];
            const label = course ? course.code : `Course #${sched.course_id}`;
            const days = (sched.days_of_week || []).join(', ');
            return (
              <button
                key={sched.id}
                onClick={() => setSelectedScheduleId(sched.id)}
                className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 cursor-pointer ${
                  selectedScheduleId === sched.id
                    ? 'bg-primary-600/15 text-primary-400 border border-primary-500/30'
                    : 'bg-surface-800 text-surface-400 border border-surface-700 hover:border-surface-600 hover:text-surface-200'
                }`}
              >
                <CalendarClock size={14} />
                {label}
                <span className="text-xs opacity-70">{days} {formatTime(sched.start_time)}-{formatTime(sched.end_time)}</span>
              </button>
            );
          })
        )}
      </div>

      {selectedSchedule ? (
        <div className="card-glass flex flex-wrap items-center gap-6 text-sm">
          <div>
            <span className="text-surface-500">Course:</span>
            <span className="ml-2 text-surface-100 font-medium">
              {coursesMap[selectedSchedule.course_id]
                ? `${coursesMap[selectedSchedule.course_id].code} ${coursesMap[selectedSchedule.course_id].name}`
                : `Course #${selectedSchedule.course_id}`}
            </span>
          </div>
          <div className="w-px h-4 bg-surface-700" />
          <div>
            <span className="text-surface-500">Room ID:</span>
            <span className="ml-2 text-surface-100">{selectedSchedule.room_id}</span>
          </div>
          <div className="w-px h-4 bg-surface-700" />
          <div>
            <span className="text-surface-500">Total Snapshots:</span>
            <span className="ml-2 text-surface-100">{report?.total_snapshots ?? 0}</span>
          </div>
        </div>
      ) : null}

      <div className="card p-0 overflow-x-auto">
        <table className="table min-w-[720px]">
          <thead>
            <tr>
              <th>Student</th>
              <th>Observed</th>
              <th>Total</th>
              <th>Ratio</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {!loadingReport && records.map((record) => {
              const pct = Math.round(record.ratio * 100);
              return (
                <tr key={record.student_id}>
                  <td className="font-medium text-surface-100">{record.student_name}</td>
                  <td className="text-surface-300">{record.observed_snapshots}</td>
                  <td className="text-surface-300">{record.total_snapshots}</td>
                  <td className="text-surface-300">{pct}%</td>
                  <td>
                    <span className={`badge ${record.status === 'present' ? 'badge-success' : 'badge-danger'}`}>
                      {record.status}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {loadingReport ? <div className="text-center py-12 text-surface-500">Loading attendance report...</div> : null}
        {!loadingReport && selectedScheduleId && records.length === 0 ? (
          <div className="text-center py-12 text-surface-500">
            <p className="font-medium">No attendance records yet</p>
            <p className="text-sm mt-1">Records will appear after snapshots are processed for this schedule.</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
