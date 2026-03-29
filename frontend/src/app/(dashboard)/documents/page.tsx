"use client"
/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable @typescript-eslint/no-unused-vars */

import { useState, useEffect, useRef } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Button } from "@/components/ui/Button"
import { Input } from "@/components/ui/Input"
import {
  UploadCloud, FileText, Loader2, CheckCircle2, Trash2,
  Send, BrainCircuit, AlertCircle, BookOpen, ChevronRight,
  Clock, Hash
} from "lucide-react"
import {
  uploadDocument, listDocuments, askDocument, deleteDocument,
  DocumentSummary, DocumentAskResponse,
} from "@/lib/api"

// ── Types ─────────────────────────────────────────────────────

interface QAHistory {
  question: string
  answer: string
  confidence_score: number | null
  retrieved_pages: DocumentAskResponse["retrieved_pages"]
}

// ── Helpers ───────────────────────────────────────────────────

function ConfidenceBadge({ score }: { score: number | null }) {
  if (score == null) return null
  const pct = Math.round(score * 100)
  const color = pct >= 80 ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/20"
              : pct >= 50 ? "text-amber-400 bg-amber-500/10 border-amber-500/20"
              : "text-red-400 bg-red-500/10 border-red-500/20"
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${color}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {pct}% confidence
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    ready:      "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
    processing: "text-amber-400 bg-amber-500/10 border-amber-500/20",
    failed:     "text-red-400 bg-red-500/10 border-red-500/20",
  }
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wider border ${map[status] ?? "text-foreground/40 border-surface-border"}`}>
      {status}
    </span>
  )
}

// ── Main Page ─────────────────────────────────────────────────

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [selectedDoc, setSelectedDoc] = useState<DocumentSummary | null>(null)

  // Upload
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState("")
  const [uploadSuccess, setUploadSuccess] = useState("")
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Q&A
  const [question, setQuestion] = useState("")
  const [asking, setAsking] = useState(false)
  const [qaError, setQaError] = useState("")
  const [history, setHistory] = useState<QAHistory[]>([])

  // Delete
  const [deletingId, setDeletingId] = useState<string | null>(null)

  // Load documents on mount
  useEffect(() => {
    listDocuments().then(setDocuments).catch(() => {})
  }, [])

  // Reset Q&A when doc changes
  useEffect(() => {
    setHistory([])
    setQaError("")
    setQuestion("")
  }, [selectedDoc?.id])

  // ── Upload ────────────────────────────────────────────

  const handleFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setUploadError("Only PDF files are supported.")
      return
    }
    setUploading(true)
    setUploadError("")
    setUploadSuccess("")
    try {
      const docName = file.name.replace(/\.pdf$/i, "")
      await uploadDocument(docName, file)
      setUploadSuccess(`"${docName}" processed and ready!`)
      const refreshed = await listDocuments()
      setDocuments(refreshed)
      // Auto-select the newest document
      if (refreshed.length > 0) setSelectedDoc(refreshed[0])
    } catch (err: any) {
      setUploadError(err?.response?.data?.detail || "Upload failed. Please try again.")
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) handleFile(f)
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files?.[0]
    if (f) handleFile(f)
  }

  // ── Ask ───────────────────────────────────────────────

  const handleAsk = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedDoc || !question.trim() || asking) return
    const q = question.trim()
    setAsking(true)
    setQaError("")
    setQuestion("")
    try {
      const res = await askDocument(selectedDoc.id, q)
      setHistory(prev => [{ question: q, answer: res.answer, confidence_score: res.confidence_score, retrieved_pages: res.retrieved_pages }, ...prev])
    } catch (err: any) {
      setQaError(err?.response?.data?.detail || "Failed to get an answer. Please try again.")
    } finally {
      setAsking(false)
    }
  }

  // ── Delete ─────────────────────────────────────────────

  const handleDelete = async (doc: DocumentSummary) => {
    setDeletingId(doc.id)
    try {
      await deleteDocument(doc.id)
      const refreshed = await listDocuments()
      setDocuments(refreshed)
      if (selectedDoc?.id === doc.id) setSelectedDoc(refreshed[0] ?? null)
    } catch {
      // silently fail
    } finally {
      setDeletingId(null)
    }
  }

  // ── Suggested questions ─────────────────────────────
  const suggestions = [
    "What is the main topic of this document?",
    "Summarize the key findings.",
    "What are the conclusions?",
    "List the key recommendations.",
  ]

  // ── Render ────────────────────────────────────────────

  return (
    <div className="space-y-10 animate-fade-in pb-20">

      {/* Header */}
      <div>
        <h1 className="text-4xl font-display font-bold tracking-tight mb-2 flex items-center gap-3">
          <BookOpen className="w-9 h-9 text-brand-light" />
          Document Q&amp;A
        </h1>
        <p className="text-foreground/60 text-lg">
          Upload a PDF and ask questions — powered by PageIndex reasoning-based retrieval.
        </p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 items-start">

        {/* ═══ LEFT COLUMN: Upload + Document List ═══ */}
        <div className="xl:col-span-1 space-y-6">

          {/* Upload Zone */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-lg flex items-center gap-2">
                <UploadCloud className="w-5 h-5 text-brand-light" />
                Upload PDF
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div
                className={`relative border-2 border-dashed rounded-2xl p-8 flex flex-col items-center justify-center text-center cursor-pointer transition-all duration-200
                  ${dragOver ? "border-brand-light bg-brand/10" : "border-surface-border hover:border-brand/40 hover:bg-brand/5"}
                  ${uploading ? "pointer-events-none opacity-70" : ""}`}
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                onDragLeave={() => setDragOver(false)}
                onDrop={onDrop}
              >
                <input ref={fileInputRef} type="file" accept=".pdf" className="hidden" onChange={onFileChange} />
                {uploading ? (
                  <>
                    <Loader2 className="w-10 h-10 text-brand-light animate-spin mb-3" />
                    <p className="text-sm font-medium text-foreground/70">Processing PDF & generating tree index…</p>
                    <p className="text-xs text-foreground/40 mt-1">This may take up to 60s for large files.</p>
                  </>
                ) : (
                  <>
                    <div className="w-14 h-14 rounded-2xl bg-brand/10 flex items-center justify-center mb-3">
                      <UploadCloud className="w-7 h-7 text-brand-light" />
                    </div>
                    <p className="font-semibold mb-1">Drop a PDF here</p>
                    <p className="text-sm text-foreground/40">or click to browse · Max 50MB</p>
                  </>
                )}
              </div>

              {uploadSuccess && (
                <div className="mt-3 flex items-center gap-2 text-emerald-400 text-sm">
                  <CheckCircle2 className="w-4 h-4 shrink-0" />
                  {uploadSuccess}
                </div>
              )}
              {uploadError && (
                <div className="mt-3 flex items-center gap-2 text-red-400 text-sm">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  {uploadError}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Document List */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-lg flex items-center gap-2">
                <FileText className="w-5 h-5 text-brand-light" />
                My Documents
                {documents.length > 0 && (
                  <span className="ml-auto text-xs font-normal text-foreground/40 bg-surface border border-surface-border px-2 py-0.5 rounded-full">
                    {documents.length}
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {documents.length === 0 ? (
                <p className="text-foreground/40 text-sm text-center py-8">
                  No documents yet — upload a PDF to get started.
                </p>
              ) : (
                <div className="space-y-2">
                  {documents.map((doc) => (
                    <div
                      key={doc.id}
                      className={`group flex items-center gap-3 p-3 rounded-xl border transition-all cursor-pointer ${
                        selectedDoc?.id === doc.id
                          ? "border-brand-light/50 bg-brand/10 shadow-[0_0_20px_rgba(212,168,83,0.12)]"
                          : "border-surface-border hover:border-brand/30 hover:bg-surface/[0.04]"
                      }`}
                      onClick={() => setSelectedDoc(doc)}
                    >
                      <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${
                        selectedDoc?.id === doc.id ? "bg-brand/20" : "bg-surface border border-surface-border"
                      }`}>
                        <FileText className={`w-4 h-4 ${selectedDoc?.id === doc.id ? "text-brand-light" : "text-foreground/50"}`} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">{doc.document_name}</p>
                        <div className="flex items-center gap-2 mt-0.5">
                          <StatusBadge status={doc.status} />
                          {doc.page_count && (
                            <span className="text-xs text-foreground/40 flex items-center gap-1">
                              <Hash className="w-3 h-3" />{doc.page_count}p
                            </span>
                          )}
                          {doc.query_count > 0 && (
                            <span className="text-xs text-foreground/40 flex items-center gap-1">
                              <Clock className="w-3 h-3" />{doc.query_count}q
                            </span>
                          )}
                        </div>
                      </div>
                      {selectedDoc?.id === doc.id && (
                        <ChevronRight className="w-4 h-4 text-brand-light shrink-0" />
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDelete(doc) }}
                        className="shrink-0 opacity-0 group-hover:opacity-100 p-1.5 rounded-lg hover:bg-red-500/10 hover:text-red-400 text-foreground/30 transition-all"
                        disabled={deletingId === doc.id}
                      >
                        {deletingId === doc.id
                          ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          : <Trash2 className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

        </div>

        {/* ═══ RIGHT COLUMN: Q&A Panel ═══ */}
        <div className="xl:col-span-2">
          {!selectedDoc ? (
            <div className="h-96 flex flex-col items-center justify-center text-center border border-dashed border-surface-border rounded-2xl p-12">
              <BookOpen className="w-14 h-14 text-foreground/15 mb-4" />
              <h3 className="text-lg font-semibold text-foreground/40">No document selected</h3>
              <p className="text-sm text-foreground/30 mt-1">Upload or select a document from the list to start asking questions.</p>
            </div>
          ) : selectedDoc.status !== "ready" ? (
            <div className="h-96 flex flex-col items-center justify-center text-center border border-surface-border rounded-2xl p-12">
              <AlertCircle className="w-12 h-12 text-amber-400 mb-4" />
              <h3 className="text-lg font-semibold">Document not ready</h3>
              <p className="text-sm text-foreground/50 mt-2">
                Status: <StatusBadge status={selectedDoc.status} />
              </p>
              <p className="text-sm text-foreground/40 mt-2">
                {selectedDoc.status === "processing"
                  ? "The document is still being processed. Please wait."
                  : "The document failed to process. Please try re-uploading."}
              </p>
            </div>
          ) : (
            <Card className="flex flex-col border-brand/10">
              {/* Q&A Header */}
              <CardHeader className="pb-3 border-b border-surface-border/50">
                <CardTitle className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-lg bg-brand/15 flex items-center justify-center border border-brand/20">
                    <BrainCircuit className="w-5 h-5 text-brand-light" />
                  </div>
                  <div>
                    <p className="text-base font-bold leading-tight">{selectedDoc.document_name}</p>
                    <p className="text-xs text-foreground/40 font-normal mt-0.5">
                      {selectedDoc.page_count} pages · PageIndex reasoning search
                    </p>
                  </div>
                </CardTitle>
              </CardHeader>

              <CardContent className="pt-6 space-y-6">

                {/* Question Input */}
                <form onSubmit={handleAsk} className="flex gap-3">
                  <div className="flex-1 relative">
                    <BrainCircuit className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-brand-light/50 pointer-events-none" />
                    <Input
                      value={question}
                      onChange={(e) => setQuestion(e.target.value)}
                      placeholder="Ask anything about this document..."
                      className="h-13 pl-12 pr-4 text-base rounded-2xl border-surface-border bg-surface/[0.03] focus:bg-surface/[0.06] focus:border-brand-light/40 transition-all placeholder:text-foreground/20"
                      disabled={asking}
                    />
                  </div>
                  <Button
                    type="submit"
                    variant="brand"
                    className="h-13 px-6 rounded-2xl font-semibold shadow-[0_0_20px_rgba(212,168,83,0.25)]"
                    disabled={!question.trim() || asking}
                  >
                    {asking ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
                  </Button>
                </form>

                {/* Suggested questions (shown when no history) */}
                {history.length === 0 && !asking && (
                  <div className="flex flex-wrap gap-2">
                    {suggestions.map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => setQuestion(s)}
                        className="px-3 py-2 rounded-full border border-surface-border text-xs text-foreground/60 hover:border-brand/40 hover:text-brand-light hover:bg-brand/5 transition-all"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}

                {/* Loading indicator */}
                {asking && (
                  <div className="flex items-center gap-3 p-4 rounded-xl bg-brand/5 border border-brand/15 text-sm text-brand-light animate-pulse">
                    <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                    Navigating document tree and generating answer…
                  </div>
                )}

                {/* Q&A Error */}
                {qaError && (
                  <div className="flex items-start gap-3 p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                    <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                    {qaError}
                  </div>
                )}

                {/* Answer History */}
                {history.length > 0 && (
                  <div className="space-y-5">
                    {history.map((item, i) => (
                      <div key={i} className="space-y-3">
                        {/* Question bubble */}
                        <div className="flex justify-end">
                          <div className="max-w-[80%] px-4 py-3 rounded-2xl rounded-tr-sm bg-gradient-to-br from-brand to-brand-light text-[#06060A] font-medium text-sm shadow-[0_4px_20px_rgba(212,168,83,0.25)]">
                            {item.question}
                          </div>
                        </div>

                        {/* Answer card */}
                        <div className="rounded-2xl border border-surface-border bg-surface/[0.03] p-5 space-y-4">
                          {/* Answer header */}
                          <div className="flex items-center gap-2">
                            <div className="w-7 h-7 rounded-lg bg-brand/15 flex items-center justify-center border border-brand/20">
                              <BrainCircuit className="w-3.5 h-3.5 text-brand-light" />
                            </div>
                            <span className="text-xs font-semibold text-foreground/50 uppercase tracking-wider">PageIndex Answer</span>
                            <div className="ml-auto">
                              <ConfidenceBadge score={item.confidence_score} />
                            </div>
                          </div>

                          {/* Answer text */}
                          <p className="text-sm text-foreground/85 leading-relaxed whitespace-pre-wrap">
                            {item.answer}
                          </p>

                          {/* Retrieved pages */}
                          {item.retrieved_pages && item.retrieved_pages.length > 0 && (
                            <div className="pt-3 border-t border-surface-border/50">
                              <p className="text-xs font-semibold text-foreground/40 uppercase tracking-wider mb-2">
                                Sources retrieved
                              </p>
                              <div className="flex flex-wrap gap-2">
                                {item.retrieved_pages.map((p, j) => (
                                  <div key={j} className="px-3 py-1.5 rounded-lg border border-surface-border bg-surface/[0.04] text-xs">
                                    <span className="text-foreground/70 font-medium">{p.title}</span>
                                    <span className="text-foreground/35 ml-2">pp. {p.start_page}–{p.end_page}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>

                        {/* Divider between Q&A pairs */}
                        {i < history.length - 1 && (
                          <div className="border-t border-surface-border/30" />
                        )}
                      </div>
                    ))}
                  </div>
                )}

              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
