import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";   // change to ./App.jsx if needed
import "./index.css";      // delete this line if you don't have index.css

const root = ReactDOM.createRoot(document.getElementById("root"));

root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
