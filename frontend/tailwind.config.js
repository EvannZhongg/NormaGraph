/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['IBM Plex Sans', 'Segoe UI Variable', 'Microsoft YaHei UI', 'sans-serif'],
        mono: ['IBM Plex Mono', 'Cascadia Code', 'Consolas', 'monospace'],
      },
      boxShadow: {
        panel: '0 18px 50px rgba(15, 23, 42, 0.16)',
      },
    },
  },
  plugins: [],
}
