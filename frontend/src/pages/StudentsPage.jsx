import { useState } from 'react';
import { Search, Plus, ChevronDown, MoreHorizontal, GraduationCap } from 'lucide-react';

const mockStudents = Array.from({ length: 12 }, (_, i) => ({
  id: i + 1,
  name: ['Ahmed Hassan', 'Fatima Ali', 'Omar Khalil', 'Sara Ibrahim', 'Yusuf Noor', 'Layla Mahmoud', 'Khaled Osman', 'Nadia Saleh', 'Tariq Yousef', 'Amina Abdi', 'Hassan Jama', 'Maryam Farah'][i],
  enrollment_no: `2024${String(i + 100).padStart(4, '0')}`,
  email: `student${i + 1}@university.edu`,
  courses: Math.floor(Math.random() * 4) + 2,
  embeddings: Math.floor(Math.random() * 6) + 3,
  status: Math.random() > 0.15 ? 'active' : 'inactive',
}));

export default function StudentsPage() {
  const [search, setSearch] = useState('');

  const filtered = mockStudents.filter(
    (s) =>
      s.name.toLowerCase().includes(search.toLowerCase()) ||
      s.enrollment_no.includes(search)
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Students</h1>
          <p className="text-surface-500 text-sm mt-0.5">{mockStudents.length} enrolled students</p>
        </div>
        <button className="btn-primary">
          <Plus size={16} />
          Add Student
        </button>
      </div>

      {/* Search + Filters */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-surface-500" />
          <input
            type="text"
            placeholder="Search by name or enrollment number…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="input pl-10"
          />
        </div>
        <button className="btn-secondary">
          Status
          <ChevronDown size={14} />
        </button>
        <button className="btn-secondary">
          Course
          <ChevronDown size={14} />
        </button>
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <table className="table">
          <thead>
            <tr>
              <th>Student</th>
              <th>Enrollment #</th>
              <th>Email</th>
              <th>Courses</th>
              <th>Embeddings</th>
              <th>Status</th>
              <th className="w-10"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((student) => (
              <tr key={student.id}>
                <td>
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary-600/15 flex items-center justify-center text-primary-400">
                      <GraduationCap size={14} />
                    </div>
                    <span className="font-medium text-surface-100">{student.name}</span>
                  </div>
                </td>
                <td className="font-mono text-surface-400 text-xs">{student.enrollment_no}</td>
                <td className="text-surface-400">{student.email}</td>
                <td className="text-surface-300">{student.courses}</td>
                <td>
                  <span className={`badge ${student.embeddings >= 5 ? 'badge-success' : 'badge-warning'}`}>
                    {student.embeddings}/8
                  </span>
                </td>
                <td>
                  <span className={`badge ${student.status === 'active' ? 'badge-success' : 'badge-danger'}`}>
                    {student.status}
                  </span>
                </td>
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

      {/* Empty state */}
      {filtered.length === 0 && (
        <div className="text-center py-12 text-surface-500">
          <GraduationCap size={40} className="mx-auto mb-3 opacity-30" />
          <p className="font-medium">No students found</p>
          <p className="text-sm mt-1">Try adjusting your search query</p>
        </div>
      )}
    </div>
  );
}
