/**
 * Builds the stylesheet the app can serve itself, instead of compiling utility
 * classes in the browser on every page load.
 *
 * Why it is here: the Tailwind Play CDN is a development convenience by
 * Tailwind's own documentation. It costs a render-blocking request to a third
 * party — which matters on the connections agronomists and farmers actually
 * have — and it is the reason the Content-Security-Policy has to allow
 * 'unsafe-eval' and a third-party script origin at all.
 *
 * Build it with:
 *     npm install tailwindcss@3.4.17
 *     npx tailwindcss -c tailwind.config.js -i static/css/tailwind.src.css \
 *                     -o static/css/tailwind.css --minify
 *
 * Then serve it, and tighten the policy to match:
 *     TAILWIND_LOCAL=1  CSP_ALLOW_EVAL=0
 *
 * The output is committed, so nothing needs Node at deploy time.
 */
module.exports = {
  content: ["./templates/**/*.html", "./*.py"],
  theme: {
    extend: {
      colors: {
        ink: '#030920', dove: '#6C6C6C', silver: '#D6DBD5',
        sage: '#B0D48C', sagedeep: '#7FB65E', sagesoft: '#E7F1DB',
        canvas: '#F2F5EF',
      },
      fontFamily: {
        sans: ['Montserrat', 'system-ui', 'sans-serif'],
        mono: ['Montserrat', 'system-ui', 'sans-serif'],
      },
      borderRadius: { xl2: '1.1rem' },
    },
  },
};
