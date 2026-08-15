/** Build from the project root:
 *    npm install tailwindcss@3.4.17
 *    npx tailwindcss -c build/tailwind.config.js -i build/tailwind.input.css \\
 *                    -o static/css/app.css --minify
 */
module.exports = {
  content: ["./templates/**/*.html",
            "./*.py"],
  theme: {
    extend: {
      colors: {
        ink: '#030920', dove: '#6C6C6C', silver: '#D6DBD5',
        sage: '#B0D48C', sagedeep: '#7FB65E', sagesoft: '#E7F1DB',
        canvas: '#F2F5EF',
      },
      fontFamily: { sans: ['Montserrat', 'system-ui', 'sans-serif'], mono: ['Montserrat', 'system-ui', 'sans-serif'] },
      borderRadius: { xl2: '1.1rem' },
    }
  },
}
