import axios from 'axios';

const backendBaseUrl = (process.env.REACT_APP_BACKEND_URL || 'http://localhost:8000').replace(/\/+$/, '');
const API_URL = `${backendBaseUrl}/api`;

// Habits API
export const habitsApi = {
  getAll: () => axios.get(`${API_URL}/habits`),
  get: (id) => axios.get(`${API_URL}/habits/${id}`),
  create: (data) => axios.post(`${API_URL}/habits`, data),
  update: (id, data) => axios.put(`${API_URL}/habits/${id}`, data),
  delete: (id) => axios.delete(`${API_URL}/habits/${id}`),
  getLogs: (id, startDate, endDate) => 
    axios.get(`${API_URL}/habits/${id}/logs`, { params: { start_date: startDate, end_date: endDate } }),
};

// Habit Logs API
export const logsApi = {
  create: (data) => axios.post(`${API_URL}/habits/log`, data),
  getAll: (startDate, endDate) => 
    axios.get(`${API_URL}/logs`, { params: { start_date: startDate, end_date: endDate } }),
};

// Analytics API
export const analyticsApi = {
  getDashboard: () => axios.get(`${API_URL}/analytics/dashboard`),
  getWeekly: () => axios.get(`${API_URL}/analytics/weekly`),
  getMonthly: (year, month) => axios.get(`${API_URL}/analytics/monthly`, { params: { year, month } }),
  getYearly: (year) => axios.get(`${API_URL}/analytics/yearly`, { params: { year } }),
};

// Leaderboard API
export const leaderboardApi = {
  getWeekly: (limit = 10, offset = 0) => axios.get(`${API_URL}/leaderboard/weekly`, { params: { limit, offset } }),
  getCountdown: () => axios.get(`${API_URL}/leaderboard/countdown`),
  getHistory: (limit = 10, offset = 0) => axios.get(`${API_URL}/leaderboard/history`, { params: { limit, offset } }),
};

export default { habitsApi, logsApi, analyticsApi, leaderboardApi };
