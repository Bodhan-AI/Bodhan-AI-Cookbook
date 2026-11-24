import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./lib/auth";
import { ModeProvider } from "./lib/mode";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ModeProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </ModeProvider>
    </BrowserRouter>
  </StrictMode>,
);
