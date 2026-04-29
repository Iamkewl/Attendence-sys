import { useEffect, useMemo, useRef, useState } from 'react';
import { FlaskConical, Camera, RefreshCw, CheckSquare, Square } from 'lucide-react';
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

function MetricCard({ label, value }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value text-surface-50">{value}</div>
    </div>
  );
}

export default function TestingLabPage() {
  const [students, setStudents] = useState([]);
  const [selectedExpectedIds, setSelectedExpectedIds] = useState([]);
  const [testImageFile, setTestImageFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [cameraReady, setCameraReady] = useState(false);
  const [deploymentConfig, setDeploymentConfig] = useState(null);
  const [trackStats, setTrackStats] = useState(null);

  const videoRef = useRef(null);
  const streamRef = useRef(null);

  const isCaptureSupported = typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;

  const expectedNames = useMemo(() => {
    const map = new Map(students.map((student) => [student.id, student.name]));
    return selectedExpectedIds.map((id) => map.get(id) || `Student ${id}`);
  }, [selectedExpectedIds, students]);

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    setCameraReady(false);
  };

  const startCamera = async () => {
    if (!isCaptureSupported) {
      setError('Camera capture is not supported in this browser.');
      return;
    }

    setError('');
    try {
      if (!streamRef.current) {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            width: { ideal: 1920 },
            height: { ideal: 1080 },
            frameRate: { ideal: 30 },
          },
          audio: false,
        });
        streamRef.current = stream;
      }
      if (videoRef.current) {
        videoRef.current.srcObject = streamRef.current;
        await videoRef.current.play().catch(() => {});
      }
      setCameraReady(true);
    } catch {
      setError('Unable to access camera. Check browser permission and device availability.');
    }
  };

  const capturePhoto = async () => {
    const video = videoRef.current;
    if (!video || video.readyState < 2) {
      setError('Camera stream is not ready yet.');
      return;
    }

    const width = video.videoWidth || 1280;
    const height = video.videoHeight || 720;
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;

    const ctx = canvas.getContext('2d');
    if (!ctx) {
      setError('Unable to initialize capture surface.');
      return;
    }

    ctx.drawImage(video, 0, 0, width, height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.98));
    if (!blob) {
      setError('Failed to capture photo from camera stream.');
      return;
    }

    const file = new File([blob], `testing_lab_${Date.now()}.jpg`, {
      type: 'image/jpeg',
      lastModified: Date.now(),
    });

    setTestImageFile(file);
    const url = URL.createObjectURL(file);
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return url;
    });
    setNotice('Captured a test image from camera.');
  };

  const loadStudents = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getStudents({ enrolled_only: true, limit: 300 });
      setStudents(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(await parseApiError(err, 'Failed to load enrolled students for expected list.'));
      setStudents([]);
    } finally {
      setLoading(false);
    }
  };

  const loadDeploymentConfig = async () => {
    try {
      const cfg = await api.getSystemSettings();
      setDeploymentConfig(cfg || null);
    } catch {
      setDeploymentConfig(null);
    }
  };

  const loadTrackStats = async () => {
    try {
      const stats = await api.getTrackStats();
      setTrackStats(stats || null);
    } catch {
      setTrackStats(null);
    }
  };

  useEffect(() => {
    loadStudents();
    loadDeploymentConfig();
    loadTrackStats();
    return () => {
      stopCamera();
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
      }
    };
  }, []);

  const toggleExpected = (studentId) => {
    setSelectedExpectedIds((prev) => (
      prev.includes(studentId)
        ? prev.filter((id) => id !== studentId)
        : [...prev, studentId]
    ));
  };

  const runTest = async () => {
    if (!testImageFile) {
      setError('Please upload or capture a test image first.');
      return;
    }

    setRunning(true);
    setError('');
    setNotice('');
    setResult(null);

    try {
      const payload = await api.testMultiFaceScene(testImageFile, selectedExpectedIds);
      setResult(payload);
      setNotice('Multi-face classroom test completed. Review metrics and matched identities below.');
    } catch (err) {
      setError(await parseApiError(err, 'Failed to run multi-face classroom test.'));
    } finally {
      setRunning(false);
    }
  };

  const selectAllExpected = () => {
    setSelectedExpectedIds(students.map((student) => student.id));
  };

  const clearExpected = () => {
    setSelectedExpectedIds([]);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-surface-50 tracking-tight">Testing Lab</h1>
          <p className="text-surface-500 text-sm mt-0.5">
            Capture a classroom image and evaluate multi-face detection and recognition accuracy.
          </p>
        </div>
        <button className="btn-secondary" onClick={loadStudents} disabled={loading || running}>
          <RefreshCw size={15} />
          Reload Students
        </button>
        <button className="btn-secondary" onClick={loadTrackStats} disabled={loading || running}>
          <RefreshCw size={15} />
          Refresh Tracking
        </button>
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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card space-y-3">
          <div className="flex items-center gap-2 text-surface-100">
            <Camera size={16} />
            <h2 className="font-semibold">Camera Capture</h2>
          </div>
          <p className="text-xs text-surface-500">
            Place 5-6 people in frame, then capture one image for evaluation.
          </p>

          <div className="rounded-md border border-surface-700 overflow-hidden bg-surface-950/60 min-h-[220px]">
            <video ref={videoRef} autoPlay muted playsInline className="w-full max-h-72 object-cover" />
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <button type="button" className="btn-secondary !px-2.5 !py-1.5" onClick={startCamera} disabled={!isCaptureSupported || running}>
              Start Camera
            </button>
            <button type="button" className="btn-secondary !px-2.5 !py-1.5" onClick={capturePhoto} disabled={!cameraReady || running}>
              Capture Photo
            </button>
            <button type="button" className="btn-secondary !px-2.5 !py-1.5" onClick={stopCamera} disabled={running}>
              Stop Camera
            </button>
          </div>

          <div>
            <label className="block text-sm text-surface-300 mb-1.5">Or Upload an Existing Classroom Photo</label>
            <input
              type="file"
              accept="image/*"
              onChange={(e) => {
                const file = e.target.files?.[0] || null;
                setTestImageFile(file);
                if (file) {
                  const url = URL.createObjectURL(file);
                  setPreviewUrl((prev) => {
                    if (prev) URL.revokeObjectURL(prev);
                    return url;
                  });
                }
              }}
              className="input py-2"
            />
          </div>

          {previewUrl ? (
            <div className="space-y-2">
              <p className="text-xs text-surface-400">Current test image preview:</p>
              <img src={previewUrl} alt="Testing preview" className="w-full rounded border border-surface-700" />
            </div>
          ) : null}
        </div>

        <div className="card space-y-3">
          <div className="flex items-center gap-2 text-surface-100">
            <FlaskConical size={16} />
            <h2 className="font-semibold">Expected Students (Optional)</h2>
          </div>
          <p className="text-xs text-surface-500">
            Select students you expect in the image to compute precision, recall, and F1.
          </p>

          <div className="flex items-center gap-2">
            <button type="button" className="btn-secondary !px-2.5 !py-1.5" onClick={selectAllExpected} disabled={students.length === 0 || running}>
              <CheckSquare size={13} />
              Select All
            </button>
            <button type="button" className="btn-secondary !px-2.5 !py-1.5" onClick={clearExpected} disabled={selectedExpectedIds.length === 0 || running}>
              <Square size={13} />
              Clear
            </button>
          </div>

          <div className="max-h-72 overflow-auto border border-surface-700 rounded-md p-2 space-y-1">
            {students.map((student) => {
              const checked = selectedExpectedIds.includes(student.id);
              return (
                <label key={student.id} className="flex items-center justify-between gap-2 px-2 py-1.5 rounded hover:bg-surface-800 cursor-pointer">
                  <span className="text-sm text-surface-200 truncate">{student.name}</span>
                  <input type="checkbox" checked={checked} onChange={() => toggleExpected(student.id)} />
                </label>
              );
            })}
            {!loading && students.length === 0 ? (
              <p className="text-xs text-surface-500">No enrolled students available.</p>
            ) : null}
          </div>

          {expectedNames.length > 0 ? (
            <div className="rounded border border-surface-700 p-2">
              <p className="text-xs text-surface-400 mb-1">Selected expected ({expectedNames.length}):</p>
              <div className="flex flex-wrap gap-1">
                {expectedNames.slice(0, 12).map((name) => (
                  <span key={name} className="badge border border-surface-700 text-surface-300">{name}</span>
                ))}
              </div>
            </div>
          ) : null}

          <button type="button" className="btn-primary" onClick={runTest} disabled={running || !testImageFile}>
            {running ? 'Running Test...' : 'Run Multi-Face Test'}
          </button>

          {deploymentConfig ? (
            <div className="rounded border border-surface-700 p-2 text-xs text-surface-300 space-y-1">
              <p className="text-surface-200 font-medium">Active Deployment Gates</p>
              <p>Primary model: {deploymentConfig.primary_model}</p>
              <p>
                Match thresholds: strict {Number(deploymentConfig.confidence_threshold).toFixed(2)}, relaxed {Number(deploymentConfig.face_match_relaxed_threshold).toFixed(2)}, margin {Number(deploymentConfig.face_match_margin).toFixed(2)}
              </p>
              <p>
                Quality gates: min size {deploymentConfig.min_face_size_px}px, blur {Number(deploymentConfig.min_blur_variance).toFixed(1)}, quality {Number(deploymentConfig.min_face_quality_score).toFixed(2)}
              </p>
            </div>
          ) : null}
        </div>
      </div>

      {trackStats ? (
        <div className="card space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-surface-100">Cross-Camera Identity Handoff</h2>
            <span className="badge border border-surface-700 text-surface-300">
              ReID: {trackStats.cross_camera_reid_enabled ? 'enabled' : 'disabled'}
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
            <MetricCard label="Active Tracks" value={trackStats.active_tracks ?? 0} />
            <MetricCard label="Confirmed Tracks" value={trackStats.confirmed_tracks ?? 0} />
            <MetricCard label="Linked Handovers" value={trackStats.cross_camera?.link_count ?? 0} />
            <MetricCard label="Rejected Links" value={trackStats.cross_camera?.rejected_link_count ?? 0} />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <div className="rounded border border-surface-700 p-3">
              <p className="text-xs text-surface-400 mb-2">Link confidence distribution</p>
              <div className="flex flex-wrap gap-1">
                {Object.entries(trackStats.cross_camera?.confidence_distribution || {}).map(([bucket, count]) => (
                  <span key={bucket} className="badge border border-surface-700 text-surface-300">
                    {bucket}: {count}
                  </span>
                ))}
              </div>
            </div>

            <div className="rounded border border-surface-700 p-3 overflow-x-auto">
              <p className="text-xs text-surface-400 mb-2">Per-camera tracking status</p>
              {(trackStats.cameras || []).length > 0 ? (
                <table className="table min-w-[420px]">
                  <thead>
                    <tr>
                      <th>Camera</th>
                      <th>Active</th>
                      <th>Confirmed</th>
                      <th>Avg Age (s)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(trackStats.cameras || []).map((camera) => (
                      <tr key={camera.camera_id}>
                        <td>{camera.camera_id}</td>
                        <td>{camera.active_tracks}</td>
                        <td>{camera.confirmed_tracks}</td>
                        <td>{Number(camera.average_track_age_seconds || 0).toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="text-xs text-surface-500">No active camera track stats available yet.</p>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {result ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
            <MetricCard label="Detected Faces" value={result.detected_faces} />
            <MetricCard label="Recognized Faces" value={result.recognized_faces} />
            <MetricCard label="Unmatched Detections" value={result.unmatched_detected_faces} />
            <MetricCard label="Expected Faces" value={result.expected_faces} />
          </div>

          {(result.precision !== null && result.precision !== undefined) ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <MetricCard label="Precision" value={`${(result.precision * 100).toFixed(1)}%`} />
              <MetricCard label="Recall" value={`${(result.recall * 100).toFixed(1)}%`} />
              <MetricCard label="F1 Score" value={`${(result.f1_score * 100).toFixed(1)}%`} />
            </div>
          ) : null}

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <div className="card">
              <h3 className="text-sm font-semibold text-surface-100 mb-3">Matched Students</h3>
              {result.matches?.length ? (
                <div className="overflow-x-auto">
                  <table className="table min-w-[520px]">
                    <thead>
                      <tr>
                        <th>Student</th>
                        <th>Confidence</th>
                        <th>Quality</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.matches.map((match) => (
                        <tr key={`${match.student_id}-${match.confidence}`}>
                          <td>{match.student_name} (ID {match.student_id})</td>
                          <td>{Number(match.confidence).toFixed(3)}</td>
                          <td>{Number(match.quality).toFixed(3)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-xs text-surface-500">No students recognized in this image.</p>
              )}
            </div>

            <div className="card space-y-3">
              <h3 className="text-sm font-semibold text-surface-100">Diagnostic Notes</h3>
              <div className="space-y-1 text-xs text-surface-300">
                {(result.notes || []).map((note, idx) => (
                  <p key={`note-${idx}`}>- {note}</p>
                ))}
              </div>
              {Object.keys(result.quality_reject_summary || {}).length > 0 ? (
                <div>
                  <p className="text-xs text-warning-300 mb-1">Quality reject summary:</p>
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(result.quality_reject_summary).map(([reason, count]) => (
                      <span key={`reject-${reason}`} className="badge border border-warning-500/40 text-warning-200">
                        {reason}: {count}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
              {(result.missed_expected_students || []).length > 0 ? (
                <div>
                  <p className="text-xs text-warning-300 mb-1">Missed expected students:</p>
                  <div className="flex flex-wrap gap-1">
                    {result.missed_expected_students.map((name) => (
                      <span key={`missed-${name}`} className="badge border border-warning-500/40 text-warning-200">{name}</span>
                    ))}
                  </div>
                </div>
              ) : null}
              {(result.false_positive_students || []).length > 0 ? (
                <div>
                  <p className="text-xs text-danger-300 mb-1">False positive students:</p>
                  <div className="flex flex-wrap gap-1">
                    {result.false_positive_students.map((name) => (
                      <span key={`fp-${name}`} className="badge border border-danger-500/40 text-danger-200">{name}</span>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </div>

          {(result.detections || []).length > 0 ? (
            <div className="card">
              <h3 className="text-sm font-semibold text-surface-100 mb-3">Detected Faces Diagnostics</h3>
              <div className="overflow-x-auto">
                <table className="table min-w-[760px]">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>BBox</th>
                      <th>Min Size (px)</th>
                      <th>Quality</th>
                      <th>Sharpness</th>
                      <th>Gate</th>
                      <th>Reject Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.detections.map((det, idx) => (
                      <tr key={`det-${idx}`}>
                        <td>{idx + 1}</td>
                        <td>[{det.bbox.join(', ')}]</td>
                        <td>{Number(det.face_size_px).toFixed(0)}</td>
                        <td>{Number(det.quality_score).toFixed(3)}</td>
                        <td>{Number(det.sharpness).toFixed(1)}</td>
                        <td>
                          <span className={det.passes_quality_gate ? 'text-accent-300' : 'text-warning-300'}>
                            {det.passes_quality_gate ? 'pass' : 'reject'}
                          </span>
                        </td>
                        <td>{det.reject_reason || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {result.annotated_detections_image_b64 ? (
            <div className="card space-y-2">
              <h3 className="text-sm font-semibold text-surface-100">Detection Overlay (All Faces)</h3>
              <img
                src={`data:image/jpeg;base64,${result.annotated_detections_image_b64}`}
                alt="Annotated detection overlay"
                className="w-full rounded border border-surface-700"
              />
            </div>
          ) : null}

          {result.annotated_image_b64 ? (
            <div className="card space-y-2">
              <h3 className="text-sm font-semibold text-surface-100">Annotated Output</h3>
              <img
                src={`data:image/jpeg;base64,${result.annotated_image_b64}`}
                alt="Annotated recognition output"
                className="w-full rounded border border-surface-700"
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
