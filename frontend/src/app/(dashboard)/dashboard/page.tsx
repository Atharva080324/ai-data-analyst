"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Button } from "@/components/ui/Button"
import { Input } from "@/components/ui/Input"
import {
  UploadCloud, Database, Sparkles, Send, MessageSquare,
  Loader2, CheckCircle2, ScanSearch, Cpu, BrainCircuit,
  BarChart3, PieChart as PieChartIcon, TrendingUp, Lightbulb,
  ArrowRight, FileSpreadsheet, X
} from "lucide-react"
import { useAuthStore } from "@/lib/store"
import { api } from "@/lib/api"
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend
} from "recharts"

// ── Types ─────────────────────────────────────────────────────

interface DatasetOption {
  id: string
  dataset_name: string
  dataset_type: string
  created_at: string
}

interface AnalysisResult {
  query_id: string
  user_query: string
  generated_sql: string | null
  sql_valid: boolean
  execution_time_ms: number | null
  result: { result_row_count: number | null; result_preview: Record<string, unknown>[] | null } | null
  visualizations: { chart_type: string; chart_config: Record<string, unknown> }[]
  insights: { insight_text: string; importance_score: number | null }[]
  recommendations: { recommendation_text: string; confidence_score: number | null }[]
}

interface ChatMessage {
  role: string
  content: string
}

// ── Pipeline Steps Config ─────────────────────────────────────

const PIPELINE_STEPS = [
  { icon: ScanSearch, label: "Scanning data schema…", duration: 1200 },
  { icon: Cpu, label: "Processing columns & types…", duration: 1000 },
  { icon: BrainCircuit, label: "AI Agent generating insights…", duration: 1500 },
  { icon: BarChart3, label: "Building visualizations…", duration: 800 },
]

const CHART_COLORS = ["#8b5cf6", "#06b6d4", "#10b981", "#f59e0b", "#ef4444", "#ec4899", "#6366f1", "#14b8a6"]

// ── Main Component ────────────────────────────────────────────

export default function DashboardPage() {
  const user = useAuthStore((s) => s.user)
  const firstName = user?.name?.split(" ")[0] || "there"

  // ── State ────────────────────────────
  const [summary, setSummary] = useState<{ total_datasets: number; total_sessions: number; total_queries: number } | null>(null)
  const [datasets, setDatasets] = useState<DatasetOption[]>([])
  const [selectedDataset, setSelectedDataset] = useState<string>("")
  const [uploading, setUploading] = useState(false)
  const [uploadSuccess, setUploadSuccess] = useState("")

  const [analysisStep, setAnalysisStep] = useState(-1) // -1 = not started
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null)
  const [analysisError, setAnalysisError] = useState("")

  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = useState("")
  const [chatLoading, setChatLoading] = useState(false)

  const [userQuery, setUserQuery] = useState("")

  const fileInputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const resultsRef = useRef<HTMLDivElement>(null)

  // ── Load dashboard data on mount ────
  useEffect(() => {
    api.get("/datasets/").then((r) => setDatasets(r.data)).catch(() => {})
    api.get("/users/dashboard-summary").then((r) => setSummary(r.data)).catch(() => {})
  }, [])

  // ── Auto-scroll chat ────────────────
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  // ── Upload handler ──────────────────
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setUploadSuccess("")
    const formData = new FormData()
    formData.append("file", file)
    formData.append("dataset_name", file.name.replace(/\.[^/.]+$/, ""))
    try {
      const { data } = await api.post("/datasets/upload", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      setUploadSuccess(`"${file.name}" uploaded!`)
      const refreshed = await api.get("/datasets/")
      setDatasets(refreshed.data)
      // Auto-select the newly uploaded dataset
      if (data.id) setSelectedDataset(data.id)
    } catch {
      setAnalysisError("Upload failed. Please try again.")
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  // ── Run Analysis Pipeline ───────────
  const runAnalysis = useCallback(async (overrideQuery?: string) => {
    const q = (overrideQuery || userQuery).trim()
    
    if (!selectedDataset) {
      setAnalysisError("Please select a dataset first.")
      return
    }

    // Default query if none provided
    const finalQuery = q || "Summarize this dataset and show key trends"
    if (q) setUserQuery(q)
    else setUserQuery(finalQuery)

    setAnalysisResult(null)
    setAnalysisError("")
    setMessages([])
    setSessionId(null)

    // Step-by-step animation
    for (let i = 0; i < PIPELINE_STEPS.length; i++) {
      setAnalysisStep(i)
      await new Promise((r) => setTimeout(r, PIPELINE_STEPS[i].duration))
    }

    try {
      // Create a chat session for follow-up
      const ds = datasets.find((d) => d.id === selectedDataset)
      const { data: session } = await api.post("/chat/sessions", {
        dataset_id: selectedDataset,
        session_name: `Analysis: ${ds?.dataset_name || "Dataset"}`,
      })
      setSessionId(session.id)

      // Run AI analysis
      const { data: result } = await api.post<AnalysisResult>("/ai/analyze", {
        session_id: session.id,
        dataset_id: selectedDataset,
        user_query: finalQuery,
      })

      setAnalysisResult(result)
      setAnalysisStep(PIPELINE_STEPS.length) // complete

      // Seed chat with initial insight
      setMessages([{
        role: "assistant",
        content: result.insights?.[0]?.insight_text || "Analysis complete! Ask me follow-up questions about your data."
      }])

      // Scroll to results
      setTimeout(() => {
        resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
      }, 300)

    } catch (err: unknown) {
      const resp = (err as { response?: { data?: { detail?: string } } })?.response
      setAnalysisError(resp?.data?.detail || "Analysis failed. Please try again.")
      setAnalysisStep(-1)
    }
  }, [selectedDataset, userQuery, datasets])

  // ── Send chat message ───────────────
  const handleChatSend = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!chatInput.trim() || !sessionId) return
    const msg = chatInput.trim()
    setMessages((p) => [...p, { role: "user", content: msg }])
    setChatInput("")
    setChatLoading(true)
    try {
      const { data } = await api.post(`/chat/sessions/${sessionId}/message`, { message: msg })
      setMessages((p) => [...p, { role: "assistant", content: data.ai_message.message_text }])
    } catch {
      setMessages((p) => [...p, { role: "assistant", content: "Sorry, something went wrong." }])
    } finally {
      setChatLoading(false)
    }
  }

  // ── Suggested queries ───────────────
  const suggestedQueries = [
    "Show me the overall trends",
    "What are the top 5 categories?",
    "Compare metrics across segments",
    "Find any anomalies in the data",
  ]

  // ── Render ──────────────────────────

  const hasResults = analysisStep >= PIPELINE_STEPS.length && analysisResult

  return (
    <div className="space-y-10 animate-fade-in pb-20">

      {/* ═══ HEADER ═══ */}
      <div>
        <h1 className="text-4xl font-bold tracking-tight mb-2">
          Welcome back, {firstName} 👋
        </h1>
        <p className="text-foreground/60 text-lg">Here&apos;s your data intelligence overview.</p>
      </div>

      {/* ═══ DASHBOARD STATS ═══ */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="group p-6 rounded-2xl border border-surface-border bg-surface/[0.02] hover:border-brand/30 transition-all duration-300">
          <div className="flex justify-between items-start mb-4">
            <p className="text-foreground/60 font-medium text-sm">Total Datasets</p>
            <div className="w-11 h-11 rounded-xl bg-surface flex items-center justify-center border border-surface-border group-hover:bg-brand/10 transition-colors">
              <Database className="w-5 h-5 text-brand-light" />
            </div>
          </div>
          <h3 className="text-4xl font-bold tracking-tight">{summary?.total_datasets ?? datasets.length}</h3>
        </div>
        <div className="group p-6 rounded-2xl border border-surface-border bg-surface/[0.02] hover:border-brand/30 transition-all duration-300">
          <div className="flex justify-between items-start mb-4">
            <p className="text-foreground/60 font-medium text-sm">Chat Sessions</p>
            <div className="w-11 h-11 rounded-xl bg-surface flex items-center justify-center border border-surface-border group-hover:bg-brand/10 transition-colors">
              <MessageSquare className="w-5 h-5 text-cyan-400" />
            </div>
          </div>
          <h3 className="text-4xl font-bold tracking-tight">{summary?.total_sessions ?? 0}</h3>
        </div>
        <div className="group p-6 rounded-2xl border border-surface-border bg-surface/[0.02] hover:border-brand/30 transition-all duration-300">
          <div className="flex justify-between items-start mb-4">
            <p className="text-foreground/60 font-medium text-sm">AI Queries</p>
            <div className="w-11 h-11 rounded-xl bg-surface flex items-center justify-center border border-surface-border group-hover:bg-brand/10 transition-colors">
              <BarChart3 className="w-5 h-5 text-emerald-400" />
            </div>
          </div>
          <h3 className="text-4xl font-bold tracking-tight">{summary?.total_queries ?? 0}</h3>
        </div>
      </div>

      {/* ═══ DIVIDER ═══ */}
      <div className="relative">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-surface-border/50" />
        </div>
        <div className="relative flex justify-center">
          <span className="bg-background px-4 text-sm text-foreground/40 font-medium">Analysis Workspace</span>
        </div>
      </div>

      {/* ═══ STEP 1: UPLOAD & SELECT ═══ */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* Upload Zone */}
        <Card className="lg:col-span-1 border-dashed border-2 border-surface-border hover:border-brand/40 transition-colors group cursor-pointer"
          onClick={() => fileInputRef.current?.click()}
        >
          <CardContent className="p-8 flex flex-col items-center justify-center text-center min-h-[200px]">
            <input ref={fileInputRef} type="file" accept=".csv,.xlsx,.xls" className="hidden" onChange={handleUpload} />
            {uploading ? (
              <Loader2 className="w-10 h-10 text-brand-light animate-spin mb-4" />
            ) : (
              <div className="w-16 h-16 rounded-2xl bg-brand/10 flex items-center justify-center mb-4 group-hover:bg-brand/20 transition-colors">
                <UploadCloud className="w-8 h-8 text-brand-light" />
              </div>
            )}
            <h3 className="text-lg font-semibold mb-1">{uploading ? "Uploading…" : "Upload Dataset"}</h3>
            <p className="text-sm text-foreground/50">Drop CSV or Excel • Click to browse</p>
            {uploadSuccess && (
              <div className="mt-3 flex items-center gap-2 text-emerald-400 text-sm">
                <CheckCircle2 className="w-4 h-4" /> {uploadSuccess}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Existing Datasets */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="text-lg flex items-center gap-2">
              <Database className="w-5 h-5 text-brand-light" />
              Your Datasets
            </CardTitle>
          </CardHeader>
          <CardContent>
            {datasets.length === 0 ? (
              <p className="text-foreground/40 text-sm py-6 text-center">No datasets yet. Upload one to get started.</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {datasets.map((ds) => (
                  <button
                    key={ds.id}
                    onClick={() => setSelectedDataset(ds.id)}
                    className={`flex items-center gap-3 p-4 rounded-xl border transition-all text-left ${
                      selectedDataset === ds.id
                        ? "border-brand/50 bg-brand/10 shadow-[0_0_20px_rgba(138,43,226,0.15)]"
                        : "border-surface-border bg-surface/[0.02] hover:border-brand/30 hover:bg-surface/[0.05]"
                    }`}
                  >
                    <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
                      selectedDataset === ds.id ? "bg-brand/20" : "bg-surface border border-surface-border"
                    }`}>
                      <FileSpreadsheet className={`w-5 h-5 ${selectedDataset === ds.id ? "text-brand-light" : "text-foreground/50"}`} />
                    </div>
                    <div className="min-w-0">
                      <p className="font-medium text-sm truncate">{ds.dataset_name}</p>
                      <p className="text-xs text-foreground/40">{new Date(ds.created_at).toLocaleDateString()}</p>
                    </div>
                    {selectedDataset === ds.id && (
                      <CheckCircle2 className="w-5 h-5 text-brand-light ml-auto shrink-0" />
                    )}
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ═══ QUERY INPUT ═══ */}
      {selectedDataset && (
        <Card className="border-brand/20 shadow-[0_0_40px_rgba(138,43,226,0.08)] animate-fade-in">
          <CardContent className="p-6">
            <form onSubmit={(e) => { e.preventDefault(); runAnalysis() }} className="flex items-center gap-4">
              <div className="flex-1 relative">
                <Sparkles className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-brand-light/60 pointer-events-none" />
                <Input
                  value={userQuery}
                  onChange={(e) => setUserQuery(e.target.value)}
                  placeholder="Ask anything about your data..."
                  className="h-14 pl-14 pr-6 text-lg rounded-[1.5rem] border-surface-border bg-surface/[0.03] focus:bg-surface/[0.06] focus:border-brand/40 transition-all placeholder:text-foreground/20"
                />
              </div>
              <Button
                type="submit"
                variant="brand"
                className="h-14 px-8 text-base font-semibold rounded-2xl shadow-[0_0_20px_rgba(138,43,226,0.3)]"
                disabled={analysisStep >= 0 && analysisStep < PIPELINE_STEPS.length}
              >
                {analysisStep >= 0 && analysisStep < PIPELINE_STEPS.length ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <>Analyze <ArrowRight className="w-5 h-5 ml-2" /></>
                )}
              </Button>
            </form>

            <div className="flex flex-wrap gap-2 mt-4">
              {suggestedQueries.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => runAnalysis(q)}
                  className="px-4 py-3 rounded-full border border-surface-border text-xs text-foreground/60 hover:border-brand/40 hover:text-brand-light hover:bg-brand/5 transition-all"
                >
                  {q}
                </button>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* ═══ STEP 2: PIPELINE ANIMATION ═══ */}
      {analysisStep >= 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 animate-fade-in">
          {PIPELINE_STEPS.map((step, i) => {
            const isDone = analysisStep > i
            const isActive = analysisStep === i
            const Icon = step.icon
            return (
              <div
                key={i}
                className={`relative p-6 rounded-2xl border backdrop-blur-sm transition-all duration-500 ${
                  isDone
                    ? "border-emerald-500/30 bg-emerald-500/5"
                    : isActive
                    ? "border-brand/50 bg-brand/10 shadow-[0_0_30px_rgba(138,43,226,0.2)]"
                    : "border-surface-border bg-surface/[0.02] opacity-40"
                }`}
              >
                <div className="flex items-center gap-3 mb-3">
                  {isDone ? (
                    <CheckCircle2 className="w-6 h-6 text-emerald-400" />
                  ) : isActive ? (
                    <div className="relative">
                      <Icon className="w-6 h-6 text-brand-light" />
                      <div className="absolute -inset-1 bg-brand/20 rounded-full animate-ping" />
                    </div>
                  ) : (
                    <Icon className="w-6 h-6 text-foreground/30" />
                  )}
                  <span className={`text-xs font-bold uppercase tracking-wider ${
                    isDone ? "text-emerald-400" : isActive ? "text-brand-light" : "text-foreground/30"
                  }`}>
                    Step {i + 1}
                  </span>
                </div>
                <p className={`text-sm font-medium ${
                  isDone ? "text-emerald-300" : isActive ? "text-foreground" : "text-foreground/30"
                }`}>
                  {isDone ? step.label.replace("…", " ✓") : step.label}
                </p>
                {isActive && (
                  <div className="mt-3 h-1 bg-surface-border rounded-full overflow-hidden">
                    <div className="h-full bg-brand-light rounded-full animate-[progress_1s_ease-in-out_infinite]"
                      style={{ width: "60%" }}
                    />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Analysis Error */}
      {analysisError && (
        <div className="flex items-center gap-3 p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm animate-fade-in">
          <X className="w-5 h-5 shrink-0" />
          {analysisError}
        </div>
      )}

      {/* ═══ STEP 3: VISUAL RESULTS ═══ */}
      {hasResults && (
        <div ref={resultsRef} className="space-y-8 animate-fade-in">

          {/* Result Summary Bar */}
          <div className="flex flex-wrap items-center gap-6 p-6 rounded-2xl bg-surface/[0.03] border border-surface-border">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-5 h-5 text-emerald-400" />
              <span className="text-sm font-medium text-emerald-400">Analysis Complete</span>
            </div>
            {analysisResult.execution_time_ms && (
              <div className="text-sm text-foreground/50">
                Executed in <strong className="text-foreground">{analysisResult.execution_time_ms}ms</strong>
              </div>
            )}
            {analysisResult.result?.result_row_count != null && (
              <div className="text-sm text-foreground/50">
                <strong className="text-foreground">{analysisResult.result.result_row_count}</strong> rows returned
              </div>
            )}
          </div>

          {/* Charts Grid */}
          {analysisResult.visualizations.length > 0 ? (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {analysisResult.visualizations.map((viz, i) => (
                <Card key={i} className="border-brand/10 bg-gradient-to-b from-surface/[0.05] to-transparent shadow-glass overflow-hidden">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-lg flex items-center gap-2">
                      {viz.chart_type === "pie" ? (
                        <PieChartIcon className="w-5 h-5 text-brand-light" />
                      ) : viz.chart_type === "area" ? (
                        <TrendingUp className="w-5 h-5 text-brand-light" />
                      ) : (
                        <BarChart3 className="w-5 h-5 text-brand-light" />
                      )}
                      {(viz.chart_config as { title?: string }).title || `Visualization ${i + 1}`}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="h-[350px]">
                    <RenderChart config={viz.chart_config} chartType={viz.chart_type} />
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : analysisResult.result?.result_preview && analysisResult.result.result_preview.length > 0 ? (
            // Fallback: render data as auto-chart
            <Card className="border-brand/10 bg-gradient-to-b from-surface/[0.05] to-transparent shadow-glass">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2">
                  <BarChart3 className="w-5 h-5 text-brand-light" />
                  Data Preview
                </CardTitle>
              </CardHeader>
              <CardContent className="h-[400px]">
                <AutoChart data={analysisResult.result.result_preview} />
              </CardContent>
            </Card>
          ) : null}

          {/* Insights Cards */}
          {analysisResult.insights.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {analysisResult.insights.map((insight, i) => (
                <div key={i} className="p-5 rounded-2xl border border-surface-border bg-surface/[0.02] flex items-start gap-4">
                  <div className="w-10 h-10 rounded-xl bg-amber-500/10 flex items-center justify-center shrink-0">
                    <Lightbulb className="w-5 h-5 text-amber-400" />
                  </div>
                  <div>
                    <p className="text-sm text-foreground/80 leading-relaxed">{insight.insight_text}</p>
                    {insight.importance_score != null && (
                      <p className="text-xs text-foreground/40 mt-2">Importance: {Math.round(insight.importance_score * 100)}%</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* ═══ STEP 4: CHAT + RECOMMENDATIONS ═══ */}
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">

            {/* Left: AI Chat (60%) */}
            <div className="lg:col-span-3 flex flex-col h-[500px] bg-surface/[0.02] border border-surface-border rounded-3xl overflow-hidden shadow-glass">
              <div className="h-14 border-b border-surface-border/50 bg-background/50 backdrop-blur-xl flex items-center px-5 shrink-0">
                <div className="w-7 h-7 rounded-lg bg-brand/10 flex items-center justify-center mr-3">
                  <Sparkles className="w-3.5 h-3.5 text-brand-light" />
                </div>
                <h3 className="font-semibold text-sm">AI Chat — Follow Up</h3>
              </div>

              <div className="flex-1 overflow-y-auto p-5 space-y-4">
                {messages.map((msg, idx) => (
                  <div key={idx} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                    <div className={`flex max-w-[85%] ${msg.role === "user" ? "flex-row-reverse" : "flex-row"} items-end gap-2`}>
                      <div className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 ${
                        msg.role === "user" ? "bg-surface border border-surface-border" : "bg-brand shadow-glow"
                      }`}>
                        {msg.role === "user" ? <MessageSquare className="w-3.5 h-3.5 text-foreground/60" /> : <Sparkles className="w-3.5 h-3.5 text-white" />}
                      </div>
                      <div className={`p-3 rounded-2xl text-sm leading-relaxed ${
                        msg.role === "user"
                          ? "bg-surface text-foreground rounded-br-none border border-surface-border"
                          : "bg-brand/10 text-foreground border border-brand/20 rounded-bl-none"
                      }`}>
                        <pre className="whitespace-pre-wrap font-sans">{msg.content}</pre>
                      </div>
                    </div>
                  </div>
                ))}
                {chatLoading && (
                  <div className="flex justify-start">
                    <div className="flex items-center gap-2">
                      <div className="w-7 h-7 rounded-full bg-brand flex items-center justify-center shadow-glow">
                        <Sparkles className="w-3.5 h-3.5 text-white" />
                      </div>
                      <div className="p-3 rounded-2xl bg-brand/10 border border-brand/20 rounded-bl-none flex items-center space-x-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-brand-light animate-bounce" style={{ animationDelay: "0ms" }} />
                        <span className="w-1.5 h-1.5 rounded-full bg-brand-light animate-bounce" style={{ animationDelay: "150ms" }} />
                        <span className="w-1.5 h-1.5 rounded-full bg-brand-light animate-bounce" style={{ animationDelay: "300ms" }} />
                      </div>
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              <div className="p-4 bg-background/50 backdrop-blur-xl border-t border-surface-border/50 shrink-0">
                <form onSubmit={handleChatSend} className="relative flex items-center">
                  <Input
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    placeholder="Ask a follow-up question…"
                    disabled={!sessionId || chatLoading}
                    className="h-12 pl-5 pr-12 text-sm rounded-2xl border-surface-border bg-surface/[0.05]"
                  />
                  <Button
                    type="submit"
                    size="icon"
                    variant="brand"
                    className="absolute right-1.5 h-9 w-9 rounded-xl"
                    disabled={!chatInput.trim() || chatLoading || !sessionId}
                  >
                    {chatLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                  </Button>
                </form>
              </div>
            </div>

            {/* Right: Recommendations (40%) */}
            <div className="lg:col-span-2 space-y-4">
              {/* AI Recommendations */}
              {analysisResult.recommendations.length > 0 && (
                <Card className="bg-gradient-to-br from-brand/10 to-transparent border-brand/20">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2 text-brand-light">
                      <Lightbulb className="w-5 h-5" />
                      Recommendations
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {analysisResult.recommendations.map((rec, i) => (
                      <div key={i} className="flex items-start gap-3">
                        <div className="w-7 h-7 rounded-lg bg-emerald-500/20 text-emerald-400 flex items-center justify-center shrink-0 text-xs font-bold">
                          {i + 1}
                        </div>
                        <p className="text-sm text-foreground/80 leading-relaxed">{rec.recommendation_text}</p>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              )}

              {/* Suggested Follow-up Queries */}
              <Card className="border-surface-border">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">Suggested Queries</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {[
                    "Break down results by category",
                    "What are the outliers?",
                    "Compare this with last period",
                    "Summarize the key takeaways",
                  ].map((q) => (
                    <button
                      key={q}
                      onClick={() => { setChatInput(q) }}
                      className="w-full text-left p-3 rounded-xl border border-surface-border hover:border-brand/40 hover:bg-brand/5 transition-all group flex items-center justify-between text-sm"
                    >
                      <span className="text-foreground/70 group-hover:text-white transition-colors">{q}</span>
                      <Send className="w-3.5 h-3.5 text-foreground/30 group-hover:text-brand-light" />
                    </button>
                  ))}
                </CardContent>
              </Card>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


// ── Chart Renderers ───────────────────────────────────────────

function RenderChart({ config, chartType }: { config: Record<string, unknown>; chartType: string }) {
  const data = (config.data as Record<string, unknown>[]) || []
  const xKey = (config.x_axis as string) || (config.xKey as string) || Object.keys(data[0] || {})[0] || "name"
  const yKey = (config.y_axis as string) || (config.yKey as string) || Object.keys(data[0] || {}).find((k) => k !== xKey) || "value"

  if (data.length === 0) {
    return <p className="text-foreground/40 text-sm text-center pt-20">No data to visualize</p>
  }

  if (chartType === "pie") {
    return (
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie data={data} dataKey={yKey} nameKey={xKey} cx="50%" cy="50%" outerRadius="80%" label>
            {data.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
          </Pie>
          <Tooltip contentStyle={{ backgroundColor: "#0a0a0a", border: "1px solid #333", borderRadius: "12px" }} />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    )
  }

  if (chartType === "area") {
    return (
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data}>
          <defs>
            <linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" vertical={false} />
          <XAxis dataKey={xKey} stroke="#ffffff40" tick={{ fill: "#ffffff60" }} axisLine={false} tickLine={false} />
          <YAxis stroke="#ffffff40" tick={{ fill: "#ffffff60" }} axisLine={false} tickLine={false} />
          <Tooltip contentStyle={{ backgroundColor: "#0a0a0a", border: "1px solid #333", borderRadius: "12px" }} />
          <Area type="monotone" dataKey={yKey} stroke="#8b5cf6" strokeWidth={3} fillOpacity={1} fill="url(#grad)" />
        </AreaChart>
      </ResponsiveContainer>
    )
  }

  // Default: bar chart
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" vertical={false} />
        <XAxis dataKey={xKey} stroke="#ffffff40" tick={{ fill: "#ffffff60" }} axisLine={false} tickLine={false} />
        <YAxis stroke="#ffffff40" tick={{ fill: "#ffffff60" }} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={{ backgroundColor: "#0a0a0a", border: "1px solid #333", borderRadius: "12px" }} />
        <Bar dataKey={yKey} fill="#8b5cf6" radius={[6, 6, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function AutoChart({ data }: { data: Record<string, unknown>[] }) {
  if (!data.length) return null
  const keys = Object.keys(data[0])
  const xKey = keys[0]
  const yKey = keys.find((k) => typeof data[0][k] === "number") || keys[1]

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" vertical={false} />
        <XAxis dataKey={xKey} stroke="#ffffff40" tick={{ fill: "#ffffff60" }} axisLine={false} tickLine={false} />
        <YAxis stroke="#ffffff40" tick={{ fill: "#ffffff60" }} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={{ backgroundColor: "#0a0a0a", border: "1px solid #333", borderRadius: "12px" }} />
        <Bar dataKey={yKey!} fill="#8b5cf6" radius={[6, 6, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}
