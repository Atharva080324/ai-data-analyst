import { ReactNode } from "react"
import { Navbar } from "./Navbar"

export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col bg-[#0A0A0F] relative selection:bg-brand selection:text-white overflow-hidden">
      
      {/* Background Floating Orbs (from reference image) */}
      <div className="absolute top-0 left-0 w-full h-full pointer-events-none z-0 overflow-hidden">
        {/* Giant Top Right Orb */}
        <div className="absolute -top-[10%] -right-[10%] w-[800px] h-[800px] bg-[radial-gradient(circle_at_30%_30%,rgba(150,180,255,0.4),rgba(50,80,255,0.05)_40%,transparent_70%)] rounded-full mix-blend-screen blur-[2px]" />
        
        {/* Big Bottom Left Orb */}
        <div className="absolute -bottom-[20%] -left-[10%] w-[600px] h-[600px] bg-[radial-gradient(circle_at_30%_30%,rgba(200,220,255,0.3),rgba(80,100,255,0.05)_50%,transparent_70%)] rounded-full mix-blend-screen blur-[1px]" />
        
        {/* Small Floating Orbs */}
        <div className="absolute top-[30%] left-[20%] w-24 h-24 bg-[radial-gradient(circle_at_30%_30%,rgba(255,255,255,0.8),rgba(150,180,255,0.2)_50%,transparent_70%)] rounded-full mix-blend-screen blur-[1px]" />
        <div className="absolute bottom-[40%] right-[25%] w-32 h-32 bg-[radial-gradient(circle_at_30%_30%,rgba(255,255,255,0.6),rgba(150,180,255,0.1)_50%,transparent_70%)] rounded-full mix-blend-screen blur-[2px]" />
      </div>
      
      <Navbar />
      
      <main className="flex-1 flex items-center justify-center p-6 relative z-10 pt-20">
        <div className="w-full max-w-[440px]">
          {children}
        </div>
      </main>
    </div>
  )
}
