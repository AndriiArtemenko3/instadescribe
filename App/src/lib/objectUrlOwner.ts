/** Single-owner lifecycle for an upload-preview object URL. */
export class ObjectUrlOwner {
  private selected: string | null = null
  private readonly revoked = new Set<string>()

  get current(): string | null {
    return this.selected
  }

  replace(file: Blob): string {
    if (this.selected) this.revoke(this.selected)
    const next = URL.createObjectURL(file)
    this.selected = next
    return next
  }

  owns(candidate: string): boolean {
    return this.selected === candidate
  }

  revoke(candidate: string): void {
    if (!this.revoked.has(candidate)) {
      URL.revokeObjectURL(candidate)
      this.revoked.add(candidate)
    }
    if (this.selected === candidate) this.selected = null
  }

  clear(): void {
    if (this.selected) this.revoke(this.selected)
  }
}
