import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto'
import type { DataKeyProvider, EncryptedEnvelope } from './ports'

const KEY_BYTES = 32
const IV_BYTES = 12

function encode(value: Uint8Array): string {
  return Buffer.from(value).toString('base64')
}

function decode(value: string): Buffer | null {
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length > 65_536) return null
  try {
    return Buffer.from(value, 'base64')
  } catch {
    return null
  }
}

export class EnvelopeCrypto {
  constructor(private readonly keys: DataKeyProvider) {}

  async encrypt(value: unknown, context: Readonly<Record<string, string>>): Promise<EncryptedEnvelope | 'unavailable'> {
    const generated = await this.keys.generate(context)
    if (generated === 'unavailable' || generated.plaintextKey.byteLength !== KEY_BYTES) return 'unavailable'

    const plaintextKey = Buffer.from(generated.plaintextKey)
    try {
      const iv = randomBytes(IV_BYTES)
      const cipher = createCipheriv('aes-256-gcm', plaintextKey, iv)
      cipher.setAAD(Buffer.from(JSON.stringify(context), 'utf8'))
      const ciphertext = Buffer.concat([
        cipher.update(JSON.stringify(value), 'utf8'),
        cipher.final(),
      ])
      return {
        version: 1,
        encryptedKey: encode(generated.encryptedKey),
        iv: encode(iv),
        ciphertext: encode(ciphertext),
        authTag: encode(cipher.getAuthTag()),
      }
    } catch {
      return 'unavailable'
    } finally {
      plaintextKey.fill(0)
      generated.plaintextKey.fill(0)
    }
  }

  async decrypt<T>(
    envelope: EncryptedEnvelope,
    context: Readonly<Record<string, string>>,
  ): Promise<T | 'unavailable'> {
    if (envelope.version !== 1) return 'unavailable'
    const encryptedKey = decode(envelope.encryptedKey)
    const iv = decode(envelope.iv)
    const ciphertext = decode(envelope.ciphertext)
    const authTag = decode(envelope.authTag)
    if (!encryptedKey || !iv || !ciphertext || !authTag || iv.byteLength !== IV_BYTES || authTag.byteLength !== 16) {
      return 'unavailable'
    }

    const decrypted = await this.keys.decrypt(encryptedKey, context)
    if (decrypted === 'unavailable' || decrypted.byteLength !== KEY_BYTES) return 'unavailable'
    const plaintextKey = Buffer.from(decrypted)
    try {
      const decipher = createDecipheriv('aes-256-gcm', plaintextKey, iv)
      decipher.setAAD(Buffer.from(JSON.stringify(context), 'utf8'))
      decipher.setAuthTag(authTag)
      const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()])
      if (plaintext.byteLength > 65_536) return 'unavailable'
      return JSON.parse(plaintext.toString('utf8')) as T
    } catch {
      return 'unavailable'
    } finally {
      plaintextKey.fill(0)
      decrypted.fill(0)
    }
  }
}
