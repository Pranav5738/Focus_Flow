import React, { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { analyticsApi, leaderboardApi, habitsApi, logsApi } from '../lib/api';
import { 
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, ReferenceLine
} from 'recharts';
import { Flame, Target, TrendingUp, CheckCircle2, Loader2 } from 'lucide-react';
import { cn } from '../lib/utils';
import { Avatar, AvatarFallback, AvatarImage } from '../components/ui/avatar';

const COLORS = {
  primary: '#6366F1',
  success: '#10B981',
  danger: '#EF4444',
  warning: '#F59E0B',
  muted: '#52525B'
};

const KPICard = ({ icon: Icon, label, value, suffix, trend, color = 'primary' }) => {
  const colorClasses = {
    primary: 'from-indigo-500/20 to-indigo-600/10 border-indigo-500/30',
    success: 'from-emerald-500/20 to-emerald-600/10 border-emerald-500/30',
    warning: 'from-amber-500/20 to-amber-600/10 border-amber-500/30',
    danger: 'from-red-500/20 to-red-600/10 border-red-500/30'
  };

  const iconColors = {
    primary: 'text-indigo-400',
    success: 'text-emerald-400',
    warning: 'text-amber-400',
    danger: 'text-red-400'
  };

  return (
    <div 
      className={cn(
        "relative overflow-hidden rounded-xl border p-5 bg-gradient-to-br card-hover",
        colorClasses[color]
      )}
      data-testid={`kpi-${label.toLowerCase().replace(/\s+/g, '-')}`}
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs uppercase tracking-wider text-muted-foreground font-medium">{label}</p>
          <div className="mt-2 flex items-baseline gap-1">
            <span className="text-3xl font-bold tracking-tight font-mono">{value}</span>
            {suffix && <span className="text-lg text-muted-foreground">{suffix}</span>}
          </div>
          {trend && (
            <p className={cn(
              "mt-1 text-xs font-medium",
              trend > 0 ? "text-emerald-400" : "text-red-400"
            )}>
              {trend > 0 ? '+' : ''}{trend}% from last week
            </p>
          )}
        </div>
        <div className={cn("p-2.5 rounded-lg bg-white/5", iconColors[color])}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </div>
  );
};

const CustomTooltip = ({ active, payload, label }) => {
  if (active && payload && payload.length) {
    return (
      <div className="custom-tooltip">
        <p className="text-sm font-medium mb-1">{label}</p>
        {payload.map((entry, index) => (
          <p key={index} className="text-xs" style={{ color: entry.color }}>
            {entry.name}: {entry.value}{entry.unit || ''}
          </p>
        ))}
      </div>
    );
  }
  return null;
};

const pad2 = (n) => String(Math.max(0, n)).padStart(2, '0');

const formatDDHHMMSS = (totalSeconds) => {
  const s = Math.max(0, Number.isFinite(totalSeconds) ? Math.floor(totalSeconds) : 0);
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = s % 60;
  return `${pad2(days)}:${pad2(hours)}:${pad2(minutes)}:${pad2(seconds)}`;
};

const CountdownStrip = ({ daySeconds, weekSeconds, monthSeconds }) => {
  return (
    <div className="rounded-2xl border border-white/5 glass-card glow-primary px-5 py-4" data-testid="leaderboard-countdown-strip">
      <div className="flex flex-col sm:flex-row divide-y sm:divide-y-0 sm:divide-x divide-white/5">
        <div className="flex-1 py-3 sm:py-0 sm:px-6 first:pt-0 last:pb-0 sm:first:pl-0 sm:last:pr-0 flex items-center justify-between sm:flex-col sm:items-start sm:justify-center">
          <p className="text-[11px] uppercase tracking-[0.2em] text-muted-foreground font-medium">Day ends</p>
          <div className="mt-1 text-2xl sm:text-3xl font-mono font-bold tracking-tight text-primary">
            {formatDDHHMMSS(daySeconds)}
          </div>
        </div>
        <div className="flex-1 py-3 sm:py-0 sm:px-6 flex items-center justify-between sm:flex-col sm:items-start sm:justify-center">
          <p className="text-[11px] uppercase tracking-[0.2em] text-muted-foreground font-medium">Week ends</p>
          <div className="mt-1 text-2xl sm:text-3xl font-mono font-bold tracking-tight text-primary">
            {formatDDHHMMSS(weekSeconds)}
          </div>
        </div>
        <div className="flex-1 py-3 sm:py-0 sm:px-6 flex items-center justify-between sm:flex-col sm:items-start sm:justify-center">
          <p className="text-[11px] uppercase tracking-[0.2em] text-muted-foreground font-medium">Month ends</p>
          <div className="mt-1 text-2xl sm:text-3xl font-mono font-bold tracking-tight text-primary">
            {formatDDHHMMSS(monthSeconds)}
          </div>
        </div>
      </div>
    </div>
  );
};

const nameInitials = (name) => {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return 'U';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
};

const WeeklyLeaderboardCard = ({ leaderboard, currentUserId }) => {
  const entries = leaderboard?.entries || [];
  const maxScore = Math.max(1, ...entries.map((e) => e.score || 0));
  const resetAt = leaderboard?.reset_at ? new Date(leaderboard.reset_at) : null;
  const resetLabel = resetAt ? resetAt.toUTCString().toUpperCase() : null;

  return (
    <div className="rounded-2xl border border-white/5 glass-card glow-primary p-6" data-testid="weekly-leaderboard-card">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold tracking-wide">Weekly Leaderboard</h3>
        <div className="text-xs text-muted-foreground font-mono">UTC</div>
      </div>

      <div className="mt-4 rounded-xl border border-white/5 overflow-hidden">
        <div className="grid grid-cols-[64px_1fr_80px_140px] gap-3 px-4 py-3 bg-white/[0.03] text-[11px] uppercase tracking-[0.2em] text-muted-foreground">
          <div>Rank</div>
          <div>Name</div>
          <div className="text-right">Score</div>
          <div className="text-right">Progress</div>
        </div>

        <div className="divide-y divide-white/5">
          {entries.map((e) => {
            const isTop = e.rank <= 3;
            const isMe = e.user_id === currentUserId;
            const pct = Math.round(((e.score || 0) / maxScore) * 100);

            return (
              <div
                key={e.user_id}
                className={cn(
                  'grid grid-cols-[64px_1fr_80px_140px] gap-3 px-4 py-3 items-center',
                  isTop && 'bg-primary/5',
                  isMe && 'ring-1 ring-primary/30'
                )}
              >
                <div className={cn('font-mono text-sm', isTop ? 'text-primary' : 'text-muted-foreground')}>
                  {String(e.rank).padStart(2, '0')}
                </div>

                <div className="flex items-center gap-3 min-w-0">
                  <Avatar className={cn('h-8 w-8', isTop && 'ring-1 ring-primary/30')}>
                    {e.avatar_url ? <AvatarImage src={e.avatar_url} alt={e.name} /> : null}
                    <AvatarFallback className="text-xs font-semibold">
                      {nameInitials(e.name)}
                    </AvatarFallback>
                  </Avatar>
                  <div className="min-w-0">
                    <div className={cn('text-sm font-medium truncate', isTop && 'text-primary')}>{e.name}</div>
                    {isMe && <div className="text-[11px] text-muted-foreground">You</div>}
                  </div>
                </div>

                <div className="text-right font-mono text-sm">{e.score}</div>

                <div className="flex items-center justify-end gap-3">
                  <div className="w-24 h-2 rounded-full bg-white/5 overflow-hidden">
                    <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="w-10 text-right text-[11px] text-muted-foreground font-mono">{pct}%</div>
                </div>
              </div>
            );
          })}

          {entries.length === 0 && (
            <div className="px-4 py-10 text-center text-muted-foreground">
              No leaderboard data yet.
            </div>
          )}
        </div>
      </div>

      {resetLabel && (
        <div className="mt-3 text-[11px] uppercase tracking-[0.2em] text-muted-foreground">
          Resets: {resetLabel}
        </div>
      )}
    </div>
  );
};

const DashboardPage = () => {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState(null);
  const [leaderboard, setLeaderboard] = useState(null);
  const [countdown, setCountdown] = useState(null);
  const [habits, setHabits] = useState([]);
  const [weekLogs, setWeekLogs] = useState([]);
  const [serverOffsetMs, setServerOffsetMs] = useState(0);
  const [, setTick] = useState(0);

  useEffect(() => {
    fetchDashboardData();
  }, []);

  useEffect(() => {
    if (!countdown?.day_end || !countdown?.week_end || !countdown?.month_end) return;

    const interval = setInterval(() => {
      setTick((t) => t + 1);
    }, 1000);

    return () => clearInterval(interval);
  }, [countdown?.day_end, countdown?.week_end, countdown?.month_end]);

  const fetchDashboardData = async () => {
    try {
      const [dashboardRes, leaderboardRes, countdownRes, habitsRes] = await Promise.all([
        analyticsApi.getDashboard(),
        leaderboardApi.getWeekly(10, 0),
        leaderboardApi.getCountdown(),
        habitsApi.getAll(),
      ]);

      setData(dashboardRes.data);
      setLeaderboard(leaderboardRes.data);

      const weekStartDate = leaderboardRes.data?.week_start ? new Date(leaderboardRes.data.week_start).toISOString().slice(0, 10) : null;
      const weekEndDate = leaderboardRes.data?.week_end ? new Date(leaderboardRes.data.week_end).toISOString().slice(0, 10) : null;
      if (weekStartDate && weekEndDate) {
        const logsRes = await logsApi.getAll(weekStartDate, weekEndDate);
        setWeekLogs(Array.isArray(logsRes.data) ? logsRes.data : []);
      } else {
        setWeekLogs([]);
      }

      setHabits(Array.isArray(habitsRes.data) ? habitsRes.data : []);

      const serverNowMs = Date.parse(countdownRes.data?.now);
      if (Number.isFinite(serverNowMs)) {
        setServerOffsetMs(serverNowMs - Date.now());
      }
      setCountdown(countdownRes.data);
    } catch (error) {
      console.error('Failed to fetch dashboard data:', error);
    } finally {
      setLoading(false);
    }
  };

  const computeSecondsRemaining = (iso) => {
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return 0;
    const now = Date.now() + serverOffsetMs;
    return Math.max(0, Math.floor((t - now) / 1000));
  };

  const daySeconds = countdown?.day_end ? computeSecondsRemaining(countdown.day_end) : 0;
  const weekSeconds = countdown?.week_end ? computeSecondsRemaining(countdown.week_end) : 0;
  const monthSeconds = countdown?.month_end ? computeSecondsRemaining(countdown.month_end) : 0;

  const weekStartIso = leaderboard?.week_start;
  const weekEndIso = leaderboard?.week_end;
  const weekStartDate = weekStartIso ? new Date(weekStartIso).toISOString().slice(0, 10) : null;
  const weekEndDate = weekEndIso ? new Date(weekEndIso).toISOString().slice(0, 10) : null;

  const completedByHabit = weekLogs.reduce((acc, log) => {
    if (log?.status === 'completed' && log?.habit_id) {
      acc[log.habit_id] = (acc[log.habit_id] || 0) + 1;
    }
    return acc;
  }, {});

  const daysElapsedInWeek = (() => {
    const nowMs = Date.now() + serverOffsetMs;
    const startMs = weekStartIso ? Date.parse(weekStartIso) : NaN;
    if (!Number.isFinite(startMs)) return 0;
    const diffDays = Math.floor((nowMs - startMs) / 86400000) + 1;
    return Math.max(0, Math.min(7, diffDays));
  })();

  const totalPossibleThisWeek = Math.max(1, habits.length * Math.max(1, daysElapsedInWeek));
  const totalCompletedThisWeek = Object.values(completedByHabit).reduce((sum, n) => sum + (n || 0), 0);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Loader2 className="h-8 w-8 animate-spin text-indigo-500" />
      </div>
    );
  }

  const pieData = data?.completion_breakdown ? [
    { name: 'Completed', value: data.completion_breakdown.completed || 0, color: COLORS.success },
    { name: 'Missed', value: data.completion_breakdown.missed || 0, color: COLORS.danger },
    { name: 'Skipped', value: data.completion_breakdown.skipped || 0, color: COLORS.muted }
  ].filter(item => item.value > 0) : [];

  const totalHabits = data?.kpis?.total_habits || 0;
  const dailyCompletionData = data?.daily_completion || [];
  const maxDailyTotal = Math.max(
    totalHabits,
    ...dailyCompletionData.map(d => (typeof d?.total === 'number' ? d.total : 0))
  );
  const yMax = Math.max(1, maxDailyTotal);

  return (
    <div className="space-y-8" data-testid="dashboard-page">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">
          Welcome back, {user?.name?.split(' ')[0]}
        </h1>
        <p className="mt-1 text-muted-foreground">
          Here's your habit tracking overview
        </p>
      </div>

      {/* Countdown strip (Day/Week/Month) */}
      <CountdownStrip
        daySeconds={daySeconds}
        weekSeconds={weekSeconds}
        monthSeconds={monthSeconds}
      />

      {/* KPI Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4" data-testid="kpi-cards">
        <KPICard
          icon={Flame}
          label="Current Streak"
          value={data?.kpis?.current_streak || 0}
          suffix="days"
          color="warning"
        />
        <KPICard
          icon={Target}
          label="Total Habits"
          value={data?.kpis?.total_habits || 0}
          color="primary"
        />
        <KPICard
          icon={TrendingUp}
          label="Weekly Completion"
          value={data?.kpis?.weekly_completion || 0}
          suffix="%"
          color="success"
        />
        <KPICard
          icon={CheckCircle2}
          label="Overall Rate"
          value={data?.kpis?.overall_completion || 0}
          suffix="%"
          color="primary"
        />
      </div>

      {/* Weekly leaderboard */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6" data-testid="leaderboard-grid">
        <div className="rounded-2xl border border-white/5 glass-card glow-primary p-6" data-testid="habit-list-card">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold tracking-wide">Habit List</h3>
            {weekStartDate && weekEndDate && (
              <div className="text-xs text-muted-foreground font-mono">{weekStartDate} → {weekEndDate}</div>
            )}
          </div>

          <div className="mt-4 flex items-center gap-3">
            <Avatar className="h-9 w-9 ring-1 ring-primary/20">
              <AvatarFallback className="text-xs font-semibold">{nameInitials(user?.name)}</AvatarFallback>
            </Avatar>
            <div className="min-w-0">
              <div className="text-sm font-medium truncate">{user?.name}</div>
              <div className="text-xs text-muted-foreground font-mono">
                {totalCompletedThisWeek}/{totalPossibleThisWeek} habits
              </div>
            </div>
          </div>

          <div className="mt-4 space-y-3">
            {habits.map((h) => {
              const done = completedByHabit[h.id] || 0;
              const goal = Math.max(1, Number(h.goal || 7));
              const pct = Math.max(0, Math.min(100, Math.round((done / goal) * 100)));
              return (
                <div key={h.id} className="rounded-xl border border-white/5 bg-white/[0.02] px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="h-3 w-3 rounded-full ring-2 ring-white/10" style={{ backgroundColor: h.color }} />
                      <div className="min-w-0">
                        <div className="text-sm font-medium truncate">{h.name}</div>
                        <div className="text-xs text-muted-foreground font-mono">{done}/{goal} days</div>
                      </div>
                    </div>
                    <div className="text-xs text-muted-foreground font-mono">{pct}%</div>
                  </div>
                  <div className="mt-3 h-2 rounded-full bg-white/5 overflow-hidden">
                    <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}

            {habits.length === 0 && (
              <div className="text-sm text-muted-foreground">No habits yet.</div>
            )}
          </div>
        </div>
        <WeeklyLeaderboardCard leaderboard={leaderboard} currentUserId={user?.id} />
      </div>

      {/* Charts Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Weekly Performance Line Chart */}
        <div className="lg:col-span-2 rounded-xl border border-border bg-card/50 p-6" data-testid="weekly-performance-chart">
          <h3 className="text-lg font-semibold mb-4">Weekly Performance</h3>
          <div className="h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data?.weekly_performance || []}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis 
                  dataKey="day" 
                  stroke="#71717A" 
                  tick={{ fill: '#A1A1AA', fontSize: 12 }}
                />
                <YAxis 
                  stroke="#71717A" 
                  tick={{ fill: '#A1A1AA', fontSize: 12 }}
                  domain={[0, 100]}
                />
                <Tooltip content={<CustomTooltip />} />
                <Line 
                  type="monotone" 
                  dataKey="performance" 
                  name="Performance"
                    unit="%"
                  stroke={COLORS.primary}
                  strokeWidth={3}
                  dot={{ fill: COLORS.primary, strokeWidth: 2, r: 4 }}
                  activeDot={{ r: 6, fill: COLORS.primary }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Completion Breakdown Donut */}
        <div className="rounded-xl border border-border bg-card/50 p-6" data-testid="completion-breakdown-chart">
          <h3 className="text-lg font-semibold mb-4">Completion Breakdown</h3>
          <div className="h-[300px] flex items-center justify-center">
            {pieData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={90}
                    paddingAngle={2}
                    dataKey="value"
                  >
                    {pieData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip content={<CustomTooltip />} />
                  <Legend 
                    verticalAlign="bottom"
                    formatter={(value) => <span className="text-sm text-muted-foreground">{value}</span>}
                  />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="text-center text-muted-foreground">
                <p>No data yet</p>
                <p className="text-sm mt-1">Start tracking habits to see stats</p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Daily Completion Bar Chart */}
      <div className="rounded-xl border border-border bg-card/50 p-6" data-testid="daily-completion-chart">
        <h3 className="text-lg font-semibold mb-4">Daily Completion (This Week)</h3>
        <p className="text-sm text-muted-foreground -mt-2 mb-4">
          Completed habits per day{totalHabits > 0 ? ` (out of ${totalHabits})` : ''}
        </p>
        <div className="h-[300px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={dailyCompletionData} barCategoryGap={18}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
              <XAxis 
                dataKey="day" 
                stroke="#71717A" 
                tick={{ fill: '#A1A1AA', fontSize: 12 }}
              />
              <YAxis 
                stroke="#71717A" 
                tick={{ fill: '#A1A1AA', fontSize: 12 }}
                allowDecimals={false}
                domain={[0, yMax]}
              />
              <Tooltip content={<CustomTooltip />} />
              {totalHabits > 0 && (
                <ReferenceLine
                  y={totalHabits}
                  stroke={COLORS.primary}
                  strokeOpacity={0.35}
                  strokeDasharray="4 4"
                />
              )}
              <Bar 
                dataKey="completed" 
                name="Completed"
                unit=""
                fill={COLORS.primary}
                radius={[4, 4, 0, 0]}
                barSize={28}
                background={{ fill: 'rgba(255,255,255,0.04)' }}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
};

export default DashboardPage;
