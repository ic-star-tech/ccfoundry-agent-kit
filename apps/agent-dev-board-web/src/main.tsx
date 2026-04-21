import React, { type ErrorInfo } from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

type BoundaryState = {
  error: Error | null;
};

class AppErrorBoundary extends React.Component<React.PropsWithChildren, BoundaryState> {
  state: BoundaryState = {
    error: null,
  };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("Agent Dev Board render failed.", error, errorInfo);
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }

    return (
      <div
        style={{
          minHeight: "100vh",
          padding: "32px",
          background: "#eef3fb",
          color: "#172033",
          fontFamily: "\"IBM Plex Sans\", \"Segoe UI\", sans-serif",
        }}
      >
        <div
          style={{
            maxWidth: "880px",
            margin: "0 auto",
            padding: "28px",
            borderRadius: "24px",
            border: "1px solid #d6deef",
            background: "rgba(255, 255, 255, 0.94)",
            boxShadow: "0 24px 60px rgba(30, 50, 100, 0.12)",
          }}
        >
          <p style={{ margin: "0 0 8px", fontSize: "0.82rem", fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "#66738d" }}>
            Frontend Error
          </p>
          <h1 style={{ margin: "0 0 12px", fontSize: "2rem" }}>Agent Dev Board failed to render</h1>
          <p style={{ margin: "0 0 16px", lineHeight: 1.6 }}>
            This usually means the browser hit an unexpected runtime error. Try a hard refresh first, and make sure you opened the exact <code>Web:</code> URL printed by <code>ccfoundry</code>.
          </p>
          <pre
            style={{
              margin: 0,
              padding: "16px",
              borderRadius: "18px",
              overflowX: "auto",
              background: "#172033",
              color: "#f6f9ff",
              fontFamily: "\"IBM Plex Mono\", monospace",
              fontSize: "0.95rem",
              lineHeight: 1.55,
              whiteSpace: "pre-wrap",
            }}
          >
            {this.state.error.stack || this.state.error.message}
          </pre>
        </div>
      </div>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </React.StrictMode>,
);
