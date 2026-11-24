/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        clay: { deep: "#713D31", DEFAULT: "#985541", soft: "#B8755D", muted: "#D5A18B", pale: "#E8CFC0" },
        sand: "#F3E7DC",
        ivory: "#FAF7F2",
        line: "#E9DDD3",
        mute: "#8B756B",
      },
      fontFamily: {
        serif: ['"Fraunces"', '"Noto Serif Telugu"', '"Noto Serif Devanagari"', "Georgia", "serif"],
        sans: ['"Inter"', '"Noto Sans Telugu"', '"Noto Sans Devanagari"', '"Nirmala UI"', "system-ui", "sans-serif"],
      },
      boxShadow: { paper: "0 1px 0 #E9DDD3, 0 12px 28px -18px rgba(113,61,49,.35)" },
    },
  },
  plugins: [],
};
