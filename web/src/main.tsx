import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { AppRoutes } from "./app/AppRoutes";
import { applyDensity, loadDensity } from "./ui/density";
import "./styles/base.css";
import "./ui/ui.css";
import "./app/app.css";
import "./dev/tokens-page.css";

// 先应用已保存的密度，避免首屏闪烁
applyDensity(loadDensity());

const container = document.getElementById("root");
if (!container) {
  throw new Error("找不到 #root 挂载点");
}

// 使用 HashRouter：web/ 下仍保留原型 index.html，History 回退会命中原型页面
createRoot(container).render(
  <StrictMode>
    <HashRouter>
      <AppRoutes />
    </HashRouter>
  </StrictMode>,
);
