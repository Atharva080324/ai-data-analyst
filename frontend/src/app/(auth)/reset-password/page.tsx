"use client"

import { useState, Suspense } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { AuthLayout } from "@/components/layout/AuthLayout"
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/Card"
import { Input } from "@/components/ui/Input"
import { Button } from "@/components/ui/Button"
import { Lock, ShieldCheck, ArrowRight, Loader2, AlertCircle, CheckCircle2 } from "lucide-react"
import { resetPassword } from "@/lib/auth"

function ResetPasswordForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const emailFromUrl = searchParams.get("email") || ""

  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")
  const [email, setEmail] = useState(emailFromUrl)
  const [otp, setOtp] = useState("")
  const [newPassword, setNewPassword] = useState("")

  const handleReset = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setSuccess("")
    setIsLoading(true)

    try {
      const result = await resetPassword({ email, otp, new_password: newPassword })
      setSuccess(result.message)
      setTimeout(() => router.push("/login"), 2000)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Card className="border-brand/20 shadow-[0_0_50px_rgba(138,43,226,0.1)]">
      <CardHeader className="text-center pb-6">
        <div className="w-16 h-16 rounded-2xl bg-brand/10 flex items-center justify-center mx-auto mb-4">
          <ShieldCheck className="w-8 h-8 text-brand-light" />
        </div>
        <CardTitle className="text-3xl font-bold tracking-tight mb-2">Reset Password</CardTitle>
        <CardDescription className="text-base">
          Enter the code from your email and choose a new password.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error && (
          <div className="flex items-center gap-3 p-4 mb-5 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
            <AlertCircle className="w-5 h-5 shrink-0" />
            {error}
          </div>
        )}
        {success && (
          <div className="flex items-center gap-3 p-4 mb-5 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm">
            <CheckCircle2 className="w-5 h-5 shrink-0" />
            {success}
          </div>
        )}
        <form onSubmit={handleReset} className="space-y-5">
          {!emailFromUrl && (
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground/80 pl-1" htmlFor="email">Email Address</label>
              <Input 
                id="email" 
                type="email" 
                placeholder="name@company.com" 
                className="h-14 text-base" 
                required 
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
          )}

          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground/80 pl-1" htmlFor="otp">Reset Code</label>
            <Input 
              id="otp" 
              type="text" 
              placeholder="123456" 
              className="h-14 text-base text-center tracking-[0.5em] text-2xl font-mono"
              maxLength={6}
              required 
              value={otp}
              onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground/80 pl-1" htmlFor="new-password">New Password</label>
            <div className="relative">
              <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-foreground/40" />
              <Input 
                id="new-password" 
                type="password" 
                placeholder="••••••••" 
                className="pl-12 h-14 text-base" 
                required 
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
            </div>
            <p className="text-xs text-foreground/40 pl-1">Min 8 chars, 1 uppercase, 1 number</p>
          </div>

          <Button type="submit" variant="brand" className="w-full h-14 text-lg mt-4 group" disabled={isLoading}>
            {isLoading ? (
              <Loader2 className="w-6 h-6 animate-spin" />
            ) : (
              <>
                Reset Password
                <ArrowRight className="w-5 h-5 ml-2 group-hover:translate-x-1 transition-transform" />
              </>
            )}
          </Button>
        </form>
      </CardContent>
      <CardFooter className="flex justify-center pt-2 pb-8 border-t border-surface-border/50 mt-6">
        <p className="text-foreground/60 text-base mt-6">
          <Link href="/login" className="text-brand font-semibold hover:text-brand-light transition-colors">
            Back to login
          </Link>
        </p>
      </CardFooter>
    </Card>
  )
}

export default function ResetPasswordPage() {
  return (
    <AuthLayout>
      <div className="animate-slide-up" style={{ animationDuration: "0.6s" }}>
        <Suspense fallback={<div className="text-center text-foreground/40">Loading...</div>}>
          <ResetPasswordForm />
        </Suspense>
      </div>
    </AuthLayout>
  )
}
