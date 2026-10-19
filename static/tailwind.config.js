/*
 * Tailwind theme for Daredevil, used two ways:
 *  - by the Tailwind CLI while the Docker image builds (see Dockerfile), which
 *    writes static/css/tailwind.css from the classes in templates/ and apps/;
 *  - in the browser by the Tailwind Play CDN, the fallback base.html uses when
 *    that file hasn't been built (bare-metal runs, or the repo bind-mounted
 *    into a dev container).
 *
 * The look follows Apple's TV app: true black, neutral greys (Apple's dark
 * system greys, not Tailwind's blue-tinted ones) and one accent colour.
 * Accent ("brand") colours are CSS variables so App Settings → Appearance can
 * switch theme.
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
        // Fills and backgrounds, lightest to darkest: 800 is a grouped cell
        // (secondarySystemBackground), 700 / 600 are controls on top of it.
        surface: {
          50:  '#f2f2f7',
          100: '#d1d1d6',
          200: '#aeaeb2',
          300: '#8e8e93',
          400: '#636366',
          500: '#48484a',
          600: '#3a3a3c',
          700: '#2c2c2e',
          800: '#1c1c1e',
          900: '#0e0e10',
          950: '#000000',
        },
        // Text: 100–300 primary, 400 secondary, 500 tertiary — each readable
        // (4.5:1+) on black and on surface-800 cells
        gray: {
          50:  '#f5f5f7',
          100: '#f2f2f7',
          200: '#e5e5ea',
          300: '#c7c7cc',
          400: '#a1a1a6',
          500: '#8e8e93',
          600: '#737378',
          700: '#48484a',
          800: '#2c2c2e',
          900: '#1c1c1e',
          950: '#0e0e10',
        },
      },
      fontFamily: {
        // SF Pro on Apple devices, Segoe UI on Windows, Roboto on Android
        sans: ['-apple-system', 'BlinkMacSystemFont', '"SF Pro Text"', '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'Arial', 'sans-serif'],
      },
      transitionTimingFunction: {
        // Close to the system spring: quick out, gentle settle
        spring: 'cubic-bezier(0.32, 0.72, 0, 1)',
      },
    },
  },
};

if (typeof module !== 'undefined') module.exports = ddTailwindConfig;
else tailwind.config = ddTailwindConfig;
