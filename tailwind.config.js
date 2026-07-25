/* Design tokens — ported verbatim from managertools
 * (/home/terickson/managertools/manager-tool-django/tailwind.config.js)
 * so the two internal tools share one visual language. One accent
 * (teal), a deliberate type pair, and a tightened radius scale.
 *
 * Build (Tailwind CLI v3 — v4 changed the config model; keep pinned.
 * This repo's default `latest` binary resolves to v4, which drops the
 * `-c`/JS-config flow this file relies on, so the build command MUST
 * set TAILWINDCSS_VERSION explicitly):
 *   TAILWINDCSS_VERSION=v3.4.17 tailwindcss -c tailwind.config.js \
 *     -i static/src/input.css -o static/css/tw.css --minify
 *
 * content includes .py files: some views/forms may emit Tailwind
 * classes from Python strings (e.g. widget attrs), same rationale as
 * managertools.
 */
module.exports = {
  content: [
    "./webapp/templates/**/*.html",
    "./webapp/**/*.py",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Public Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['Fraunces', 'ui-serif', 'Georgia', 'serif'],
      },
      colors: {
        accent: {
          50: '#f0fdfa', 100: '#ccfbf1', 200: '#99f6e4', 300: '#5eead4',
          400: '#2dd4bf', 500: '#14b8a6', 600: '#0d9488', 700: '#0f766e',
          800: '#115e59', 900: '#134e4a',
        },
      },
      borderRadius: {
        DEFAULT: '0.25rem', md: '0.375rem', lg: '0.375rem', xl: '0.5rem',
      },
    },
  },
};
