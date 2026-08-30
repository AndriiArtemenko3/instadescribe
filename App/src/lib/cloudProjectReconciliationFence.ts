import {
  getPortfolioSessionIdentity,
  type PortfolioSessionIdentity,
} from './portfolioToken'

const reconciliationFenceBySession = new WeakMap<PortfolioSessionIdentity, number>()

export function reconciliationFence(identity: PortfolioSessionIdentity): number {
  return reconciliationFenceBySession.get(identity) ?? 0
}

export function advanceReconciliationFence(identity: PortfolioSessionIdentity): void {
  reconciliationFenceBySession.set(identity, reconciliationFence(identity) + 1)
}

/** Invalidate jobs-list snapshots that began before a successful local
    mutation. Kept outside appStore/cloudProjects to avoid a module cycle. */
export function fenceCloudProjectReconciliation(): void {
  const identity = getPortfolioSessionIdentity()
  if (identity) advanceReconciliationFence(identity)
}
