tailwind.config = {
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      colors: {
        cw: {
          blue: "#1e8bc3",
          blueHover: "#1574a8",
          blueLite: "#e8f4fb",
          blueMid: "#b8d9ef",
          blueText: "#1463a0",
          bg: "#f4f7fa",
          border: "#dce6f0",
          borderLow: "#edf3f8",
          text: "#2d3748",
          sub: "#4a6080",
          muted: "#7a90a8",
          placeholder: "#aabdd0",
          green: "#17a667",
          greenBg: "#e6f7f0",
          purple: "#6c4fd4",
          purpleBg: "#f0edfc",
        },
      },
      boxShadow: {
        soft: "0 2px 8px rgba(30,60,110,0.08), 0 1px 2px rgba(30,60,110,0.05)",
      },
    },
  },
};
