import axios from "axios"
import { useAuthStore } from "./store"

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

export const api = axios.create({
  baseURL: API_URL,
  headers: {
    "Content-Type": "application/json",
  },
})

// ── Request interceptor: attach access token ─────────────────
api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ── Response interceptor: handle 401s with token refresh ─────
let isRefreshing = false
let failedQueue: { resolve: (token: string) => void; reject: (err: unknown) => void }[] = []

function processQueue(error: unknown, token: string | null = null) {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error)
    } else {
      prom.resolve(token!)
    }
  })
  failedQueue = []
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config

    // If it's a 401 and we haven't already retried this request
    if (error.response?.status === 401 && !originalRequest._retry) {
      const refreshToken = useAuthStore.getState().refreshToken

      // No refresh token available — just logout
      if (!refreshToken) {
        useAuthStore.getState().logout()
        if (typeof window !== "undefined" && !window.location.pathname.includes("/login")) {
          window.location.href = "/login"
        }
        return Promise.reject(error)
      }

      // If already refreshing, queue this request
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject })
        }).then((token) => {
          originalRequest.headers.Authorization = `Bearer ${token}`
          return api(originalRequest)
        })
      }

      originalRequest._retry = true
      isRefreshing = true

      try {
        // Call refresh endpoint (bypasses interceptor by using raw axios)
        const { data } = await axios.post(`${API_URL}/auth/refresh`, {
          refresh_token: refreshToken,
        })

        const newAccessToken = data.access_token
        const newRefreshToken = data.refresh_token

        // Update store with new tokens
        const user = useAuthStore.getState().user
        if (user) {
          useAuthStore.getState().login(user, newAccessToken, newRefreshToken)
        }

        processQueue(null, newAccessToken)

        // Retry the original request
        originalRequest.headers.Authorization = `Bearer ${newAccessToken}`
        return api(originalRequest)
      } catch (refreshError) {
        processQueue(refreshError, null)
        useAuthStore.getState().logout()
        if (typeof window !== "undefined" && !window.location.pathname.includes("/login")) {
          window.location.href = "/login"
        }
        return Promise.reject(refreshError)
      } finally {
        isRefreshing = false
      }
    }

    return Promise.reject(error)
  }
)

// ── Document API Helpers ─────────────────────────────────────

export interface DocumentSummary {
  id: string
  document_name: string
  page_count: number | null
  status: string
  query_count: number
  created_at: string
}

export interface DocumentAskResponse {
  question: string
  answer: string
  retrieved_pages: { title: string; start_page: number; end_page: number; summary: string }[] | null
  confidence_score: number | null
}

export async function uploadDocument(name: string, file: File) {
  const formData = new FormData()
  formData.append("document_name", name)
  formData.append("file", file)
  const { data } = await api.post("/documents/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  })
  return data
}

export async function listDocuments(): Promise<DocumentSummary[]> {
  const { data } = await api.get("/documents/")
  return data
}

export async function askDocument(documentId: string, question: string): Promise<DocumentAskResponse> {
  const { data } = await api.post("/documents/ask", { document_id: documentId, question })
  return data
}

export async function deleteDocument(documentId: string) {
  await api.delete(`/documents/${documentId}`)
}
