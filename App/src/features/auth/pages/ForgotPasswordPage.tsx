import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Card,
  CardContent,
  CardHeader,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Logo } from '@/components/ui/Logo'

export default function ForgotPasswordPage() {
  const [sent, setSent] = useState(false)
  const [email, setEmail] = useState('')
  const isReady = email.length > 0

  return (
    <div className="flex min-h-screen items-center justify-center bg-neutral-50">
      <Card className="w-form-field">
        <CardHeader className="space-y-2">
          <div className="inline-flex items-center gap-2">
          <Logo size={24} className="text-brand-400" />
            <span className="font-mono text-sm font-medium">InstaDescribe</span>
          </div>
          <div className="space-y-1">
            <h1 className="text-lg font-medium">Reset password</h1>
            <p className="text-sm text-muted-foreground">
              Enter your email to receive a reset link
            </p>
          </div>
        </CardHeader>

        <CardContent>
          {sent ? (
            <p className="text-sm text-muted-foreground">
              Check your inbox — we've sent a reset link to your email.
            </p>
          ) : (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input id="email" type="email" placeholder="email@example.com" value={email} onChange={e => setEmail(e.target.value)} />
              </div>

              <Button variant="default" className="w-full" disabled={!isReady} onClick={() => setSent(true)}>
                Send reset link
              </Button>
            </div>
          )}

          {/* ── footer divider + link ── */}
          <div className="flex flex-col items-center gap-4 pt-6">
            <hr className="border-neutral-200 w-full" />
            <Link to="/login" className="text-sm text-muted-foreground underline-offset-4 hover:underline">
              Back to sign in
            </Link>
          </div>

        </CardContent>
      </Card>
    </div>
  )
}
