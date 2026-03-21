"use client"

import { useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { AuthLayout } from "@/components/layout/AuthLayout"
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/Card"
import { Input } from "@/components/ui/Input"
import { Button } from "@/components/ui/Button"
import { Mail, ArrowRight, Loader2, AlertCircle, CheckCircle2 } from "lucide-react"
import { forgotPassword } from "@/lib/auth"

export default function ForgotPasswordPage() {
  const router = useRouter()
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")
  const [email, setEmail] = useState("")

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setSuccess("")
    setIsLoading(true)

    try {
      const result = await forgotPassword({ email })
      setSuccess(result.message)
      setTimeout(() => {
        router.push(`/reset-password?email=${encodeURIComponent(email)}`)
      }, 2000)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <AuthLayout>
      <div className="animate-slide-up" style={{ animationDuration: "0.6s" }}>
        <Card className="border-brand/20 shadow-[0_0_50px_rgba(138,43,226,0.1)]">
          <CardHeader className="text-center pb-6">
            <CardTitle className="text-3xl font-bold tracking-tight mb-2">Forgot Password</CardTitle>
            <CardDescription className="text-base">
              Enter your email and we&apos;ll send you a code to reset your password.
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
            <form onSubmit={handleSubmit} className="space-y-5">
              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground/80 pl-1" htmlFor="email">Email Address</label>
                <div className="relative">
                  <Mail className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-foreground/40" />
                  <Input 
                    id="email" 
                    type="email" 
                    placeholder="name@company.com" 
                    className="pl-12 h-14 text-base" 
                    required 
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </div>
              </div>

              <Button type="submit" variant="brand" className="w-full h-14 text-lg mt-4 group" disabled={isLoading}>
                {isLoading ? (
                  <Loader2 className="w-6 h-6 animate-spin" />
                ) : (
                  <>
                    Send Reset Code
                    <ArrowRight className="w-5 h-5 ml-2 group-hover:translate-x-1 transition-transform" />
                  </>
                )}
              </Button>
            </form>
          </CardContent>
          <CardFooter className="flex justify-center pt-2 pb-8 border-t border-surface-border/50 mt-6">
            <p className="text-foreground/60 text-base mt-6">
              Remember your password?{" "}
              <Link href="/login" className="text-brand font-semibold hover:text-brand-light transition-colors">
                Back to login
              </Link>
            </p>
          </CardFooter>
        </Card>
      </div>
    </AuthLayout>
  )
}
