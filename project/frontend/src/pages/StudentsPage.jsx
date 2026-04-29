import { useEffect, useMemo, useRef, useState } from 'react';
import { Search, Plus, GraduationCap, Pencil, Trash2, RefreshCw, Camera } from 'lucide-react';
import api from '../api/client';

const statusTabs = [
  { value: 'all', label: 'All' },
  { value: 'enrolled', label: 'Enrolled' },
  { value: 'not_enrolled', label: 'Not Enrolled' },
];

const rejectReasonLabels = {
  no_face_detected: 'No face detected',
  multiple_faces_detected: 'Multiple faces detected',
  face_too_small: 'Face too small',
  image_too_blurry: 'Image too blurry',
  face_quality_low: 'Face quality too low',
  empty_file: 'Empty file',
  invalid_image_format: 'Invalid image format',
  crop_failed: 'Face crop failed',
  embedding_extraction_failed: 'Embedding extraction failed',
  invalid_embedding_vector: 'Invalid embedding vector',
  duplicate_embedding: 'Duplicate embedding candidate',
  collision_risk: 'Too close to another student',
  other: 'Other rejection',
};

const captureGuidanceByReason = {
  no_face_detected: 'Center your face in frame and improve front lighting.',
  multiple_faces_detected: 'Ensure only one person is visible during capture burst.',
  face_too_small: 'Move closer so your face occupies more of the frame.',
  image_too_blurry: 'Hold steady and avoid motion between burst frames.',
  face_quality_low: 'Use brighter light and face the camera directly.',
  duplicate_embedding: 'Vary angle/expression slightly across burst images.',
  collision_risk: 'Capture a clearer frontal image with neutral expression.',
};

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

async function parseApiError(err, fallback) {
  if (!err?.response) return err?.message || fallback;
  try {
    const payload = await err.response.json();
    return payload?.detail?.message || payload?.detail || payload?.message || fallback;
  } catch {
    return fallback;
  }
}

export default function StudentsPage() {
  const [students, setStudents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [courseFilter, setCourseFilter] = useState('all');
  const [courses, setCourses] = useState([]);

  const [createOpen, setCreateOpen] = useState(false);
  const [editStudent, setEditStudent] = useState(null);
  const [deleteStudent, setDeleteStudent] = useState(null);
  const [enrollStudent, setEnrollStudent] = useState(null);
  const [enrollSummary, setEnrollSummary] = useState(null);
  const [enrollQuality, setEnrollQuality] = useState(null);
  const [enrollAnalytics, setEnrollAnalytics] = useState(null);
  const [enrollHistory, setEnrollHistory] = useState(null);
  const [enrollTemplates, setEnrollTemplates] = useState([]);
  const [enrollFeedback, setEnrollFeedback] = useState('');
  const [enrollFeedbackTone, setEnrollFeedbackTone] = useState('info');
  const [enrollmentTestFile, setEnrollmentTestFile] = useState(null);
  const [enrollmentTestResult, setEnrollmentTestResult] = useState(null);
  const [testingEnrollment, setTestingEnrollment] = useState(false);
  const [capturingBurst, setCapturingBurst] = useState(false);
  const [qualityLoading, setQualityLoading] = useState(false);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [updatingTemplateId, setUpdatingTemplateId] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const cameraVideoRef = useRef(null);
  const cameraStreamRef = useRef(null);

  const [createForm, setCreateForm] = useState({
    name: '',
    department: '',
    enrollment_year: '',
    files: [],
    pose_label: 'frontal',
    auto_pose: true,
  });

  const [editForm, setEditForm] = useState({
    name: '',
    department: '',
    enrollment_year: '',
  });

  const [enrollForm, setEnrollForm] = useState({
    files: [],
    pose_label: 'frontal',
    auto_pose: true,
  });

  const isCaptureSupported = typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;

  const stopGuidedCapture = () => {
    if (cameraStreamRef.current) {
      cameraStreamRef.current.getTracks().forEach((track) => track.stop());
      cameraStreamRef.current = null;
    }
    if (cameraVideoRef.current) {
      cameraVideoRef.current.srcObject = null;
    }
  };

  const startGuidedCapture = async () => {
    if (!isCaptureSupported) {
      setError('Camera capture is not supported in this browser.');
      return;
    }
    setError('');
    try {
      if (cameraStreamRef.current) {
        return;
      }
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false });
      cameraStreamRef.current = stream;
      if (cameraVideoRef.current) {
        cameraVideoRef.current.srcObject = stream;
        await cameraVideoRef.current.play().catch(() => {});
      }
    } catch {
      setError('Unable to access camera for guided capture. Check browser permissions.');
    }
  };

  const captureFrameFromVideo = async (index) => {
    const video = cameraVideoRef.current;
    if (!video || video.readyState < 2) return null;

    const width = video.videoWidth || 640;
    const height = video.videoHeight || 480;
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    ctx.drawImage(video, 0, 0, width, height);

    const blob = await new Promise((resolve) => {
      canvas.toBlob(resolve, 'image/jpeg', 0.9);
    });
    if (!blob) return null;

    return new File([blob], `guided_burst_${Date.now()}_${index}.jpg`, {
      type: 'image/jpeg',
      lastModified: Date.now(),
    });
  };

  const runGuidedBurstCapture = async () => {
    setError('');
    setNotice('');
    setEnrollFeedback('');
    setEnrollFeedbackTone('info');
    setCapturingBurst(true);

    try {
      await startGuidedCapture();

      const frames = [];
      const burstCount = 8;
      const frameGapMs = 180;

      for (let i = 0; i < burstCount; i += 1) {
        const frameFile = await captureFrameFromVideo(i);
        if (frameFile) {
          frames.push(frameFile);
        }
        if (i < burstCount - 1) {
          // Time-spaced frame selection reduces near-duplicate captures.
          await new Promise((resolve) => setTimeout(resolve, frameGapMs));
        }
      }

      if (frames.length === 0) {
        setError('No frames were captured. Keep face centered and try again.');
        setEnrollFeedback('Capture failed: no frames detected. Keep face centered and camera stable, then retry.');
        setEnrollFeedbackTone('error');
        return;
      }

      setEnrollForm((prev) => ({
        ...prev,
        files: frames,
        auto_pose: true,
      }));

      // Guided flow should give immediate end-to-end feedback (capture + enrollment).
      await submitEnrollFiles(frames, {
        autoPose: true,
        sourceLabel: `Guided burst captured ${frames.length} frame(s).`,
      });
    } finally {
      setCapturingBurst(false);
    }
  };

  const loadEnrollmentQuality = async (studentId) => {
    setQualityLoading(true);
    try {
      const quality = await api.getStudentEnrollmentQuality(studentId);
      setEnrollQuality(quality);
    } catch {
      setEnrollQuality(null);
    } finally {
      setQualityLoading(false);
    }
  };

  const loadEnrollmentAnalytics = async (studentId) => {
    try {
      const analytics = await api.getStudentEnrollmentAnalytics(studentId);
      setEnrollAnalytics(analytics);
    } catch {
      setEnrollAnalytics(null);
    }
  };

  const loadEnrollmentHistory = async (studentId) => {
    try {
      const history = await api.getStudentEnrollmentAnalyticsHistory(studentId, { limit: 60 });
      setEnrollHistory(history);
    } catch {
      setEnrollHistory(null);
    }
  };

  const loadEnrollmentTemplates = async (studentId) => {
    setTemplatesLoading(true);
    try {
      const templates = await api.getStudentEnrollmentTemplates(studentId);
      setEnrollTemplates(templates || []);
    } catch {
      setEnrollTemplates([]);
    } finally {
      setTemplatesLoading(false);
    }
  };

  const updateTemplateStatus = async (embeddingId, templateStatus) => {
    if (!enrollStudent) return;
    setUpdatingTemplateId(embeddingId);
    setError('');
    try {
      await api.updateStudentEnrollmentTemplate(enrollStudent.id, embeddingId, { template_status: templateStatus });
      await Promise.all([
        loadEnrollmentQuality(enrollStudent.id),
        loadEnrollmentTemplates(enrollStudent.id),
        loadEnrollmentHistory(enrollStudent.id),
      ]);
      setNotice(`Template ${embeddingId} updated to ${templateStatus}.`);
    } catch (err) {
      setError(await parseApiError(err, 'Failed to update template status.'));
    } finally {
      setUpdatingTemplateId(null);
    }
  };

  const loadStudents = async () => {
    setLoading(true);
    setError('');
    try {
      const enrolledOnly = statusFilter === 'enrolled' ? true : undefined;
      const selectedCourseId = courseFilter !== 'all' ? Number(courseFilter) : undefined;
      const data = await api.getStudents({
        limit: 200,
        enrolled_only: enrolledOnly,
        course_id: Number.isFinite(selectedCourseId) ? selectedCourseId : undefined,
      });
      const normalized = statusFilter === 'not_enrolled'
        ? data.filter((item) => !item.is_enrolled)
        : data;
      setStudents(normalized);
    } catch (err) {
      setError(await parseApiError(err, 'Failed to load students.'));
    } finally {
      setLoading(false);
    }
  };

  const loadCourses = async () => {
    try {
      const list = await api.getCourses();
      setCourses(Array.isArray(list) ? list : []);
    } catch {
      setCourses([]);
    }
  };

  useEffect(() => {
    loadCourses();
  }, []);

  useEffect(() => {
    loadStudents();
  }, [statusFilter, courseFilter]);

  useEffect(() => {
    return () => {
      stopGuidedCapture();
    };
  }, []);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return students;
    return students.filter((student) => {
      const name = (student.name || '').toLowerCase();
      const department = (student.department || '').toLowerCase();
      const year = student.enrollment_year ? String(student.enrollment_year) : '';
      const id = String(student.id);
      const courseText = (student.course_names || []).join(' ').toLowerCase();
      return name.includes(query)
        || department.includes(query)
        || year.includes(query)
        || id.includes(query)
        || courseText.includes(query);
    });
  }, [students, search]);

  const stats = useMemo(() => {
    const total = students.length;
    const enrolled = students.filter((s) => s.is_enrolled).length;
    const notEnrolled = total - enrolled;
    return { total, enrolled, notEnrolled };
  }, [students]);

  const submitCreate = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    setNotice('');
    try {
      const created = await api.createStudent({
        name: createForm.name.trim(),
        department: createForm.department.trim() || null,
        enrollment_year: createForm.enrollment_year ? Number(createForm.enrollment_year) : null,
      });

      let feedback = `Student ${created.name} created.`;
      if (createForm.files.length > 0) {
        try {
          const summary = await api.enrollStudentWithImages(created.id, createForm.files, {
            pose_label: createForm.pose_label,
            auto_pose: createForm.auto_pose,
          });
          feedback = summary.message;
        } catch (enrollErr) {
          const enrollMessage = await parseApiError(enrollErr, 'Failed to enroll student images.');
          feedback = `Student created, but enrollment failed: ${enrollMessage}`;
        }
      }

      setNotice(feedback);
      setCreateOpen(false);
      setCreateForm({
        name: '',
        department: '',
        enrollment_year: '',
        files: [],
        pose_label: 'frontal',
        auto_pose: true,
      });
      await loadStudents();
    } catch (err) {
      setError(await parseApiError(err, 'Failed to create student.'));
    } finally {
      setSubmitting(false);
    }
  };

  const openEdit = (student) => {
    setEditStudent(student);
    setEditForm({
      name: student.name || '',
      department: student.department || '',
      enrollment_year: student.enrollment_year ? String(student.enrollment_year) : '',
    });
  };

  const submitEdit = async (e) => {
    e.preventDefault();
    if (!editStudent) return;

    setSubmitting(true);
    setError('');
    try {
      await api.updateStudent(editStudent.id, {
        name: editForm.name.trim(),
        department: editForm.department.trim() || null,
        enrollment_year: editForm.enrollment_year ? Number(editForm.enrollment_year) : null,
      });
      setEditStudent(null);
      await loadStudents();
    } catch (err) {
      setError(await parseApiError(err, 'Failed to update student.'));
    } finally {
      setSubmitting(false);
    }
  };

  const submitDelete = async () => {
    if (!deleteStudent) return;

    setSubmitting(true);
    setError('');
    try {
      await api.deleteStudent(deleteStudent.id);
      setDeleteStudent(null);
      await loadStudents();
    } catch (err) {
      setError(await parseApiError(err, 'Failed to delete student.'));
    } finally {
      setSubmitting(false);
    }
  };

  const openEnroll = (student) => {
    setEnrollStudent(student);
    setEnrollSummary(null);
    setEnrollQuality(null);
    setEnrollAnalytics(null);
    setEnrollHistory(null);
    setEnrollTemplates([]);
    setEnrollFeedback('');
    setEnrollFeedbackTone('info');
    setEnrollmentTestFile(null);
    setEnrollmentTestResult(null);
    setEnrollForm({
      files: [],
      pose_label: 'frontal',
      auto_pose: true,
    });
    Promise.all([
      loadEnrollmentQuality(student.id),
      loadEnrollmentAnalytics(student.id),
      loadEnrollmentHistory(student.id),
      loadEnrollmentTemplates(student.id),
    ]);
  };

  const closeEnrollModal = () => {
    stopGuidedCapture();
    setEnrollStudent(null);
    setEnrollFeedback('');
    setEnrollFeedbackTone('info');
    setEnrollmentTestFile(null);
    setEnrollmentTestResult(null);
  };

  const getPoseRecommendation = () => {
    const missing = enrollQuality?.missing_pose_coverage || {};
    if (Object.keys(missing).length === 0) {
      return 'Pose coverage complete. No manual pose switching needed.';
    }
    if (missing.left_34) {
      return 'Recommended next pose: Left 3/4. Turn your head ~30° to your left and run burst capture again.';
    }
    if (missing.right_34) {
      return 'Recommended next pose: Right 3/4. Turn your head ~30° to your right and run burst capture again.';
    }
    return 'Recommended next pose: Frontal. Face the camera directly and run burst capture.';
  };

  const submitEnrollFiles = async (files, opts = {}) => {
    if (!enrollStudent) return;
    if (!files || files.length === 0) {
      setError('Please select at least one image for enrollment.');
      return;
    }

    setSubmitting(true);
    setError('');
    setNotice('');
    setEnrollFeedback('Processing enrollment...');
    setEnrollFeedbackTone('info');

    try {
      const summary = await api.enrollStudentWithImages(enrollStudent.id, files, {
        pose_label: enrollForm.pose_label,
        auto_pose: opts.autoPose ?? enrollForm.auto_pose,
      });
      setEnrollSummary(summary);

      const baseMessage = summary.message || 'Enrollment completed.';
      const fullMessage = opts.sourceLabel ? `${opts.sourceLabel} ${baseMessage}` : baseMessage;
      setEnrollFeedback(fullMessage);
      setEnrollFeedbackTone(summary?.enrolled ? 'success' : 'warning');
      setNotice(fullMessage);

      await Promise.all([
        loadEnrollmentQuality(enrollStudent.id),
        loadEnrollmentAnalytics(enrollStudent.id),
        loadEnrollmentHistory(enrollStudent.id),
        loadEnrollmentTemplates(enrollStudent.id),
      ]);
      await loadStudents();
    } catch (err) {
      const msg = await parseApiError(err, 'Failed to enroll student images.');
      setError(msg);
      setEnrollFeedback(`Enrollment failed: ${msg}`);
      setEnrollFeedbackTone('error');
    } finally {
      setSubmitting(false);
    }
  };

  const submitEnroll = async (e) => {
    e.preventDefault();
    if (!enrollForm.files.length) {
      setEnrollFeedback('No images selected. Capture burst or upload photos first.');
      setEnrollFeedbackTone('error');
      return;
    }
    await submitEnrollFiles(enrollForm.files);
  };

  const runEnrollmentTest = async () => {
    if (!enrollStudent) return;
    if (!enrollmentTestFile) {
      setError('Please select a test image first.');
      return;
    }

    setTestingEnrollment(true);
    setError('');
    setEnrollmentTestResult(null);

    try {
      const result = await api.testStudentEnrollment(enrollStudent.id, enrollmentTestFile);
      setEnrollmentTestResult(result);
      if (result?.is_match) {
        setNotice('Enrollment test passed for this student.');
      }
    } catch (err) {
      setError(await parseApiError(err, 'Failed to run enrollment verification test.'));
    } finally {
      setTestingEnrollment(false);
    }
  };

  const getNextPoseAction = () => {
    const missing = enrollSummary?.missing_pose_coverage || enrollQuality?.missing_pose_coverage || {};
    if (!missing || Object.keys(missing).length === 0) {
      return 'Enrollment pose coverage is complete.';
    }
    if (missing.left_34) {
      return 'Next step: turn head LEFT ~30° and run Capture Burst + Enroll again.';
    }
    if (missing.right_34) {
      return 'Next step: turn head RIGHT ~30° and run Capture Burst + Enroll again.';
    }
    return 'Next step: face FRONT and run Capture Burst + Enroll again.';
  };

  const getEnrollmentProgress = () => {
    if (!enrollQuality) {
      return {
        overallPct: 0,
        templatePct: 0,
        posePct: 0,
        templateCurrent: 0,
        templateRequired: 0,
        poseRows: [],
      };
    }

    const templateCurrent = Number(enrollQuality.active_embeddings || 0);
    const templateRequired = Number(enrollQuality.required_embeddings || 0);
    const templatePct = templateRequired > 0
      ? Math.min(100, Math.round((templateCurrent / templateRequired) * 100))
      : 0;

    const requiredPose = enrollQuality.required_pose_coverage || {};
    const currentPose = enrollQuality.pose_coverage || {};
    const poseKeys = ['frontal', 'left_34', 'right_34'];

    const poseRows = poseKeys.map((pose) => {
      const required = Number(requiredPose[pose] || 0);
      const current = Number(currentPose[pose] || 0);
      const pct = required > 0
        ? Math.min(100, Math.round((Math.min(current, required) / required) * 100))
        : 100;
      return { pose, current, required, pct };
    });

    const totalRequiredPose = poseRows.reduce((sum, row) => sum + row.required, 0);
    const totalCoveredPose = poseRows.reduce((sum, row) => sum + Math.min(row.current, row.required), 0);
    const posePct = totalRequiredPose > 0
      ? Math.min(100, Math.round((totalCoveredPose / totalRequiredPose) * 100))
      : 100;

    const overallPct = Math.round((templatePct + posePct) / 2);

    return {
      overallPct,
      templatePct,
      posePct,
      templateCurrent,
      templateRequired,
      poseRows,
    };
  };

  const enrollmentProgress = getEnrollmentProgress();

  const rejectDiagnostics = useMemo(() => {
    const checks = enrollSummary?.checks || [];
    const groups = {};
    checks.forEach((check) => {
      if (check?.accepted) return;
      const code = check?.reject_reason_code || 'other';
      groups[code] = (groups[code] || 0) + 1;
    });

    return Object.entries(groups)
      .sort((a, b) => b[1] - a[1])
      .map(([code, count]) => ({
        code,
        count,
        label: rejectReasonLabels[code] || code,
      }));
  }, [enrollSummary]);

  const dominantCaptureGuidance = useMemo(() => {
    const backendGuidance = enrollSummary?.capture_guidance;
    if (Array.isArray(backendGuidance) && backendGuidance.length) {
      return backendGuidance;
    }

    const dominantCode = rejectDiagnostics[0]?.code;
    if (!dominantCode) {
      return [];
    }

    const guidance = captureGuidanceByReason[dominantCode];
    return guidance ? [guidance] : [];
  }, [enrollSummary, rejectDiagnostics]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Students</h1>
          <p className="text-surface-500 text-sm mt-0.5">{stats.total} students in current view</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-secondary" onClick={loadStudents} aria-label="Refresh students">
            <RefreshCw size={15} />
            Refresh
          </button>
          <button className="btn-primary" onClick={() => setCreateOpen(true)}>
            <Plus size={16} />
            Add Student
          </button>
        </div>
      </div>

      {error ? (
        <div className="px-3 py-2 rounded-lg bg-danger-500/10 border border-danger-500/20 text-danger-300 text-sm">
          {error}
        </div>
      ) : null}

      {notice ? (
        <div className="px-3 py-2 rounded-lg bg-accent-500/10 border border-accent-500/20 text-accent-300 text-sm">
          {notice}
        </div>
      ) : null}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="stat-card">
          <div className="stat-label">Total</div>
          <div className="stat-value text-surface-50">{stats.total}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Enrolled</div>
          <div className="stat-value text-accent-400">{stats.enrolled}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Not Enrolled</div>
          <div className="stat-value text-warning-400">{stats.notEnrolled}</div>
        </div>
      </div>

      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="relative flex-1 max-w-md">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-surface-500" />
          <input
            type="text"
            placeholder="Search by name, department, year, or id"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="input pl-10"
          />
        </div>

        <div className="flex items-center gap-2 flex-wrap justify-end">
          <select
            value={courseFilter}
            onChange={(e) => setCourseFilter(e.target.value)}
            className="input !py-1.5 !w-[220px]"
            aria-label="Filter students by course"
          >
            <option value="all">All Courses</option>
            {courses.map((course) => (
              <option key={course.id} value={String(course.id)}>
                {`${course.code} ${course.name}`.trim()}
              </option>
            ))}
          </select>

          <div className="flex items-center p-1 bg-surface-900/80 rounded-lg border border-surface-700">
            {statusTabs.map((tab) => (
              <button
                key={tab.value}
                onClick={() => setStatusFilter(tab.value)}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors cursor-pointer ${
                  statusFilter === tab.value
                    ? 'bg-primary-600/20 text-primary-300'
                    : 'text-surface-400 hover:text-surface-100'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="table min-w-[980px]">
            <thead>
              <tr>
                <th>Student</th>
                <th>Department</th>
                <th>Enrollment Year</th>
                <th>Courses</th>
                <th>Status</th>
                <th className="w-[180px]">Actions</th>
              </tr>
            </thead>
            <tbody>
              {!loading && filtered.map((student) => (
                <tr key={student.id}>
                  <td>
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-primary-600/15 flex items-center justify-center text-primary-400">
                        <GraduationCap size={14} />
                      </div>
                      <div>
                        <p className="font-medium text-surface-100">{student.name}</p>
                        <p className="text-xs text-surface-500">ID: {student.id}</p>
                      </div>
                    </div>
                  </td>
                  <td className="text-surface-300">{student.department || 'Unassigned'}</td>
                  <td className="text-surface-400">{student.enrollment_year || '-'}</td>
                  <td>
                    {(student.course_names || []).length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {(student.course_names || []).slice(0, 3).map((courseName) => (
                          <span key={`${student.id}-${courseName}`} className="badge border border-surface-700 text-surface-300">
                            {courseName}
                          </span>
                        ))}
                        {(student.course_names || []).length > 3 ? (
                          <span className="text-xs text-surface-500">+{student.course_names.length - 3} more</span>
                        ) : null}
                      </div>
                    ) : (
                      <span className="text-surface-500 text-xs">No observed courses</span>
                    )}
                  </td>
                  <td>
                    <span className={`badge ${student.is_enrolled ? 'badge-success' : 'badge-warning'}`}>
                      {student.is_enrolled ? 'enrolled' : 'not enrolled'}
                    </span>
                  </td>
                  <td>
                    <div className="flex items-center gap-2">
                      <button className="btn-secondary !px-2.5 !py-1.5" onClick={() => openEnroll(student)}>
                        <Camera size={13} />
                        Enroll
                      </button>
                      <button className="btn-secondary !px-2.5 !py-1.5" onClick={() => openEdit(student)}>
                        <Pencil size={13} />
                        Edit
                      </button>
                      <button
                        className="btn-secondary !px-2.5 !py-1.5 text-danger-300 border-danger-500/30 hover:bg-danger-500/15"
                        onClick={() => setDeleteStudent(student)}
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

        {!loading && filtered.length === 0 ? (
          <div className="text-center py-12 text-surface-500">
            <GraduationCap size={40} className="mx-auto mb-3 opacity-30" />
            <p className="font-medium">No students found</p>
            <p className="text-sm mt-1">Try adjusting your search or status filter</p>
          </div>
        ) : null}

        {loading ? <div className="text-center py-12 text-surface-500">Loading students...</div> : null}
      </div>

      {createOpen ? (
        <ModalShell title="Create Student" onClose={() => setCreateOpen(false)}>
          <form className="space-y-4" onSubmit={submitCreate}>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Name</label>
              <input
                type="text"
                required
                value={createForm.name}
                onChange={(e) => setCreateForm((prev) => ({ ...prev, name: e.target.value }))}
                className="input"
              />
            </div>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Department</label>
              <input
                type="text"
                value={createForm.department}
                onChange={(e) => setCreateForm((prev) => ({ ...prev, department: e.target.value }))}
                className="input"
              />
            </div>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Enrollment Year</label>
              <input
                type="number"
                min="2000"
                max="2100"
                value={createForm.enrollment_year}
                onChange={(e) => setCreateForm((prev) => ({ ...prev, enrollment_year: e.target.value }))}
                className="input"
              />
            </div>
            <div className="space-y-2">
              <label className="block text-sm text-surface-300">Enrollment Images (optional)</label>
              <input
                type="file"
                accept="image/*"
                multiple
                onChange={(e) => setCreateForm((prev) => ({ ...prev, files: Array.from(e.target.files || []) }))}
                className="input py-2"
              />
              <p className="text-xs text-surface-500">
                Upload at least 5 high-quality face images to activate recognition enrollment.
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-surface-400 mb-1">Pose Label</label>
                  <select
                    value={createForm.pose_label}
                    onChange={(e) => setCreateForm((prev) => ({ ...prev, pose_label: e.target.value }))}
                    disabled={createForm.auto_pose}
                    className="input"
                  >
                    <option value="frontal">Frontal</option>
                    <option value="left_34">Left 3/4</option>
                    <option value="right_34">Right 3/4</option>
                  </select>
                </div>
                <label className="flex items-center gap-2 text-sm text-surface-300 mt-6 sm:mt-0">
                  <input
                    type="checkbox"
                    checked={createForm.auto_pose}
                    onChange={(e) => setCreateForm((prev) => ({ ...prev, auto_pose: e.target.checked }))}
                  />
                  Auto-detect pose from face landmarks
                </label>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button type="button" className="btn-secondary" onClick={() => setCreateOpen(false)}>Cancel</button>
              <button type="submit" className="btn-primary" disabled={submitting}>
                {submitting ? 'Creating...' : 'Create Student'}
              </button>
            </div>
          </form>
        </ModalShell>
      ) : null}

      {editStudent ? (
        <ModalShell title="Edit Student" onClose={() => setEditStudent(null)}>
          <form className="space-y-4" onSubmit={submitEdit}>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Name</label>
              <input
                type="text"
                required
                value={editForm.name}
                onChange={(e) => setEditForm((prev) => ({ ...prev, name: e.target.value }))}
                className="input"
              />
            </div>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Department</label>
              <input
                type="text"
                value={editForm.department}
                onChange={(e) => setEditForm((prev) => ({ ...prev, department: e.target.value }))}
                className="input"
              />
            </div>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Enrollment Year</label>
              <input
                type="number"
                min="2000"
                max="2100"
                value={editForm.enrollment_year}
                onChange={(e) => setEditForm((prev) => ({ ...prev, enrollment_year: e.target.value }))}
                className="input"
              />
            </div>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button type="button" className="btn-secondary" onClick={() => setEditStudent(null)}>Cancel</button>
              <button type="submit" className="btn-primary" disabled={submitting}>
                {submitting ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          </form>
        </ModalShell>
      ) : null}

      {enrollStudent ? (
        <ModalShell title={`Enroll ${enrollStudent.name}`} onClose={closeEnrollModal}>
          <form className="space-y-4" onSubmit={submitEnroll}>
            <div>
              <label className="block text-sm text-surface-300 mb-1.5">Upload Face Images</label>
              <input
                type="file"
                accept="image/*"
                multiple
                onChange={(e) => setEnrollForm((prev) => ({ ...prev, files: Array.from(e.target.files || []) }))}
                className="input py-2"
              />
              <p className="text-xs text-surface-500 mt-1.5">
                Minimum 5 accepted images are required to mark this student as enrolled.
              </p>
              <p className="text-xs text-surface-400 mt-1">
                Currently selected: {enrollForm.files.length} image(s)
              </p>
            </div>

            {enrollQuality ? (
              <div className="rounded-lg border border-surface-700 bg-surface-900/50 p-3 space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-surface-200">Enrollment Progress</p>
                  <span className="text-xs font-semibold text-primary-300">{enrollmentProgress.overallPct}%</span>
                </div>
                <div className="space-y-2">
                  <div>
                    <div className="flex items-center justify-between text-[11px] text-surface-400 mb-1">
                      <span>Active templates</span>
                      <span>{enrollmentProgress.templateCurrent}/{enrollmentProgress.templateRequired}</span>
                    </div>
                    <div className="h-2 rounded bg-surface-800 overflow-hidden">
                      <div
                        className="h-2 bg-primary-500 transition-all duration-300"
                        style={{ width: `${enrollmentProgress.templatePct}%` }}
                      />
                    </div>
                  </div>
                  <div>
                    <div className="flex items-center justify-between text-[11px] text-surface-400 mb-1">
                      <span>Pose coverage</span>
                      <span>{enrollmentProgress.posePct}%</span>
                    </div>
                    <div className="h-2 rounded bg-surface-800 overflow-hidden">
                      <div
                        className="h-2 bg-accent-500 transition-all duration-300"
                        style={{ width: `${enrollmentProgress.posePct}%` }}
                      />
                    </div>
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  {enrollmentProgress.poseRows.map((row) => (
                    <div key={row.pose} className="rounded border border-surface-700 px-2 py-1.5 text-[11px] text-surface-300">
                      <div className="flex items-center justify-between mb-1">
                        <span>{row.pose}</span>
                        <span>{row.current}/{row.required}</span>
                      </div>
                      <div className="h-1.5 rounded bg-surface-800 overflow-hidden">
                        <div
                          className="h-1.5 bg-warning-400 transition-all duration-300"
                          style={{ width: `${row.pct}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="rounded-lg border border-surface-700 bg-surface-900/40 p-3 space-y-2">
              <p className="text-xs text-surface-200">Guided One-Session Capture</p>
              <p className="text-xs text-surface-500">
                Capture a short burst and enrollment runs immediately after capture.
              </p>
              <p className="text-xs text-primary-300">
                Auto pose detection is enabled for guided capture. You usually do not need to change pose dropdown manually.
              </p>
              <p className="text-xs text-warning-300">{getPoseRecommendation()}</p>
              {dominantCaptureGuidance.length > 0 ? (
                <div className="rounded border border-warning-500/30 bg-warning-500/10 p-2 text-[11px] text-warning-100 space-y-1">
                  <p className="font-semibold">Camera guidance from latest rejects</p>
                  {dominantCaptureGuidance.map((item, idx) => (
                    <p key={`capture-guidance-${idx}`}>- {item}</p>
                  ))}
                </div>
              ) : null}
              <div className="rounded-md border border-surface-700 overflow-hidden bg-surface-950/60">
                <video
                  ref={cameraVideoRef}
                  autoPlay
                  muted
                  playsInline
                  className="w-full max-h-52 object-cover"
                />
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  type="button"
                  className="btn-secondary !px-2.5 !py-1.5"
                  onClick={startGuidedCapture}
                  disabled={!isCaptureSupported || capturingBurst || submitting}
                >
                  Start Camera
                </button>
                <button
                  type="button"
                  className="btn-secondary !px-2.5 !py-1.5"
                  onClick={runGuidedBurstCapture}
                  disabled={!isCaptureSupported || capturingBurst || submitting}
                >
                  {capturingBurst ? 'Capturing...' : (submitting ? 'Enrolling...' : 'Capture Burst + Enroll')}
                </button>
                <button
                  type="button"
                  className="btn-secondary !px-2.5 !py-1.5"
                  onClick={stopGuidedCapture}
                  disabled={capturingBurst || submitting}
                >
                  Stop Camera
                </button>
              </div>
            </div>

            <div className="rounded-lg border border-surface-700 bg-surface-900/40 p-3 space-y-2">
              <p className="text-xs text-surface-200">Enrollment Verification Test</p>
              <p className="text-xs text-surface-500">
                After enrollment, upload a fresh test photo to confirm the current embeddings match the expected student.
              </p>
              <div className="flex items-center gap-2 flex-wrap">
                <input
                  type="file"
                  accept="image/*"
                  onChange={(e) => {
                    const file = e.target.files?.[0] || null;
                    setEnrollmentTestFile(file);
                  }}
                  className="input py-2 flex-1 min-w-[220px]"
                />
                <button
                  type="button"
                  className="btn-secondary !px-2.5 !py-1.5"
                  onClick={runEnrollmentTest}
                  disabled={!enrollmentTestFile || testingEnrollment || submitting || capturingBurst}
                >
                  {testingEnrollment ? 'Testing...' : 'Run Test Match'}
                </button>
              </div>
              {enrollmentTestFile ? (
                <p className="text-xs text-surface-400">Selected: {enrollmentTestFile.name}</p>
              ) : null}

              {enrollmentTestResult ? (
                <div className={`rounded border p-2.5 text-xs space-y-1.5 ${
                  enrollmentTestResult.is_match
                    ? 'border-accent-500/40 bg-accent-500/10 text-accent-200'
                    : 'border-warning-500/40 bg-warning-500/10 text-warning-200'
                }`}>
                  <p className="font-semibold">
                    {enrollmentTestResult.is_match ? 'Match Confirmed' : 'Match Not Confirmed'}
                  </p>
                  <p>{enrollmentTestResult.reason}</p>
                  <p>
                    Best match: {enrollmentTestResult.best_match_student_name || '-'}
                    {enrollmentTestResult.best_match_student_id ? ` (ID ${enrollmentTestResult.best_match_student_id})` : ''}
                    {' | '}score {Number.isFinite(enrollmentTestResult.best_match_score)
                      ? enrollmentTestResult.best_match_score.toFixed(3)
                      : '-'}
                  </p>
                  <p>
                    Expected student score: {Number.isFinite(enrollmentTestResult.expected_student_score)
                      ? enrollmentTestResult.expected_student_score.toFixed(3)
                      : '-'}
                    {' | '}margin {Number.isFinite(enrollmentTestResult.margin)
                      ? enrollmentTestResult.margin.toFixed(3)
                      : '-'}
                  </p>
                  <p>
                    Faces detected: {enrollmentTestResult.detected_faces}
                    {' | '}pose: {enrollmentTestResult.estimated_pose_label || '-'}
                    {' | '}quality: {Number.isFinite(enrollmentTestResult.quality_score)
                      ? enrollmentTestResult.quality_score.toFixed(3)
                      : '-'}
                  </p>
                  {enrollmentTestResult.face_selection_warning ? (
                    <p className="text-surface-300">Detector note: {enrollmentTestResult.face_selection_warning}</p>
                  ) : null}
                  {Array.isArray(enrollmentTestResult.candidates) && enrollmentTestResult.candidates.length > 0 ? (
                    <div className="pt-1 border-t border-surface-700/70">
                      <p className="text-surface-300">Top candidates:</p>
                      <div className="space-y-1 mt-1">
                        {enrollmentTestResult.candidates.map((candidate) => (
                          <div key={`${candidate.student_id}-${candidate.student_name}`} className="flex items-center justify-between gap-2 text-[11px]">
                            <span className="truncate">{candidate.student_name} (ID {candidate.student_id})</span>
                            <span>{Number.isFinite(candidate.score) ? candidate.score.toFixed(3) : '-'}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>

            {enrollFeedback ? (
              <div className={`rounded-lg border p-3 text-xs ${
                enrollFeedbackTone === 'error'
                  ? 'border-danger-500/40 bg-danger-500/10 text-danger-200'
                  : enrollFeedbackTone === 'success'
                    ? 'border-accent-500/40 bg-accent-500/10 text-accent-200'
                    : enrollFeedbackTone === 'warning'
                      ? 'border-warning-500/40 bg-warning-500/10 text-warning-200'
                      : 'border-surface-700 bg-surface-900/60 text-surface-200'
              }`}>
                {enrollFeedback}
              </div>
            ) : null}

            {(enrollSummary || enrollQuality) ? (
              <div className="rounded-lg border border-primary-500/30 bg-primary-500/10 p-3 space-y-1 text-xs text-primary-200">
                <p>{getNextPoseAction()}</p>
              </div>
            ) : null}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-surface-400 mb-1">Pose Label</label>
                <select
                  value={enrollForm.pose_label}
                  onChange={(e) => setEnrollForm((prev) => ({ ...prev, pose_label: e.target.value }))}
                  disabled={enrollForm.auto_pose}
                  className="input"
                >
                  <option value="frontal">Frontal</option>
                  <option value="left_34">Left 3/4</option>
                  <option value="right_34">Right 3/4</option>
                </select>
              </div>
              <label className="flex items-center gap-2 text-sm text-surface-300 mt-6 sm:mt-0">
                <input
                  type="checkbox"
                  checked={enrollForm.auto_pose}
                  onChange={(e) => setEnrollForm((prev) => ({ ...prev, auto_pose: e.target.checked }))}
                />
                Auto-detect pose
              </label>
            </div>

            {enrollSummary ? (
              <div className="rounded-lg border border-surface-700 bg-surface-900/60 p-3 space-y-2">
                <p className="text-sm text-surface-200">{enrollSummary.message}</p>
                <p className="text-xs text-surface-500">
                  Accepted {enrollSummary.new_embeddings} new embedding(s). Total: {enrollSummary.total_embeddings}/{enrollSummary.required_embeddings}
                </p>
                {rejectDiagnostics.length > 0 ? (
                  <div className="rounded border border-warning-500/30 bg-warning-500/10 p-2 space-y-1 text-[11px] text-warning-100">
                    <p className="font-semibold">
                      Reject Diagnostics
                      {enrollSummary?.dominant_reject_reason_label ? `: ${enrollSummary.dominant_reject_reason_label}` : ''}
                    </p>
                    {rejectDiagnostics.map((item) => (
                      <p key={`reject-diagnostic-${item.code}`}>
                        {item.label}: {item.count}
                      </p>
                    ))}
                  </div>
                ) : null}
                <div className="max-h-40 overflow-auto text-xs text-surface-400 space-y-1 pr-1">
                  {enrollSummary.checks?.map((check, idx) => (
                    <div key={`${check.filename}-${idx}`} className="flex items-center justify-between gap-3">
                      <span className="truncate">{check.filename}</span>
                      <span className={check.accepted ? 'text-accent-300' : 'text-warning-300'}>
                        {check.accepted ? 'accepted' : (check.reason || 'rejected')}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {qualityLoading ? (
              <div className="text-xs text-surface-500">Loading enrollment quality...</div>
            ) : null}

            {templatesLoading ? (
              <div className="text-xs text-surface-500">Loading templates...</div>
            ) : null}

            {enrollQuality ? (
              <div className="rounded-lg border border-surface-700 bg-surface-900/40 p-3 space-y-2 text-xs text-surface-300">
                <p>
                  Active templates: {enrollQuality.active_embeddings}/{enrollQuality.required_embeddings}
                </p>
                <p>
                  Pose coverage: frontal {enrollQuality.pose_coverage?.frontal || 0}, left_34 {enrollQuality.pose_coverage?.left_34 || 0}, right_34 {enrollQuality.pose_coverage?.right_34 || 0}
                </p>
                {Object.keys(enrollQuality.missing_pose_coverage || {}).length > 0 ? (
                  <p className="text-warning-300">
                    Missing coverage: {Object.entries(enrollQuality.missing_pose_coverage).map(([pose, count]) => `${pose}:${count}`).join(', ')}
                  </p>
                ) : (
                  <p className="text-accent-300">Pose coverage complete.</p>
                )}
              </div>
            ) : null}

            {enrollAnalytics ? (
              <div className="rounded-lg border border-surface-700 bg-surface-900/40 p-3 space-y-2 text-xs text-surface-300">
                <p>
                  Analytics: total {enrollAnalytics.total_templates}, active {enrollAnalytics.active_templates}, backup {enrollAnalytics.backup_templates}, quarantined {enrollAnalytics.quarantined_templates}
                </p>
                <p>
                  Avg quality {enrollAnalytics.average_quality_score?.toFixed ? enrollAnalytics.average_quality_score.toFixed(3) : '-'} | Avg retention {enrollAnalytics.average_retention_score?.toFixed ? enrollAnalytics.average_retention_score.toFixed(3) : '-'}
                </p>
                <p>
                  Risk flags: high collision {enrollAnalytics.high_collision_templates}, low quality {enrollAnalytics.low_quality_templates}
                </p>
              </div>
            ) : null}

            {enrollHistory?.events?.length > 0 ? (
              <div className="rounded-lg border border-surface-700 bg-surface-900/40 p-3 space-y-2 text-xs text-surface-300">
                <p className="text-surface-200">Historical Analytics</p>
                <div className="max-h-32 overflow-auto space-y-1 pr-1">
                  {enrollHistory.events.slice(-8).reverse().map((evt, idx) => (
                    <div key={`${evt.timestamp}-${idx}`} className="flex items-center justify-between gap-3 border border-surface-700 rounded px-2 py-1">
                      <span className="truncate">{evt.event_type}</span>
                      <span className="text-surface-500">{evt.timestamp ? new Date(evt.timestamp).toLocaleString() : '-'}</span>
                    </div>
                  ))}
                </div>
                <div className="max-h-24 overflow-auto space-y-1 pr-1">
                  {(enrollHistory.pose_drift_timeline || []).slice(-6).reverse().map((point, idx) => (
                    <div key={`${point.timestamp}-${idx}`} className="text-[11px] text-surface-400 flex items-center justify-between gap-3">
                      <span>{point.timestamp ? new Date(point.timestamp).toLocaleTimeString() : '-'}</span>
                      <span>F:{point.frontal} L:{point.left_34} R:{point.right_34}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {enrollTemplates.length > 0 ? (
              <div className="rounded-lg border border-surface-700 bg-surface-900/40 p-3 space-y-2">
                <p className="text-xs text-surface-300">Template Review</p>
                <div className="max-h-40 overflow-auto space-y-1 pr-1">
                  {enrollTemplates.slice(0, 12).map((tpl) => (
                    <div key={tpl.id} className="text-xs text-surface-400 border border-surface-700 rounded-md px-2 py-1.5 space-y-1">
                      <div className="flex items-center justify-between gap-2">
                        <span>#{tpl.id} {tpl.model_name} {tpl.pose_label} {tpl.resolution}</span>
                        <span className={tpl.template_status === 'active' ? 'text-accent-300' : tpl.template_status === 'backup' ? 'text-warning-300' : 'text-danger-300'}>
                          {tpl.template_status}
                        </span>
                      </div>
                      <div className="flex items-center justify-between gap-2">
                        <span>retention {tpl.retention_score?.toFixed ? tpl.retention_score.toFixed(3) : '-'}</span>
                        <div className="flex items-center gap-1">
                          <button
                            type="button"
                            className="btn-secondary !px-2 !py-1"
                            onClick={() => updateTemplateStatus(tpl.id, 'active')}
                            disabled={updatingTemplateId === tpl.id}
                          >
                            Promote
                          </button>
                          <button
                            type="button"
                            className="btn-secondary !px-2 !py-1"
                            onClick={() => updateTemplateStatus(tpl.id, 'backup')}
                            disabled={updatingTemplateId === tpl.id}
                          >
                            Backup
                          </button>
                          <button
                            type="button"
                            className="btn-secondary !px-2 !py-1 text-danger-300 border-danger-500/40 hover:bg-danger-500/15"
                            onClick={() => updateTemplateStatus(tpl.id, 'quarantined')}
                            disabled={updatingTemplateId === tpl.id}
                          >
                            Quarantine
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="flex items-center justify-end gap-2 pt-2">
              <button type="button" className="btn-secondary" onClick={closeEnrollModal}>Close</button>
              <button type="submit" className="btn-primary" disabled={submitting}>
                {submitting ? 'Enrolling...' : `Run Enrollment (${enrollForm.files.length})`}
              </button>
            </div>
          </form>
        </ModalShell>
      ) : null}

      {deleteStudent ? (
        <ModalShell title="Delete Student" onClose={() => setDeleteStudent(null)}>
          <div className="space-y-5">
            <p className="text-sm text-surface-300">
              Delete student <span className="font-semibold text-surface-100">{deleteStudent.name}</span>? This action cannot be undone.
            </p>
            <div className="flex items-center justify-end gap-2">
              <button className="btn-secondary" onClick={() => setDeleteStudent(null)}>Cancel</button>
              <button className="btn-primary bg-danger-600 hover:bg-danger-500" onClick={submitDelete} disabled={submitting}>
                {submitting ? 'Deleting...' : 'Delete Student'}
              </button>
            </div>
          </div>
        </ModalShell>
      ) : null}
    </div>
  );
}
