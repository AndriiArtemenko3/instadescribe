// Vitest's default remains the fast node environment. Mounted DOM suites opt
// in with `@vitest-environment jsdom`; this fallback supplies Web Storage only
// to node suites. Storage-backed code under test — the portfolio token's
// sessionStorage continuity and Zustand persistence — can therefore use the
// same get/set/remove/clear/key contract in either environment.

class MemoryStorage implements Storage {
  private store = new Map<string, string>()

  get length(): number {
    return this.store.size
  }

  clear(): void {
    this.store.clear()
  }

  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null
  }

  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null
  }

  removeItem(key: string): void {
    this.store.delete(key)
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value))
  }
}

// Zustand's persist middleware only engages when a window exists; alias the
// node global so storage-backed persistence behaves like the browser.
if (typeof (globalThis as Record<string, unknown>).window === 'undefined') {
  Object.defineProperty(globalThis, 'window', { value: globalThis })
}
if (typeof globalThis.localStorage === 'undefined') {
  Object.defineProperty(globalThis, 'localStorage', { value: new MemoryStorage() })
}
if (typeof globalThis.sessionStorage === 'undefined') {
  Object.defineProperty(globalThis, 'sessionStorage', { value: new MemoryStorage() })
}
