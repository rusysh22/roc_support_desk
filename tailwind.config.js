/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./*/templates/**/*.html",
  ],
  theme: {
    extend: {
      fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] },
      animation: {
        'float':      'float 6s ease-in-out infinite',
        'spin-slow':  'spin 20s linear infinite',
        'spin-rev':   'spin 15s linear infinite reverse',
        'pulse-slow': 'pulse 3s cubic-bezier(0.4,0,0.6,1) infinite',
        'pulse-glow': 'pulseGlow 2s cubic-bezier(0.4,0,0.6,1) infinite',
      },
      keyframes: {
        float: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%':      { transform: 'translateY(-20px)' },
        },
        pulseGlow: {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%':      { opacity: '.7', transform: 'scale(1.05)' },
        },
      },
    },
  },
  plugins: [],
}

