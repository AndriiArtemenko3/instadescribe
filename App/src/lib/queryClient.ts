// The single TanStack Query client (G7.1 B): exported as a module so cloud
// logout can clear EVERY cached manifest, signed URL and artifact JSON —
// wrong-token re-entry must never render previously cached editor data.

import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,
      retry: 1,
    },
  },
})
