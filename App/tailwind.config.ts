import type { Config } from 'tailwindcss'

// ─────────────────────────────────────────────────────────────────────────────
// DESIGN SYSTEM STATUS: PLACEHOLDER
//
// All colour tokens in this file are temporary placeholders.
// No brand identity has been defined for InstaScribe yet.
// When the brand system is established, update the values here.
// Zero component code should need changing — tokens only.
//
// Until then: use shadcn/ui component defaults as-is.
// Do not make opinionated visual decisions in components.
// ─────────────────────────────────────────────────────────────────────────────

const config: Config = {
  darkMode: ['class'],
  // Application sources only. src/portfolio-demo/** is deliberately NOT
  // scanned here — it belongs to the separate portfolio-demo entry
  // (tailwind.portfolio-demo.config.ts), so its class usage cannot alter the
  // default application build's CSS output.
  content: [
    './index.html',
    './src/*.{ts,tsx}',
    './src/assets/**/*.{ts,tsx}',
    './src/components/**/*.{ts,tsx}',
    './src/features/**/*.{ts,tsx}',
    './src/hooks/**/*.{ts,tsx}',
    './src/lib/**/*.{ts,tsx}',
    './src/router/**/*.{ts,tsx}',
    './src/store/**/*.{ts,tsx}',
    './src/types/**/*.{ts,tsx}',
  ],

  theme: {
    extend: {

      // ─── COLOURS ────────────────────────────────────────────────
      // PLACEHOLDER values — will be replaced when brand is defined.
      // Structured to match shadcn/ui CSS variable conventions.
      colors: {

        // Brand — placeholder teal
        // Replace entire scale when brand colour is chosen.
        brand: {
          50:  '#E8F7F2',
          100: '#C2EADB',
          200: '#7DD4BA',
          400: '#1BA87A',
          500: '#179167',
          600: '#127A58',
          800: '#0A5038',
          900: '#042C1E',
        },

        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',

        sidebar: {
          DEFAULT: 'hsl(var(--sidebar-background))',
          foreground: 'hsl(var(--sidebar-foreground))',
          accent: 'hsl(var(--sidebar-accent))',
          'accent-foreground': 'hsl(var(--sidebar-accent-foreground))',
          border: 'hsl(var(--sidebar-border))',
          ring: 'hsl(var(--sidebar-ring))',
        },

        // Neutrals — pure grey scale, no temperature
        // These are stable and unlikely to change with rebranding.
        neutral: {
          0:   '#FFFFFF',
          50:  '#F9F9F9',   // page background
          100: '#F3F3F3',   // sidebar, secondary surfaces
          150: '#EBEBEB',   // hover states
          200: '#E0E0E0',   // subtle borders
          300: '#D4D4D4',   // default borders
          400: '#A0A0A0',   // placeholder text, muted icons
          500: '#737373',   // secondary text
          600: '#525252',   // body text
          700: '#404040',   // strong secondary text
          900: '#1A1A1A',   // primary text
          950: '#111111',   // dark surfaces
        },

        // Semantic — status colours
        // Used only for feedback states (success, warning, error, info).
        success: {
          50:  '#F0FDF4',
          400: '#22C55E',
          800: '#166534',
        },
        warning: {
          50:  '#FFFBEB',
          400: '#F59E0B',
          800: '#92400E',
        },
        danger: {
          50:  '#FEF2F2',
          400: '#EF4444',
          800: '#991B1B',
        },
        info: {
          50:  '#EFF6FF',
          400: '#3B82F6',
          800: '#1E40AF',
        },
      },

      // ─── TYPOGRAPHY ─────────────────────────────────────────────
      // PLACEHOLDER — font choices not finalised.
      // Using system-safe defaults until brand typography is defined.
      fontFamily: {
        sans: ['Geist Variable', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'monospace'],
      },

      fontSize: {
        'xs':   ['11px', { lineHeight: '16px' }],
        'sm':   ['12px', { lineHeight: '18px' }],
        'base': ['14px', { lineHeight: '20px' }],
        'md':   ['15px', { lineHeight: '22px' }],
        'lg':   ['18px', { lineHeight: '26px' }],
        'xl':   ['22px', { lineHeight: '30px' }],
        '2xl':  ['26px', { lineHeight: '34px' }],
        '3xl':  ['32px', { lineHeight: '40px' }],
        '4xl':  ['38px', { lineHeight: '46px' }],
      },

      fontWeight: {
        light:    '300',
        regular:  '400',
        medium:   '500',
        semibold: '600',
        bold:     '700',
      },

      // ─── SPACING ────────────────────────────────────────────────
      // Base unit: 4px. Add tokens here if new values are needed.
      // Never use arbitrary values in components.
      spacing: {
        '0':  '0px',
        '0.5': '2px',
        '1':  '4px',
        '1.5': '6px',
        '2':  '8px',
        '2.5': '10px',
        '3':  '12px',
        '3.5': '14px',
        '4':  '16px',
        '5':  '20px',
        '6':  '24px',
        '8':  '32px',
        '10': '40px',
        '12': '48px',
        '16': '64px',
        '20': '80px',
        '24': '96px',
      },

      // ─── BORDER RADIUS ──────────────────────────────────────────
      borderRadius: {
        'none': '0px',
        'sm':   '4px',
        'md':   '6px',
        'lg':   '8px',
        'xl':   '12px',
        'full': '9999px',
      },

      // ─── LAYOUT ─────────────────────────────────────────────────
      // Key dimensions used consistently across the app.
      // Update here if layout proportions change — never in components.
      width: {
        'sidebar':      '220px',
        'script-panel': '300px',
        'form-field':   '360px',
      },

      height: {
        'topnav':         '38px',
        'footer':         '48px',
        'input':          '42px',
        'btn':            '40px',
        'timeline-track': '18px',
      },

      // ─── BREAKPOINTS ────────────────────────────────────────────
      screens: {
        'sm':  '375px',   // mobile — dashboard + auth only
        'md':  '768px',   // tablet landscape — sidebar collapses
        'lg':  '1024px',  // desktop — full layout, editor supported
        'xl':  '1280px',  // wide desktop
        '2xl': '1440px',  // large desktop
      },

      // ─── SHADOWS ────────────────────────────────────────────────
      // Flat design — shadows used sparingly and only functionally.
      boxShadow: {
        'none':  'none',
        'focus': '0 0 0 3px rgba(0, 0, 0, 0.08)',
        'card':  '0 1px 3px rgba(0, 0, 0, 0.06)',
        'modal': '0 8px 32px rgba(0, 0, 0, 0.12)',
      },

      // ─── BORDERS ────────────────────────────────────────────────
      borderWidth: {
        DEFAULT: '1px',
        '0':     '0px',
        '2':     '2px',
      },

      // ─── TRANSITIONS ────────────────────────────────────────────
      transitionDuration: {
        'fast':  '100ms',
        DEFAULT: '150ms',
        'slow':  '300ms',
      },

      transitionTimingFunction: {
        DEFAULT: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },

  plugins: [],
}

export default config

