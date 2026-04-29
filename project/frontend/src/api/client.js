/**
 * API client for the Attendance System V2 backend.
 *
 * Handles JWT authentication, token refresh, and all REST endpoints.
 */

const API_BASE = '/api/v1';
const DEFAULT_TIMEOUT_MS = 15000;
const ENROLLMENT_TIMEOUT_MS = 120000;

class ApiClient {
  constructor() {
    this.accessToken = null;
    this.refreshToken = null;
    this._loadTokens();
  }

  // ── Token Management ─────────────────────────────

  _loadTokens() {
    this.accessToken = sessionStorage.getItem('access_token');
    this.refreshToken = localStorage.getItem('refresh_token');
  }

  setTokens(access, refresh) {
    this.accessToken = access;
    this.refreshToken = refresh;
    sessionStorage.setItem('access_token', access);
    if (refresh) localStorage.setItem('refresh_token', refresh);
  }

  clearTokens() {
    this.accessToken = null;
    this.refreshToken = null;
    sessionStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  }

  get isAuthenticated() {
    return !!this.accessToken;
  }

  // ── HTTP Methods ─────────────────────────────────

  async _fetchRaw(url, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } catch (err) {
      if (err?.name === 'AbortError') {
        throw new Error('Request timed out. Backend may still be processing (especially enrollment). Please retry or reduce image count.');
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  _withQuery(path, params = {}) {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        qs.append(key, String(value));
      }
    });
    const queryString = qs.toString();
    return queryString ? `${path}?${queryString}` : path;
  }

  async _fetch(path, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
    const isFormData = options.body instanceof FormData;
    const headers = { ...options.headers };
    if (!isFormData && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }
    if (this.accessToken) {
      headers['Authorization'] = `Bearer ${this.accessToken}`;
    }

    const res = await this._fetchRaw(`${API_BASE}${path}`, { ...options, headers }, timeoutMs);

    // Auto-refresh on 401
    if (res.status === 401 && this.refreshToken) {
      const refreshed = await this._refreshAccessToken();
      if (refreshed) {
        headers['Authorization'] = `Bearer ${this.accessToken}`;
        return this._fetchRaw(`${API_BASE}${path}`, { ...options, headers }, timeoutMs);
      }
    }

    return res;
  }

  async _refreshAccessToken() {
    try {
      const res = await this._fetchRaw(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: this.refreshToken }),
      });
      if (res.ok) {
        const data = await res.json();
        this.setTokens(data.access_token, data.refresh_token || this.refreshToken);
        return true;
      }
    } catch {}
    this.clearTokens();
    return false;
  }

  async get(path, params, timeoutMs = DEFAULT_TIMEOUT_MS) {
    const res = await this._fetch(this._withQuery(path, params), {}, timeoutMs);
    if (!res.ok) throw new ApiError(res);
    return res.json();
  }

  async post(path, body, timeoutMs = DEFAULT_TIMEOUT_MS) {
    const payload = body instanceof FormData ? body : JSON.stringify(body);
    const res = await this._fetch(path, {
      method: 'POST',
      body: payload,
    }, timeoutMs);
    if (!res.ok) throw new ApiError(res);
    return res.json();
  }

  async put(path, body, timeoutMs = DEFAULT_TIMEOUT_MS) {
    const payload = body instanceof FormData ? body : JSON.stringify(body);
    const res = await this._fetch(path, {
      method: 'PUT',
      body: payload,
    }, timeoutMs);
    if (!res.ok) throw new ApiError(res);
    return res.json();
  }

  async patch(path, body, timeoutMs = DEFAULT_TIMEOUT_MS) {
    const payload = body instanceof FormData ? body : JSON.stringify(body);
    const res = await this._fetch(path, {
      method: 'PATCH',
      body: payload,
    }, timeoutMs);
    if (!res.ok) throw new ApiError(res);
    return res.json();
  }

  async delete(path, timeoutMs = DEFAULT_TIMEOUT_MS) {
    const res = await this._fetch(path, { method: 'DELETE' }, timeoutMs);
    if (!res.ok) throw new ApiError(res);
    if (res.status === 204) return null;
    return res.json();
  }

  // ── Auth Endpoints ───────────────────────────────

  async login(email, password) {
    const res = await this._fetchRaw(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      let message = 'Login failed. Please try again.';
      try {
        const payload = await res.json();
        message = payload?.detail?.message || payload?.message || message;
      } catch {
        if (res.status === 401) message = 'Invalid email or password.';
        if (res.status === 429) message = 'Too many login attempts. Try again later.';
      }

      const error = new Error(message);
      error.status = res.status;
      throw error;
    }
    const data = await res.json();
    this.setTokens(data.access_token, data.refresh_token);
    return data;
  }

  logout() {
    this.clearTokens();
  }

  // ── Domain Endpoints ─────────────────────────────

  getStudents(params = {}) { return this.get('/students', params); }
  getStudent(id) { return this.get(`/students/${id}`); }
  createStudent(payload) { return this.post('/students', payload); }
  enrollStudentWithImages(studentId, files, options = {}) {
    const formData = new FormData();
    files.forEach((file) => formData.append('images', file));
    formData.append('pose_label', options.pose_label || 'frontal');
    formData.append('auto_pose', options.auto_pose === false ? 'false' : 'true');
    return this.post(`/students/${studentId}/enroll/images`, formData, ENROLLMENT_TIMEOUT_MS);
  }
  testStudentEnrollment(studentId, file) {
    const formData = new FormData();
    formData.append('image', file);
    return this.post(`/students/${studentId}/enrollment/test`, formData, ENROLLMENT_TIMEOUT_MS);
  }
  getStudentEnrollmentQuality(studentId) { return this.get(`/students/${studentId}/enrollment/quality`); }
  getStudentEnrollmentAnalytics(studentId) { return this.get(`/students/${studentId}/enrollment/analytics`); }
  getStudentEnrollmentAnalyticsHistory(studentId, params = {}) { return this.get(`/students/${studentId}/enrollment/analytics/history`, params); }
  getStudentEnrollmentTemplates(studentId) { return this.get(`/students/${studentId}/enrollment/templates`); }
  updateStudentEnrollmentTemplate(studentId, embeddingId, payload) {
    return this.patch(`/students/${studentId}/enrollment/templates/${embeddingId}`, payload);
  }
  updateStudent(studentId, payload) { return this.patch(`/students/${studentId}`, payload); }
  deleteStudent(studentId) { return this.delete(`/students/${studentId}`); }
  deleteStudentBiometricData(studentId) { return this.delete(`/students/${studentId}/biometric-data`); }
  getCourses() { return this.get('/courses'); }
  getSchedules() { return this.get('/schedules'); }
  getAttendance(scheduleId) { return this.get(`/attendance/${scheduleId}`); }
  async exportAttendanceCsv(scheduleId) {
    const res = await this._fetch(`/attendance/${scheduleId}/export`);
    if (!res.ok) throw new ApiError(res);

    const disposition = res.headers.get('content-disposition') || '';
    const match = disposition.match(/filename="?([^\"]+)"?/i);
    const filename = match?.[1] || `attendance_schedule_${scheduleId}.csv`;
    const blob = await res.blob();
    return { blob, filename };
  }
  getSystemHealth() { return this.get('/health'); }
  getAIStatus() { return this.get('/ai/status'); }
  getDashboardSummary() { return this.get('/dashboard/summary'); }
  getDashboardTrend(params = {}) { return this.get('/dashboard/trend', params); }
  getRecentDetections(params = {}) { return this.get('/dashboard/recent-detections', params); }
  getLiveCameras() { return this.get('/live/cameras'); }
  getLiveStats(scheduleId) { return this.get(`/live/stats/${scheduleId}`); }
  getTrackStats() { return this.get('/tracks'); }
  getDriftStatus() { return this.get('/drift-status'); }
  getRetentionStatus() { return this.get('/retention-status'); }
  getLatestFairnessAudit() { return this.get('/fairness-audit/latest'); }
  getTemplateRefreshLogs(params = {}) { return this.get('/template-refresh/logs', params); }
  rollbackTemplateRefresh(auditLogId) { return this.post(`/template-refresh/${auditLogId}/rollback`, {}); }
  getGovernanceOverview() { return this.get('/governance/overview'); }
  testMultiFaceScene(file, expectedStudentIds = []) {
    const formData = new FormData();
    formData.append('image', file);
    expectedStudentIds.forEach((studentId) => {
      formData.append('expected_student_ids', String(studentId));
    });
    return this.post('/testing/multi-face', formData, ENROLLMENT_TIMEOUT_MS);
  }
  getSystemSettings() { return this.get('/settings'); }
  updateSystemSettings(payload) { return this.patch('/settings', payload); }
  getSystemSettingsHistory(params = {}) { return this.get('/settings/history', params); }
  rollbackSystemSettings(revision_id) { return this.post('/settings/rollback', { revision_id }); }
  getUsers(params = {}) { return this.get('/users', params); }
  createUser(payload) { return this.post('/users', payload); }
  updateUser(userId, payload) { return this.patch(`/users/${userId}`, payload); }
  deleteUser(userId) { return this.delete(`/users/${userId}`); }
}

class ApiError extends Error {
  constructor(response) {
    super(`API Error: ${response.status}`);
    this.status = response.status;
    this.response = response;
  }
}

export const api = new ApiClient();
export default api;
