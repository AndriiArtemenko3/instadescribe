import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Card,
  CardContent,
  CardHeader,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Logo } from '@/components/ui/Logo'

export default function RegisterPage() {
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const isReady = name.length > 0 && email.length > 0 && password.length > 0

  function handleSubmit() {
    navigate('/login', { state: { message: 'Account created. Please sign in.' } })
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-neutral-50">
      <Card className="w-form-field">
        <CardHeader className="space-y-2">
          <div className="inline-flex items-center gap-2">
          <Logo size={24} className="text-brand-400" />
            <span className="font-mono text-sm font-medium">InstaDescribe</span>
          </div>
          <div className="space-y-1">
            <h1 className="text-lg font-medium">Create account</h1>
            <p className="text-sm text-muted-foreground">
              Get started with InstaDescribe
            </p>
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">Name</Label>
            <Input id="name" type="text" placeholder="Your Name" value={name} onChange={e => setName(e.target.value)} />
          </div>

          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" placeholder="email@example.com" value={email} onChange={e => setEmail(e.target.value)} />
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" placeholder="••••••••" value={password} onChange={e => setPassword(e.target.value)} />
          </div>

          <Button variant="default" className="w-full" disabled={!isReady} onClick={handleSubmit}>
            Create account
          </Button>

          {/* ── footer divider + link ── */}
          <div className="flex flex-col items-center gap-4 pt-2">
            <hr className="border-neutral-200 w-full" />
            <p className="text-sm text-muted-foreground">
              Already have an account?{' '}
              <Link to="/login" className="text-sm text-muted-foreground underline-offset-4 hover:underline">
                Sign in
              </Link>
            </p>
          </div>

        </CardContent>
      </Card>
    </div>
  )
}
