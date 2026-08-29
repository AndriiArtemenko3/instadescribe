import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', '.next']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Dev-only fast-refresh hint. shadcn/ui components export variant helpers
      // alongside the component and the router co-locates route elements; neither
      // affects runtime, so this rule is off.
      'react-refresh/only-export-components': 'off',
      // Allow underscore-prefixed throwaways in destructures / args / catches.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
      // Visible but non-blocking: the two call sites are init-from-loaded-data and
      // a viewport-resize resync — both legitimate effect uses.
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
])
