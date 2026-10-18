/*
 * Tailwind theme for Daredevil, used two ways:
 *  - by the Tailwind CLI while the Docker image builds (see Dockerfile), which
 *    writes static/css/tailwind.css from the classes in templates/ and apps/;
 *  - in the browser by the Tailwind Play CDN, the fallback base.html uses when
 *    that file hasn't been built (bare-metal runs, or the repo bind-mounted
 *    into a dev container).
 * Brand colours are CSS variables so App Settings → Appearance can switch theme.
 */
const ddTailwindConfig = {
  content: ['./templates/**/*.html', './apps/**/*.py'],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  'rgb(var(--b50)  / <alpha-value>)',
          100: 'rgb(var(--b100) / <alpha-value>)',
          200: 'rgb(var(--b200) / <alpha-value>)',
          300: 'rgb(var(--b300) / <alpha-value>)',
          400: 'rgb(var(--b400) / <alpha-value>)',
          500: 'rgb(var(--b500) / <alpha-value>)',
          600: 'rgb(var(--b600) / <alpha-value>)',
          700: 'rgb(var(--b700) / <alpha-value>)',
          800: 'rgb(var(--b800) / <alpha-value>)',
          900: 'rgb(var(--b900) / <alpha-value>)',
        },
        surface: {
          50:  '#e8e8f4',
          100: '#c5c5e8',
          200: '#9d9dcc',
          300: '#7777b0',
          400: '#55559a',
          500: '#333370',
          600: '#25255a',
          700: '#181840',
          800: '#0e0e2e',
          900: '#07071a',
          950: '#030310',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
};

if (typeof module !== 'undefined') module.exports = ddTailwindConfig;
else tailwind.config = ddTailwindConfig;
