import type { ReactNode } from 'react'
import { ProductShell } from '@/app/_components/product-shell'

export default function ProductLayout({ children }: { children: ReactNode }) {
  return <ProductShell>{children}</ProductShell>
}
