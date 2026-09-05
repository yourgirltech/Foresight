/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef4ff",
          100: "#d9e6ff",
          500: "#2f6bff",
          600: "#1e54e6",
          700: "#1a44b8",
        },
      },
    },
  },
  plugins: [],
};
