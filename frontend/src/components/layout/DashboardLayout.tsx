"use client"

import { ReactNode, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuthStore } from "@/lib/store"
import { fetchCurrentUser } from "@/lib/auth"
import { DashboardNavbar } from "./DashboardNavbar"

export function DashboardLayout({ children }: { children: ReactNode }) {
  const router = useRouter()
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const token = useAuthStore((s) => s.token)
  const hasHydrated = useAuthStore((s) => s._hasHydrated)
  const [isReady, setIsReady] = useState(false)

  useEffect(() => {
    // Wait for Zustand to hydrate from localStorage before checking auth
    if (!hasHydrated) return

    if (!isAuthenticated || !token) {
      router.replace("/login")
      return
    }

    // Token exists — verify it's still valid
    fetchCurrentUser()
      .then(() => setIsReady(true))
      .catch(() => {
        // Token invalid — interceptor handles logout+redirect
        setIsReady(true) // Let the interceptor redirect handle it
      })
  }, [hasHydrated, isAuthenticated, token, router])

  // Still loading (hydrating from localStorage or verifying token)
  if (!hasHydrated || !isReady) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-brand/30 border-t-brand rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      <DashboardNavbar />
      <main className="flex-1 mt-20">
        {/* Subtle ambient lighting */}
        <div className="fixed top-[10%] right-[-10%] w-[800px] h-[800px] bg-brand/5 blur-[120px] rounded-full pointer-events-none" />
        <div className="fixed bottom-[-10%] left-[10%] w-[600px] h-[600px] bg-accent/5 blur-[120px] rounded-full pointer-events-none" />
        
        <div className="relative z-10 p-6 md:p-10 max-w-7xl mx-auto min-h-screen">
          {children}
        </div>
      </main>
    </div>
  )
}
