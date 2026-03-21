"use client"

import { useState, Suspense } from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { AuthLayout } from "@/components/layout/AuthLayout"
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/Card"
import { Input } from "@/components/ui/Input"
import { Button } from "@/components/ui/Button"
import { ShieldCheck, ArrowRight, Loader2, AlertCircle, RefreshCw } from "lucide-react"
import { verifyEmail, resendOTP } from "@/lib/auth"

function VerifyEmailForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const emailFromUrl = searchParams.get("email") || ""

  const [isLoading, setIsLoading] = useState(false)
  const [isResending, setIsResending] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")
  const [email, setEmail] = useState(emailFromUrl)
  const [otp, setOtp] = useState("")

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setSuccess("")
    setIsLoading(true)

    try {
      const result = await verifyEmail({ email, otp })
      setSuccess(result.message)
      setTimeout(() => router.push("/login"), 2000)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed")
    } finally {
      setIsLoading(false)
    }
  }

  const handleResend = async () => {
    if (!email) {
      setError("Please enter your email address")
      return
    }
    setError("")
    setSuccess("")
    setIsResending(true)

    try {
      const result = await resendOTP({ email, purpose: "verify_email" })
      setSuccess(result.message)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resend OTP")
    } finally {
      setIsResending(false)
    }
  }

  return (
    <Card className="border-brand/20 shadow-[0_0_50px_rgba(138,43,226,0.1)]">
      <CardHeader className="text-center pb-6">
        <div className="w-16 h-16 rounded-2xl bg-brand/10 flex items-center justify-center mx-auto mb-4">
          <ShieldCheck className="w-8 h-8 text-brand-light" />
        </div>
        <CardTitle className="text-3xl font-bold tracking-tight mb-2">Verify Your Email</CardTitle>
        <CardDescription className="text-base">
          We sent a 6-digit code to <strong className="text-foreground">{email || "your email"}</strong>
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
            <ShieldCheck className="w-5 h-5 shrink-0" />
            {success}
          </div>
        )}
        <form onSubmit={handleVerify} className="space-y-5">
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
            <label className="text-sm font-medium text-foreground/80 pl-1" htmlFor="otp">Verification Code</label>
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

          <Button type="submit" variant="brand" className="w-full h-14 text-lg mt-4 group" disabled={isLoading}>
            {isLoading ? (
              <Loader2 className="w-6 h-6 animate-spin" />
            ) : (
              <>
                Verify Email
                <ArrowRight className="w-5 h-5 ml-2 group-hover:translate-x-1 transition-transform" />
              </>
            )}
          </Button>
        </form>

        <div className="mt-6 text-center">
          <button
            onClick={handleResend}
            disabled={isResending}
            className="text-sm text-foreground/60 hover:text-brand-light transition-colors inline-flex items-center gap-2"
          >
            {isResending ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Didn&apos;t receive it? Resend code
          </button>
        </div>
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

export default function VerifyEmailPage() {
  return (
    <AuthLayout>
      <div className="animate-slide-up" style={{ animationDuration: "0.6s" }}>
        <Suspense fallback={<div className="text-center text-foreground/40">Loading...</div>}>
          <VerifyEmailForm />
        </Suspense>
      </div>
    </AuthLayout>
  )
}
