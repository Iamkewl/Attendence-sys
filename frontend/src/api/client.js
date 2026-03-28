/**
 * API client for the Attendance System V2 backend.
 *
 * Handles JWT authentication, token refresh, and all REST endpoints.
 */

const API_BASE = '/api/v1';

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

  async _fetch(path, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    if (this.accessToken) {
      headers['Authorization'] = `Bearer ${this.accessToken}`;
    }

    const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

    // Auto-refresh on 401
    if (res.status === 401 && this.refreshToken) {
      const refreshed = await this._refreshAccessToken();
      if (refreshed) {
        headers['Authorization'] = `Bearer ${this.accessToken}`;
        return fetch(`${API_BASE}${path}`, { ...options, headers });
      }
    }

    return res;
  }

  async _refreshAccessToken() {
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
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

  async get(path) {
    const res = await this._fetch(path);
    if (!res.ok) throw new ApiError(res);
    return res.json();
  }

  async post(path, body) {
    const res = await this._fetch(path, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new ApiError(res);
    return res.json();
  }

  async put(path, body) {
    const res = await this._fetch(path, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new ApiError(res);
    return res.json();
  }

  async delete(path) {
    const res = await this._fetch(path, { method: 'DELETE' });
    if (!res.ok) throw new ApiError(res);
    return res.json();
  }

  // ── Auth Endpoints ───────────────────────────────

  async login(email, password) {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) throw new ApiError(res);
    const data = await res.json();
    this.setTokens(data.access_token, data.refresh_token);
    return data;
  }

  logout() {
    this.clearTokens();
  }

  // ── Domain Endpoints ─────────────────────────────

  getStudents() { return this.get('/students'); }
  getStudent(id) { return this.get(`/students/${id}`); }
  getCourses() { return this.get('/courses'); }
  getSchedules() { return this.get('/schedules'); }
  getAttendance(scheduleId) { return this.get(`/attendance/schedule/${scheduleId}`); }
  getSystemHealth() { return this.get('/system/health'); }
  getAIStatus() { return this.get('/system/ai/status'); }
  getUsers() { return this.get('/users'); }
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
