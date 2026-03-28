import { useState } from 'react';
import { CalendarClock, Download, Filter, ChevronDown, Check, X, Minus } from 'lucide-react';

const mockSchedules = [
  { id: 1, course: 'CS-301 Computer Vision', instructor: 'Dr. Ahmad', day: 'Mon', time: '09:00 - 10:30', room: 'Lab A-204' },
  { id: 2, course: 'MATH-201 Linear Algebra', instructor: 'Dr. Fatima', day: 'Tue', time: '11:00 - 12:30', room: 'Hall B-101' },
  { id: 3, course: 'ENG-102 Technical Writing', instructor: 'Prof. Sarah', day: 'Wed', time: '14:00 - 15:30', room: 'Room C-302' },
];

const mockAttendanceGrid = Array.from({ length: 8 }, (_, i) => ({
  name: ['Ahmed H.', 'Fatima A.', 'Omar K.', 'Sara I.', 'Yusuf N.', 'Layla M.', 'Khaled O.', 'Nadia S.'][i],
  sessions: Array.from({ length: 10 }, () => {
    const r = Math.random();
    return r > 0.85 ? 'absent' : r > 0.08 ? 'present' : 'late';
  }),
}));

function StatusIcon({ status }) {
  if (status === 'present') return <Check size={12} className="text-accent-400" />;
  if (status === 'absent') return <X size={12} className="text-danger-400" />;
  return <Minus size={12} className="text-warning-400" />;
}

function StatusCell({ status }) {
  const bg = {
    present: 'bg-accent-500/10',
    absent: 'bg-danger-500/10',
    late: 'bg-warning-500/10',
  };

  return (
    <td className="p-1">
      <div className={`w-8 h-8 rounded flex items-center justify-center ${bg[status]} transition-colors`}>
        <StatusIcon status={status} />
      </div>
    </td>
  );
}

export default function AttendancePage() {
  const [selectedSchedule, setSelectedSchedule] = useState(mockSchedules[0]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Attendance</h1>
          <p className="text-surface-500 text-sm mt-0.5">View and manage attendance records</p>
        </div>
        <button className="btn-secondary">
          <Download size={16} />
          Export CSV
        </button>
      </div>

      {/* Schedule Selector */}
      <div className="flex flex-wrap gap-3">
        {mockSchedules.map((sched) => (
          <button
            key={sched.id}
            onClick={() => setSelectedSchedule(sched)}
            className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 cursor-pointer ${
              selectedSchedule.id === sched.id
                ? 'bg-primary-600/15 text-primary-400 border border-primary-500/30'
                : 'bg-surface-800 text-surface-400 border border-surface-700 hover:border-surface-600 hover:text-surface-200'
            }`}
          >
            <CalendarClock size={14} />
            {sched.course.split(' ')[0]}
            <span className="text-xs opacity-60">{sched.day} {sched.time}</span>
          </button>
        ))}
      </div>

      {/* Schedule Info */}
      <div className="card-glass flex items-center gap-6 text-sm">
        <div>
          <span className="text-surface-500">Course:</span>
          <span className="ml-2 text-surface-100 font-medium">{selectedSchedule.course}</span>
        </div>
        <div className="w-px h-4 bg-surface-700" />
        <div>
          <span className="text-surface-500">Instructor:</span>
          <span className="ml-2 text-surface-100">{selectedSchedule.instructor}</span>
        </div>
        <div className="w-px h-4 bg-surface-700" />
        <div>
          <span className="text-surface-500">Room:</span>
          <span className="ml-2 text-surface-100">{selectedSchedule.room}</span>
        </div>
      </div>

      {/* Attendance Grid */}
      <div className="card p-0 overflow-x-auto">
        <table className="table">
          <thead>
            <tr>
              <th className="sticky left-0 bg-surface-800 z-10">Student</th>
              {Array.from({ length: 10 }, (_, i) => (
                <th key={i} className="text-center text-xs">W{i + 1}</th>
              ))}
              <th className="text-center">Rate</th>
            </tr>
          </thead>
          <tbody>
            {mockAttendanceGrid.map((student) => {
              const presentCount = student.sessions.filter((s) => s === 'present').length;
              const rate = Math.round((presentCount / student.sessions.length) * 100);
              return (
                <tr key={student.name}>
                  <td className="sticky left-0 bg-surface-900 z-10 font-medium text-surface-100 whitespace-nowrap">
                    {student.name}
                  </td>
                  {student.sessions.map((status, i) => (
                    <StatusCell key={i} status={status} />
                  ))}
                  <td className="text-center">
                    <span className={`badge ${rate >= 85 ? 'badge-success' : rate >= 70 ? 'badge-warning' : 'badge-danger'}`}>
                      {rate}%
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs text-surface-500">
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded bg-accent-500/20 flex items-center justify-center"><Check size={8} className="text-accent-400" /></div>
          Present
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded bg-danger-500/20 flex items-center justify-center"><X size={8} className="text-danger-400" /></div>
          Absent
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded bg-warning-500/20 flex items-center justify-center"><Minus size={8} className="text-warning-400" /></div>
          Late
        </div>
      </div>
    </div>
  );
}
