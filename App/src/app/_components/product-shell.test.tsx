// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ProductShell } from './product-shell'

afterEach(cleanup)

describe('investigations-first product shell', () => {
  it('keeps audio description out of primary navigation', () => {
    render(<ProductShell><p>Workspace</p></ProductShell>)

    const navigation = screen.getByRole('navigation', { name: 'Product' })
    expect(screen.getAllByRole('link', { name: 'Investigations' }).length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: 'New investigation' })).toBeTruthy()
    expect(navigation.textContent).not.toContain('Audio description')
    expect(navigation.textContent).not.toContain('Upload')
  })

  it('uses a compact non-shrinking header at mobile widths so every navigation link remains reachable', () => {
    render(<ProductShell><p>Workspace</p></ProductShell>)

    const homeLink = screen.getByRole('link', { name: 'InstaDescribe investigations home' })
    const wordmark = screen.getByText('InstaDescribe')
    const navigation = screen.getByRole('navigation', { name: 'Product' })

    expect(homeLink.className).toContain('shrink-0')
    expect(wordmark.className).toContain('hidden')
    expect(wordmark.className).toContain('md:inline')
    expect(wordmark.className).not.toContain('sm:inline')
    expect(navigation.className).toContain('shrink-0')
    expect(navigation.className).toContain('md:gap-1')
    expect(screen.getByText('Cases').className).toContain('md:hidden')
    expect(screen.getAllByText('Investigations')[0]?.className).toContain('md:inline')
    expect(screen.getByRole('link', { name: 'Investigations' }).textContent).toContain('Cases')
    expect(screen.getByRole('link', { name: 'New investigation' }).textContent).toContain('New')
    expect(screen.getByRole('link', { name: 'Account' }).textContent).toContain('Account')
  })
})
