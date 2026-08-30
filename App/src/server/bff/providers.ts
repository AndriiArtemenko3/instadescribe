import type {
  BffDependencies,
  ProjectGateway,
  SessionGateway,
  SignInCredentials,
} from './contracts'

/**
 * Deliberate production default until Cognito/Dynamo adapters are provisioned.
 * It never creates a demo identity or trusts an unsigned browser value.
 */
class UnavailableSessionGateway implements SessionGateway {
  async lookup(_opaqueSession: string) {
    return { kind: 'unavailable' as const }
  }

  async signIn(_credentials: SignInCredentials) {
    return { kind: 'unavailable' as const }
  }

  async continueChallenge() {
    return { kind: 'unavailable' as const }
  }

  async inspectChallenge() {
    return 'unavailable' as const
  }

  async forgotPassword() {
    return { kind: 'unavailable' as const }
  }

  async resetPassword() {
    return { kind: 'unavailable' as const }
  }

  async beginMfaEnrollment() {
    return { kind: 'unavailable' as const }
  }

  async revoke(_opaqueSession: string) {
    return { kind: 'unavailable' as const }
  }
}

class UnavailableProjectGateway implements ProjectGateway {
  async list() {
    return { kind: 'unavailable' as const }
  }
}

export const defaultBffDependencies: BffDependencies = Object.freeze({
  sessions: new UnavailableSessionGateway(),
  projects: new UnavailableProjectGateway(),
})
