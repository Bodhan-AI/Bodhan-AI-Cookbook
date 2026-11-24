const API_BASE = '/api';

function getAuthHeader(): Record<string, string> {
  const token = localStorage.getItem('pestsignal_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const headers = {
    ...getAuthHeader(),
    ...options.headers,
  };

  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    // If token invalid, handle cleanup
    // localStorage.removeItem('pestsignal_token');
    // localStorage.removeItem('pestsignal_user');
  }

  if (!response.ok) {
    let errorDetail = 'An error occurred';
    try {
      const errorJson = await response.json();
      errorDetail = errorJson.detail || errorJson.error || JSON.stringify(errorJson);
    } catch {
      errorDetail = await response.text();
    }
    throw new Error(errorDetail || `HTTP error ${response.status}`);
  }

  return response.json();
}

// Authentication
export const authApi = {
  login: (data: any) =>
    request<{ access_token: string; user: any }>('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  register: (data: any) =>
    request<{ access_token: string; user: any }>('/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  getMe: () => request<any>('/auth/me'),
};

// Crop Reports
export const reportApi = {
  create: (data: any) =>
    request<any>('/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  list: (params: Record<string, string | undefined> = {}) => {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, val]) => {
      if (val) searchParams.append(key, val);
    });
    const qs = searchParams.toString();
    return request<any[]>(`/reports${qs ? `?${qs}` : ''}`);
  },
  get: (id: string) => request<any>(`/reports/${id}`),
  updateStatus: (id: string, status: string, internal_note?: string) =>
    request<any>(`/reports/${id}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, internal_note }),
    }),
  addNote: (id: string, note: string) =>
    request<any>(`/reports/${id}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    }),
};

// AI Services
export const aiApi = {
  transcribe: async (audioBlob: Blob, language?: string) => {
    const formData = new FormData();
    formData.append('file', audioBlob, 'observation.wav');
    if (language) {
      formData.append('language', language);
    }

    const token = localStorage.getItem('pestsignal_token');
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const res = await fetch(`${API_BASE}/ai/transcribe`, {
      method: 'POST',
      headers,
      body: formData,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to transcribe audio clip.');
    }
    return res.json();
  },
  translate: (text: string, source_language: string, target_language = 'en') =>
    request<{
      success: boolean;
      translated_text: string;
      source_language: string;
      target_language: string;
      engine: string;
      bodhan_status?: any;
    }>('/ai/translate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, source_language, target_language }),
    }),
  getLanguages: () => request<{ code: string; name: string }[]>('/ai/languages'),
};

// Analytics
export const analyticsApi = {
  getOverview: () => request<any>('/analytics/overview'),
  getCrops: () => request<any[]>('/analytics/crops'),
  getRegions: () => request<any[]>('/analytics/regions'),
  getLanguages: () => request<any[]>('/analytics/languages'),
  getSymptoms: () => request<any[]>('/analytics/symptoms'),
  getTimeline: (days = 14) => request<any[]>(`/analytics/timeline?days=${days}`),
};

// Alerts & Outbreaks
export const alertApi = {
  list: (params: Record<string, string | undefined> = {}) => {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, val]) => {
      if (val) searchParams.append(key, val);
    });
    const qs = searchParams.toString();
    return request<any[]>(`/alerts${qs ? `?${qs}` : ''}`);
  },
  get: (id: string) => request<any>(`/alerts/${id}`),
  detect: () =>
    request<any[]>('/alerts/detect', {
      method: 'POST',
    }),
  updateStatus: (id: string, status: string) =>
    request<any>(`/alerts/${id}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    }),
};

// Notifications
export const notificationApi = {
  list: () => request<any[]>('/notifications'),
  markRead: (id: number) =>
    request<any>(`/notifications/${id}/read`, {
      method: 'PATCH',
    }),
  markAllRead: () =>
    request<any>('/notifications/read-all', {
      method: 'POST',
    }),
};

// Users (Admin/Officer)
export const userApi = {
  list: () => request<any[]>('/users'),
  updateRole: (id: number, role: string) =>
    request<any>(`/users/${id}/role`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role }),
    }),
};
