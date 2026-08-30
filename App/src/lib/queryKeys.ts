export const queryKeys = {
  projects: () => ['projects'] as const,
  project: (id: string) => ['projects', id] as const,
  scenes: (projectId: string) => ['projects', projectId, 'scenes'] as const,
  entities: (projectId: string) => ['projects', projectId, 'entities'] as const,
  adGaps: (projectId: string) => ['projects', projectId, 'adGaps'] as const,
  audioEvents: (projectId: string) => ['projects', projectId, 'audioEvents'] as const,
  overrides: (projectId: string) => ['projects', projectId, 'overrides'] as const,
  // Stable identifiers only — a signed URL never becomes part of a key.
  manifest: (projectId: string, jobId: string) =>
    ['projects', projectId, 'jobs', jobId, 'manifest'] as const,
  // G7.1 C: every cloud data key carries BOTH stable IDs, so a project
  // reconciled to a NEW processing job can never reuse an earlier job's
  // cached artifacts or overrides.
  cloudScenes: (projectId: string, jobId: string) =>
    ['projects', projectId, 'scenes', 'cloud', jobId] as const,
  cloudEntities: (projectId: string, jobId: string) =>
    ['projects', projectId, 'entities', 'cloud', jobId] as const,
  cloudAdGaps: (projectId: string, jobId: string) =>
    ['projects', projectId, 'adGaps', 'cloud', jobId] as const,
  cloudAudioEvents: (projectId: string, jobId: string) =>
    ['projects', projectId, 'audioEvents', 'cloud', jobId] as const,
  cloudOverrides: (projectId: string, jobId: string) =>
    ['projects', projectId, 'overrides', 'cloud', jobId] as const,
}
