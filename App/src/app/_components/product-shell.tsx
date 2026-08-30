import type { ReactNode } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/ui/Logo'

const navigation = [
  { href: '/projects', label: 'Projects' },
  { href: '/upload', label: 'Upload' },
  { href: '/account', label: 'Account' },
]

export function ProductShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-neutral-50">
      <header className="border-b border-neutral-200 bg-white">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-6 px-5 sm:px-8">
          <Link href="/projects" className="flex items-center gap-2 text-neutral-900">
            <Logo size={24} className="text-brand-400" />
            <span className="font-semibold tracking-tight">InstaDescribe</span>
          </Link>
          <nav aria-label="Product" className="ml-auto flex items-center gap-1">
            {navigation.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="rounded-md px-3 py-2 text-sm font-medium text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900"
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl px-5 py-8 sm:px-8">{children}</main>
    </div>
  )
}
