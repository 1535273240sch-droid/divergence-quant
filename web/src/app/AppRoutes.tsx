import { Link, Route, Routes } from "react-router-dom";
import { TokensPage } from "../dev/TokensPage";
import { Panel } from "../ui";
import { AppShell } from "./AppShell";
import { PAGES } from "./routes";

/** 业务页尚未实现时的占位：明确说明状态，不展示任何伪造数据 */
function PagePlaceholder({ title }: { title: string }) {
  return (
    <div className="page">
      <h1 className="page-title">{title}</h1>
      <Panel title="状态" state="empty" emptyText="页面建设中，待 T9.2c / T9.2d / T9.2e 实现" />
    </div>
  );
}

function NotFound() {
  return (
    <div className="page">
      <h1 className="page-title">页面不存在</h1>
      <Link to="/" className="page-link">
        返回总览
      </Link>
    </div>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        {PAGES.map((page) => {
          if (page.path === "/dev/tokens") {
            return <Route key={page.path} path={page.path} element={<TokensPage />} />;
          }
          return <Route key={page.path} path={page.path} element={<PagePlaceholder title={page.title} />} />;
        })}
        <Route path="/workspace/:instId" element={<PagePlaceholder title="工作台" />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
