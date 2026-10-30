import React from "react";
import ReactDOM from "react-dom/client";
import { FleetDashboard } from "./FleetDashboard";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <FleetDashboard />
  </React.StrictMode>,
);
