import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { TokensPage } from "./TokensPage";

describe("/dev/tokens 令牌页", () => {
  beforeEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.density;
  });

  it("展示色板、字阶、间距、组件四个分区", () => {
    render(<TokensPage />);
    for (const name of ["色彩", "字阶", "间距与圆角", "组件"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
  });

  it("色板覆盖全部语义色令牌", () => {
    render(<TokensPage />);
    for (const token of ["--up", "--down", "--warn", "--bg-canvas", "--text-primary"]) {
      expect(screen.getByText(token)).toBeInTheDocument();
    }
  });

  it("组件区同时展示模拟盘与实盘徽标", () => {
    render(<TokensPage />);
    expect(screen.getByText("模拟盘")).toBeInTheDocument();
    expect(screen.getByText("实盘")).toBeInTheDocument();
  });

  it("四态面板全部展示", () => {
    render(<TokensPage />);
    expect(screen.getByRole("status", { name: "加载中" })).toBeInTheDocument();
    expect(screen.getByText("暂无持仓")).toBeInTheDocument();
    // 页面上还有一个输入框校验错误，同样是 alert，因此按文案定位面板错误
    expect(screen.getByText("接口超时（8s）")).toBeInTheDocument();
    expect(screen.getByText("BTC")).toBeInTheDocument();
  });

  it("密度切换器改变根节点 data-density", async () => {
    render(<TokensPage />);
    await userEvent.click(screen.getByRole("radio", { name: "舒适" }));
    expect(document.documentElement.dataset.density).toBe("comfortable");
    await userEvent.click(screen.getByRole("radio", { name: "紧凑" }));
    expect(document.documentElement.dataset.density).toBe("compact");
  });
});
