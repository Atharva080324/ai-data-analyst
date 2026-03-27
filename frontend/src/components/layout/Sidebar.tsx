"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { LayoutDashboard, MessageSquare, Database, User, LogOut, FileText } from "lucide-react"
import { cn } from "@/lib/utils"

const navItems = [
  { name: "Overview", href: "/dashboard", icon: LayoutDashboard },
  { name: "Analysis", href: "/analysis", icon: MessageSquare },
  { name: "Datasets", href: "/datasets", icon: Database },
  { name: "Documents", href: "/documents", icon: FileText },
  { name: "Profile", href: "/profile", icon: User },
]


export function Sidebar() {
  const pathname = usePathname()

  return (
    <aside className="fixed left-0 top-0 z-40 h-screen w-72 border-r border-surface-border bg-background/50 backdrop-blur-xl flex flex-col">
      <div className="px-8 h-24 flex items-center border-b border-surface-border/50">
        <Link href="/dashboard" className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-light to-brand-dark flex items-center justify-center shadow-glow">
             <Database className="w-4 h-4 text-background" />
          </div>
          <span className="text-xl font-display font-bold tracking-tight luxury-text-gradient">AI Analyst</span>
        </Link>
      </div>

      <div className="flex-1 py-8 px-4 flex flex-col gap-2 overflow-y-auto">
        {navItems.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link
              key={item.name}
              href={item.href}
              className={cn(
                "flex items-center space-x-3 px-4 py-3 rounded-xl transition-all duration-200 group",
                isActive
                  ? "bg-brand/15 text-brand-light"
                  : "text-foreground/60 hover:bg-surface-hover hover:text-brand-light"
              )}
            >
              <item.icon className={cn("w-5 h-5", isActive ? "text-brand-light" : "text-foreground/50 group-hover:text-brand-light")} />
              <span className="font-medium">{item.name}</span>
            </Link>
          )
        })}
      </div>

      <div className="p-4 border-t border-surface-border/50">
        <button className="flex items-center space-x-3 w-full px-4 py-3 rounded-xl text-foreground/60 hover:bg-red-500/10 hover:text-red-500 transition-colors group">
          <LogOut className="w-5 h-5 text-foreground/50 group-hover:text-red-500" />
          <span className="font-medium">Logout</span>
        </button>
      </div>
    </aside>
  )
}
