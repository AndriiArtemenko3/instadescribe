import type { Metadata } from 'next'
import type { ReactNode } from 'react'
import '@fontsource-variable/geist'
import '@/index.css'
import { Providers } from './providers'

export const metadata: Metadata = {
  title: {
    default: 'InstaDescribe',
    template: '%s · InstaDescribe',
  },
  description: 'Local-first, evidence-backed video investigation and provenance.',
}

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-neutral-50 font-sans text-neutral-900 antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
