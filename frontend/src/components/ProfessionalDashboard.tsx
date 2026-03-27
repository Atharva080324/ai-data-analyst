import React, { useId } from "react"
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis
} from "recharts"
import { TrendingUp, Activity, Lightbulb, BarChart3, PieChart as PieChartIcon, LayoutGrid, Zap } from "lucide-react"

const CHART_COLORS = ["#D4A853", "#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#ec4899", "#F0C97B", "#8b5cf6"]

// ── Chart Styles Constants (Fix for infinite render loop) ────
const CHART_TICK_STYLE = { fill: "#ffffff60", fontSize: 11 }
const CHART_MARGINS = { top: 10, right: 10, left: -20, bottom: 0 }
const CHART_MARGINS_HORIZONTAL = { top: 0, right: 10, left: 0, bottom: 0 }
const CHART_YAXIS_TICK_RANK = { fill: "#ffffff90", fontSize: 11, fontWeight: 500 }

// ── Types ─────────────────────────────────────────────────────

interface AnalysisData {
  result_preview?: Record<string, unknown>[] | null
  chart_config?: { type: string; data: Record<string, unknown> | Record<string, unknown>[] | null; options?: Record<string, unknown> } | null
  insights?: { text?: string; insight_text?: string; score?: number; importance_score?: number | null }[]
  recommendations?: { text?: string; recommendation_text?: string; score?: number; confidence_score?: number | null }[]
  row_count?: number
  execution_time_ms?: number | null
  confidence_score?: number | null
  generated_sql?: string | null
  user_query?: string
}

interface DerivedStat {
  label: string
  value: string
  detail: string
  icon: React.ElementType
  color: string
  bg: string
}

// ── Helpers ───────────────────────────────────────────────────

function deriveStats(data: Record<string, unknown>[]): DerivedStat[] {
  if (!data.length) return []

  const keys = Object.keys(data[0])
  const numericKeys = keys.filter(k => typeof data[0][k] === "number")
  const stats: DerivedStat[] = []

  stats.push({
    label: "Total Rows",
    value: data.length.toLocaleString(),
    detail: `${keys.length} columns`,
    icon: BarChart3,
    color: "text-brand-light",
    bg: "bg-brand/10",
  })

  for (const k of numericKeys.slice(0, 3)) {
    const values = data.map(r => Number(r[k]) || 0)
    const sum = values.reduce((a, b) => a + b, 0)
    const avg = sum / values.length
    const max = Math.max(...values)
    const fmt = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : n.toFixed(1)

    stats.push({
      label: k.replace(/_/g, " "),
      value: fmt(sum),
      detail: `avg ${fmt(avg)} · max ${fmt(max)}`,
      icon: TrendingUp,
      color: "text-emerald-400",
      bg: "bg-emerald-500/10",
    })
  }

  return stats.slice(0, 4)
}

function normalizeChartData(config: { data?: unknown; labels?: string[]; datasets?: { label?: string; data: number[] }[] }): { data: Record<string, unknown>[]; xKey: string; yKeys: string[] } {
  if (Array.isArray(config?.data)) {
    const d = config.data as Record<string, unknown>[]
    if (!d.length) return { data: [], xKey: "name", yKeys: [] }
    const keys = Object.keys(d[0])
    const xKey = keys[0]
    const yKeys = keys.filter(k => k !== xKey && typeof d[0][k] === "number")
    return { data: d, xKey, yKeys }
  }

  const raw = config?.data as Record<string, unknown> | null
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  if (raw && typeof raw === "object" && Array.isArray((raw as any).labels)) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const labels = (raw as any).labels as string[]
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const datasets = ((raw as any).datasets || []) as { label?: string; data: number[] }[]
    if (!labels.length || !datasets.length) return { data: [], xKey: "name", yKeys: [] }

    const yKeys = datasets.map((ds, i) => ds.label || `value_${i}`)
    const data = labels.map((label, i) => {
      const row: Record<string, unknown> = { name: label }
      datasets.forEach((ds, di) => { row[yKeys[di]] = ds.data?.[i] ?? 0 })
      return row
    })
    return { data, xKey: "name", yKeys }
  }

  return { data: [], xKey: "name", yKeys: [] }
}

function autoChartFromPreview(preview: Record<string, unknown>[]): { data: Record<string, unknown>[]; xKey: string; yKeys: string[]; type: string } | null {
  if (!preview.length) return null
  const keys = Object.keys(preview[0])
  const strKeys = keys.filter(k => typeof preview[0][k] === "string")
  const numKeys = keys.filter(k => typeof preview[0][k] === "number")

  if (!numKeys.length) return null

  const xKey = strKeys[0] || keys[0]
  const yKeys = numKeys.slice(0, 2)
  const uniqueX = new Set(preview.map(r => String(r[xKey]))).size
  const type = uniqueX <= 6 && uniqueX >= 2 ? "pie" : "area"

  return { data: preview.slice(0, 50) as Record<string, unknown>[], xKey, yKeys, type }
}

const CustomTooltip = ({ active, payload, label }: { active?: boolean; payload?: { name: string; value: unknown; color: string }[]; label?: string }) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-[#06060A]/95 border border-brand/20 p-3 rounded-xl shadow-xl backdrop-blur-md">
        <p className="text-foreground/80 font-medium text-sm mb-2">{label}</p>
        {payload.map((entry, index) => (
          <div key={index} className="flex items-center justify-between gap-4 text-xs font-medium">
            <span style={{ color: entry.color }}>{entry.name}</span>
            <span className="text-foreground">{typeof entry.value === "number" ? entry.value.toLocaleString() : String(entry.value ?? "")}</span>
          </div>
        ))}
      </div>
    );
  }
  return null;
};

// ── Main Component ────────────────────────────────────────────

export function ProfessionalDashboard({ 
  analysis, 
  isCompact = false 
}: { 
  analysis: AnalysisData,
  isCompact?: boolean 
}) {
  const instanceId = useId().replace(/:/g, "-")
  const preview = analysis.result_preview || []
  const stats = deriveStats(preview as Record<string, unknown>[])
  const insights = (analysis.insights || []).map(i => i.text || i.insight_text || "")
  const recommendations = (analysis.recommendations || []).map(r => r.text || r.recommendation_text || "")

  // 1️⃣ Determine primary chart (Area/Wave)
  let chartType = analysis.chart_config?.type || "area"
  if (chartType === "bar" && analysis.chart_config?.data) chartType = "area"
  let chartInfo = analysis.chart_config ? normalizeChartData(analysis.chart_config) : null

  if ((!chartInfo || !chartInfo.data.length) && preview.length) {
    const auto = autoChartFromPreview(preview as Record<string, unknown>[])
    if (auto) {
      chartInfo = { data: auto.data, xKey: auto.xKey, yKeys: auto.yKeys }
      chartType = auto.type
    }
  }

  // 2️⃣ Build secondary chart (Donut)
  let donutChart: { data: Record<string, unknown>[]; xKey: string; yKeys: string[]; type: string } | null = null
  if (chartInfo && chartInfo.data.length && chartType !== "pie") {
    const topSlice = chartInfo.data.slice(0, 6)
    if (topSlice.length >= 2 && chartInfo.yKeys.length > 0) {
      donutChart = { data: topSlice, xKey: chartInfo.xKey, yKeys: [chartInfo.yKeys[0]], type: "pie" }
    }
  } else if (chartInfo && chartInfo.data.length && chartType === "pie") {
    donutChart = { data: chartInfo.data, xKey: chartInfo.xKey, yKeys: chartInfo.yKeys, type: "area" }
  }

  // 3️⃣ Build tertiary chart (Horizontal Bar Ranking)
  let barRankChart: { data: Record<string, unknown>[]; xKey: string; yKeys: string[] } | null = null
  if (chartInfo && chartInfo.data.length >= 3 && chartInfo.yKeys.length > 0) {
    const targetYKey = chartInfo.yKeys.length > 1 ? chartInfo.yKeys[1] : chartInfo.yKeys[0]
    let validData = chartInfo.data.filter(d => Boolean(d[chartInfo!.xKey]) && typeof d[targetYKey] === 'number')
    validData = validData.sort((a, b) => Number(a[targetYKey] || 0) - Number(b[targetYKey] || 0)).slice(-5)
    if (validData.length >= 3) {
      barRankChart = { data: validData, xKey: chartInfo.xKey, yKeys: [targetYKey] }
    }
  }

  // 4️⃣ Build quaternary chart (Radar Comparison)
  let radarChart: { data: Record<string, unknown>[]; xKey: string; yKeys: string[] } | null = null
  if (chartInfo && chartInfo.data.length >= 3 && chartInfo.yKeys.length >= 1) {
    const radarData = chartInfo.data.slice(0, 5)
    const activeYKeys = chartInfo.yKeys.slice(0, 3)
    radarChart = { data: radarData, xKey: chartInfo.xKey, yKeys: activeYKeys }
  }

  return (
    <div className={`flex flex-col animate-fade-in ${isCompact ? "gap-2 pb-2" : "gap-6 pb-10"}`}>

      {/* 🟢 Top Banner Stats */}
      {stats.length > 0 && !isCompact && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {stats.map((stat, i) => (
            <div key={i} className="p-5 rounded-2xl bg-surface/[0.02] border border-surface-border hover:border-brand/30 transition-all flex items-center justify-between group">
              <div>
                <p className="text-foreground/50 text-xs font-semibold uppercase tracking-wider mb-1">{stat.label}</p>
                <h4 className="text-2xl font-bold tracking-tight">{stat.value}</h4>
                <p className="text-xs mt-1 font-medium text-foreground/40">{stat.detail}</p>
              </div>
              <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${stat.bg} group-hover:scale-110 transition-transform`}>
                <stat.icon className={`w-6 h-6 ${stat.color}`} />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 🟢 ROW 1: Main Analytics (Wave + Donut) */}
      <div className={isCompact ? "flex flex-col gap-2" : "grid grid-cols-1 lg:grid-cols-3 gap-6"}>

        {/* Chart 1: Main Trend / Visualization (Span 2) */}
        {chartInfo && chartInfo.data.length > 0 && (
          <div className={`${isCompact ? "w-full" : "lg:col-span-2"} ${isCompact ? "p-3" : "p-6"} rounded-2xl bg-surface/[0.02] border border-surface-border flex flex-col`}>
            <div className={`flex justify-between items-center ${isCompact ? "mb-2" : "mb-6"}`}>
              <div>
                <h3 className={`${isCompact ? "text-xs" : "text-lg"} font-display font-bold`}>Data Visualization</h3>
                {!isCompact && <p className="text-xs text-foreground/50">{analysis.user_query || "Primary trends and analytics"}</p>}
              </div>
              <div className="flex items-center gap-2">
                {chartType === "pie" ? <PieChartIcon className={`${isCompact ? "w-3 h-3" : "w-4 h-4"} text-brand-light`} /> : <BarChart3 className={`${isCompact ? "w-3 h-3" : "w-4 h-4"} text-brand-light`} />}
              </div>
            </div>
            <div className={isCompact ? "h-[200px]" : "h-[300px]"}>
              {chartType === "pie" ? (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={chartInfo.data} dataKey={chartInfo.yKeys[0]} nameKey={chartInfo.xKey} cx="50%" cy="50%" innerRadius="55%" outerRadius="85%" paddingAngle={4} stroke="none" isAnimationActive={false}>
                      {chartInfo.data.map((_, index) => <Cell key={`cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />)}
                    </Pie>
                    <Tooltip content={<CustomTooltip />} />
                  </PieChart>
                </ResponsiveContainer>
              ) : chartType === "area" || chartType === "line" ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartInfo.data} margin={CHART_MARGINS}>
                    <defs>
                      {chartInfo.yKeys.map((yk, i) => (
                        <linearGradient key={yk} id={`${instanceId}-grad-${i}`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={CHART_COLORS[i % CHART_COLORS.length]} stopOpacity={0.3}/>
                          <stop offset="95%" stopColor={CHART_COLORS[i % CHART_COLORS.length]} stopOpacity={0}/>
                        </linearGradient>
                      ))}
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(212,168,83,0.08)" vertical={false} />
                    <XAxis dataKey={chartInfo.xKey} stroke="#ffffff40" tick={CHART_TICK_STYLE} axisLine={false} tickLine={false} />
                    <YAxis stroke="#ffffff40" tick={CHART_TICK_STYLE} axisLine={false} tickLine={false} />
                    <Tooltip content={<CustomTooltip />} />
                    {chartInfo.yKeys.map((yk, i) => (
                      <Area key={yk} type="monotone" dataKey={yk} stroke={CHART_COLORS[i % CHART_COLORS.length]} strokeWidth={3} fillOpacity={1} fill={`url(#${instanceId}-grad-${i})`} name={yk.replace(/_/g, " ")} isAnimationActive={false} />
                    ))}
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartInfo.data} margin={CHART_MARGINS}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(212,168,83,0.08)" vertical={false} />
                    <XAxis dataKey={chartInfo.xKey} stroke="#ffffff40" tick={CHART_TICK_STYLE} axisLine={false} tickLine={false} />
                    <YAxis stroke="#ffffff40" tick={CHART_TICK_STYLE} axisLine={false} tickLine={false} />
                    <Tooltip content={<CustomTooltip />} cursor={{fill: 'rgba(212,168,83,0.05)'}} />
                    {chartInfo.yKeys.map((yk, i) => (
                      <Bar key={yk} dataKey={yk} fill={CHART_COLORS[i % CHART_COLORS.length]} radius={[6, 6, 0, 0]} name={yk.replace(/_/g, " ")} isAnimationActive={false} />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        )}

        {/* Compact fallback: data table when no chart (e.g. text/list queries) */}
        {isCompact && (!chartInfo || chartInfo.data.length === 0) && preview.length > 0 && (
          <div className="w-full p-3 rounded-2xl bg-surface/[0.02] border border-surface-border">
            <p className="text-[10px] font-bold uppercase tracking-wider text-foreground/40 mb-2">Results</p>
            <div className="overflow-x-auto max-h-[200px] overflow-y-auto scrollbar-thin">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-[#06060A]">
                  <tr className="border-b border-surface-border">
                    {Object.keys(preview[0]).slice(0, 6).map(k => (
                      <th key={k} className="text-left py-2 px-2 text-[10px] font-bold uppercase tracking-wider text-foreground/40 whitespace-nowrap">{k.replace(/_/g, " ")}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(preview as Record<string, unknown>[]).slice(0, 15).map((row, i) => (
                    <tr key={i} className="border-b border-surface-border/30 hover:bg-brand/5 transition-colors">
                      {Object.keys(preview[0]).slice(0, 6).map(k => (
                        <td key={k} className="py-1.5 px-2 text-foreground/70 whitespace-nowrap">
                          {String(row[k] ?? "—")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {preview.length > 15 && (
                <p className="text-center text-[10px] text-foreground/30 py-1">+{preview.length - 15} more rows</p>
              )}
            </div>
          </div>
        )}

        {/* Chart 2: Distribution Donut (Span 1) */}
        {donutChart && donutChart.data.length > 0 && !isCompact && (
          <div className="p-6 rounded-2xl bg-surface/[0.02] border border-surface-border flex flex-col overflow-hidden">
            <div className="flex justify-between items-center mb-4">
              <div>
                <h3 className="text-lg font-display font-bold">Distribution</h3>
                <p className="text-xs text-foreground/50">Market share representation</p>
              </div>
            </div>
            <div className="h-[200px] relative">
              {donutChart.type === "pie" ? (
                <>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={donutChart.data} dataKey={donutChart.yKeys[0]} nameKey={donutChart.xKey} cx="50%" cy="50%" innerRadius="60%" outerRadius="82%" paddingAngle={5} stroke="none" isAnimationActive={false}>
                        {donutChart.data.map((_, index) => <Cell key={`cell-s-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />)}
                      </Pie>
                      <Tooltip content={<CustomTooltip />} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="flex flex-wrap gap-x-3 gap-y-1.5 justify-center mt-2 max-h-[48px] overflow-hidden">
                    {donutChart.data.slice(0, 6).map((entry, i) => {
                      const label = String(entry[donutChart!.xKey])
                      return (
                        <div key={i} className="flex items-center gap-1.5 text-[10px] font-medium text-foreground/60">
                          <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: CHART_COLORS[i % CHART_COLORS.length] }} />
                          <span className="truncate max-w-[80px]" title={label}>{label}</span>
                        </div>
                      )
                    })}
                  </div>
                </>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={donutChart.data} margin={CHART_MARGINS}>
                    <defs>
                      {donutChart.yKeys.map((yk, i) => (
                        <linearGradient key={yk} id={`${instanceId}-grad-sec-${i}`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={CHART_COLORS[i % CHART_COLORS.length]} stopOpacity={0.3}/>
                          <stop offset="95%" stopColor={CHART_COLORS[i % CHART_COLORS.length]} stopOpacity={0}/>
                        </linearGradient>
                      ))}
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(212,168,83,0.08)" vertical={false} />
                    <XAxis dataKey={donutChart.xKey} stroke="#ffffff40" tick={CHART_TICK_STYLE} axisLine={false} tickLine={false} />
                    <YAxis stroke="#ffffff40" tick={CHART_TICK_STYLE} axisLine={false} tickLine={false} />
                    <Tooltip content={<CustomTooltip />} />
                    {donutChart.yKeys.map((yk, i) => (
                      <Area key={yk} type="monotone" dataKey={yk} stroke={CHART_COLORS[i % CHART_COLORS.length]} strokeWidth={3} fillOpacity={1} fill={`url(#${instanceId}-grad-sec-${i})`} name={yk.replace(/_/g, " ")} isAnimationActive={false} />
                    ))}
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 🟢 ROW 2: Deep Dive (Bar + Radar + Live Feed) */}
      {!isCompact && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          
          {/* Chart 3: Horizontal Ranking */}
          {barRankChart && barRankChart.data.length > 0 && (
            <div className="p-6 rounded-2xl bg-surface/[0.02] border border-surface-border flex flex-col">
              <div className="flex justify-between items-center mb-6">
                <div>
                  <h3 className="text-base font-display font-bold flex items-center gap-2">
                    <LayoutGrid className="w-4 h-4 text-brand-light" /> Segment Ranking
                  </h3>
                  <p className="text-[11px] text-foreground/50 mt-1 uppercase tracking-wider">{barRankChart.yKeys[0].replace(/_/g, " ")} TOP 5</p>
                </div>
              </div>
              <div className="h-[220px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={barRankChart.data} layout="vertical" margin={CHART_MARGINS_HORIZONTAL}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(212,168,83,0.04)" horizontal={true} vertical={false} />
                    <XAxis type="number" stroke="#ffffff20" tick={CHART_TICK_STYLE} axisLine={false} tickLine={false} hide />
                    <YAxis dataKey={barRankChart.xKey} type="category" stroke="#ffffff40" tick={CHART_YAXIS_TICK_RANK} axisLine={false} tickLine={false} width={80} />
                    <Tooltip content={<CustomTooltip />} cursor={{fill: 'rgba(212,168,83,0.05)'}} />
                    <Bar dataKey={barRankChart.yKeys[0]} radius={[0, 4, 4, 0]} barSize={16} isAnimationActive={false}>
                      {barRankChart.data.map((_, i) => <Cell key={`h-bar-${i}`} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Chart 4: Radar / Spider Comparison */}
          {radarChart && (
            <div className="p-6 rounded-2xl bg-surface/[0.02] border border-surface-border flex flex-col">
              <div className="flex justify-between items-center mb-6">
                <div>
                  <h3 className="text-base font-display font-bold flex items-center gap-2">
                    <Zap className="w-4 h-4 text-emerald-400" /> Multi-metric Analysis
                  </h3>
                  <p className="text-[11px] text-foreground/50 mt-1 uppercase tracking-wider">Top Entities Comparison</p>
                </div>
              </div>
              <div className="h-[220px]">
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart cx="50%" cy="50%" outerRadius="65%" data={radarChart.data}>
                    <PolarGrid stroke="#ffffff20" />
                    <PolarAngleAxis dataKey={radarChart.xKey} tick={{ fill: "#ffffff70", fontSize: 10, fontWeight: 500 }} />
                    <PolarRadiusAxis angle={30} domain={[0, 'auto']} tick={false} axisLine={false} />
                    <Tooltip content={<CustomTooltip />} />
                    {radarChart.yKeys.map((yk, i) => (
                      <Radar key={yk} name={yk.replace(/_/g, " ")} dataKey={yk} stroke={CHART_COLORS[i % CHART_COLORS.length]} fill={CHART_COLORS[i % CHART_COLORS.length]} fillOpacity={0.3} strokeWidth={2} isAnimationActive={false} />
                    ))}
                  </RadarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* AI Insights */}
          {insights.length > 0 ? (
            <div className={`p-6 rounded-2xl bg-surface/[0.02] border border-surface-border flex flex-col border-t-2 border-t-brand/50 overflow-hidden min-h-0 ${(!barRankChart || !radarChart) ? "lg:col-span-3" : ""}`}>
              <div className="flex justify-between items-center mb-6">
                <h3 className="text-base font-display font-bold flex items-center gap-2">
                  <Lightbulb className="w-4 h-4 text-brand-light" /> Insights
                </h3>
                <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20">
                  <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)] animate-pulse" />
                  <span className="text-[10px] uppercase font-bold text-emerald-400 tracking-wider">Live</span>
                </div>
              </div>
              <div className="flex flex-col gap-4 overflow-y-auto pr-2 max-h-[220px] scrollbar-thin">
                {insights.filter(Boolean).map((text, i) => (
                  <div key={i} className="flex items-start gap-3 group">
                    <div className={`mt-1.5 w-2 h-2 rounded-full shrink-0 shadow-[0_0_8px_rgba(212,168,83,0.4)] ${
                      i === 0 ? 'bg-brand-light' : i === 1 ? 'bg-emerald-400' : 'bg-blue-400'
                    }`} />
                    <p className="text-sm text-foreground/70 leading-relaxed group-hover:text-foreground/90 transition-colors">{text}</p>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}

      {/* 🟢 ROW 3: Recommendations + Data Table */}
      {!isCompact && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-4">
            {/* Recommendations */}
            {recommendations.length > 0 && (
              <div className="p-6 rounded-2xl bg-surface/[0.02] border border-surface-border lg:col-span-2">
                <h3 className="text-base font-display font-bold flex items-center gap-2 mb-5">
                  <Activity className="w-4 h-4 text-emerald-400" /> Strategic Recommendations
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                  {recommendations.filter(Boolean).map((text, i) => (
                    <div key={i} className="flex items-start gap-4 p-4 rounded-xl bg-surface/10 border border-surface-border/50 hover:border-brand/30 transition-all">
                      <span className="mt-0.5 text-xs font-bold text-[#06060A] bg-brand rounded-md min-w-[24px] h-6 flex items-center justify-center shadow-[0_0_15px_rgba(212,168,83,0.3)]">
                        {i + 1}
                      </span>
                      <p className="text-sm text-foreground/80 leading-relaxed font-medium">{text}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* 🟢 Data Table Preview */}
          {preview.length > 0 && (
            <div className="p-6 rounded-2xl bg-surface/[0.02] border border-surface-border overflow-hidden mt-6">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-base font-display font-bold">Data Preview</h3>
                <span className="text-xs text-foreground/40 font-medium">{preview.length} rows preview</span>
              </div>
              <div className="overflow-x-auto max-h-[320px] overflow-y-auto scrollbar-thin">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-[#06060A] z-10 box-shadow-bottom relative">
                    <tr className="border-b border-surface-border">
                      {Object.keys(preview[0]).slice(0, 8).map(key => (
                        <th key={key} className="text-left py-4 px-4 text-xs font-bold uppercase tracking-wider text-foreground/50 whitespace-nowrap bg-[#06060A]">
                          {key.replace(/_/g, " ")}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(preview as Record<string, unknown>[]).slice(0, 20).map((row, i) => (
                      <tr key={i} className="border-b border-surface-border/50 hover:bg-brand/5 transition-colors">
                        {Object.keys(preview[0]).slice(0, 8).map(key => (
                          <td key={key} className="py-3 px-4 text-foreground/70 whitespace-nowrap font-medium">
                            {typeof row[key] === "number" ? Number(row[key]).toLocaleString() : String(row[key] ?? "—")}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
