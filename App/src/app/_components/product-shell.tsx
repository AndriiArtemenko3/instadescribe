import type { ReactNode } from 'react'
import Link from 'next/link'
import { Logo } from '@/components/ui/Logo'

const navigation = [
  { href: '/investigations', label: 'Investigations', shortLabel: 'Cases' },
  { href: '/investigations/new', label: 'New investigation', shortLabel: 'New' },
  { href: '/account', label: 'Account', shortLabel: 'Account' },
]

export function ProductShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-neutral-50">
      <header className="border-b border-neutral-200 bg-white">
        <div className="mx-auto flex h-16 max-w-7xl min-w-0 items-center gap-2 px-3 md:gap-6 md:px-8">
          <Link
            href="/investigations"
            aria-label="InstaDescribe investigations home"
            className="flex shrink-0 items-center gap-2 text-neutral-900"
          >
            <Logo size={24} className="text-brand-400" />
            <span className="hidden font-semibold tracking-tight md:inline">InstaDescribe</span>
            <span className="hidden rounded-full bg-brand-50 px-2 py-1 text-xs font-medium text-brand-800 md:inline">
              Video intelligence
            </span>
          </Link>
          <nav aria-label="Product" className="ml-auto flex shrink-0 items-center gap-0 md:gap-1">
            {navigation.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-label={item.label}
                className="whitespace-nowrap rounded-md px-1.5 py-2 text-sm font-medium text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900 md:px-3"
              >
                <span className="md:hidden">{item.shortLabel}</span>
                <span className="hidden md:inline">{item.label}</span>
              </Link>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl px-5 py-8 sm:px-8">{children}</main>
    </div>
  )
}
