import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { AppRoutes } from "./AppRoutes";
import { initialAppState, useAppStore } from "./store";

function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

function topNav() {
  return screen.getByRole("navigation", { name: "主导航" });
}

describe("AppShell 布局", () => {
  beforeEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.density;
    useAppStore.setState(initialAppState());
  });

  it("顶栏包含字标、7 项主导航、⌘K 入口、模式徽标", () => {
    renderApp();
    expect(screen.getByLabelText("DivergenceQuant")).toBeInTheDocument();
    expect(within(topNav()).getAllByRole("link")).toHaveLength(7);
    expect(screen.getByRole("button", { name: /搜索|命令/ })).toBeInTheDocument();
    expect(screen.getByText("模拟盘")).toBeInTheDocument();
  });

  it("当前页导航项 aria-current=page，其余没有", () => {
    renderApp("/positions");
    expect(within(topNav()).getByRole("link", { name: "持仓" })).toHaveAttribute("aria-current", "page");
    expect(within(topNav()).getByRole("link", { name: "总览" })).not.toHaveAttribute("aria-current");
  });

  it("总览仅在根路径激活，不会因前缀匹配常亮", () => {
    renderApp("/risk");
    expect(within(topNav()).getByRole("link", { name: "总览" })).not.toHaveAttribute("aria-current");
  });

  it("工作台子路径 /workspace/:instId 仍高亮工作台", () => {
    renderApp("/workspace/BTC-USDT-SWAP");
    expect(within(topNav()).getByRole("link", { name: "工作台" })).toHaveAttribute("aria-current", "page");
  });

  it("点击导航切换页面内容", async () => {
    renderApp();
    await userEvent.click(within(topNav()).getByRole("link", { name: "风控" }));
    expect(screen.getByRole("heading", { level: 1, name: "风控" })).toBeInTheDocument();
  });

  it("未知路径显示 404 而不是白屏", () => {
    renderApp("/no-such-page");
    expect(screen.getByRole("heading", { level: 1, name: "页面不存在" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回总览" })).toHaveAttribute("href", "/");
  });

  it("未实现的业务页明确显示建设中，不伪造数据", () => {
    renderApp("/screener");
    expect(screen.getByText(/页面建设中/)).toBeInTheDocument();
  });

  it("令牌页路由可达", () => {
    renderApp("/dev/tokens");
    expect(screen.getByRole("heading", { level: 1, name: "设计令牌" })).toBeInTheDocument();
  });

  it("手机底部标签栏有 4 项", () => {
    renderApp();
    const tabs = screen.getByRole("navigation", { name: "底部标签栏" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual(
      expect.arrayContaining(["信号", "待确认", "持仓", "我的"]),
    );
  });

  it("待确认数量 > 0 时标签栏显示数量提示", () => {
    useAppStore.getState().setPendingConfirmCount(2);
    renderApp();
    const tabs = screen.getByRole("navigation", { name: "底部标签栏" });
    expect(within(tabs).getByRole("link", { name: /待确认/ })).toHaveTextContent("2");
  });
});

describe("模式识别（F3，安全关键）", () => {
  beforeEach(() => {
    useAppStore.setState(initialAppState());
  });

  it("模拟盘：顶栏 data-mode=paper", () => {
    renderApp();
    expect(screen.getByRole("banner")).toHaveAttribute("data-mode", "paper");
  });

  it("实盘：顶栏 data-mode=live，且显示实盘徽标", () => {
    useAppStore.getState().setMode("live");
    renderApp();
    expect(screen.getByRole("banner")).toHaveAttribute("data-mode", "live");
    expect(screen.getByText("实盘")).toBeInTheDocument();
    expect(screen.queryByText("模拟盘")).toBeNull();
  });
});

describe("状态栏", () => {
  beforeEach(() => {
    useAppStore.setState(initialAppState());
  });

  it("显示连接、风控、版本", () => {
    renderApp();
    const bar = screen.getByRole("contentinfo");
    expect(bar).toHaveTextContent("行情未连接");
    expect(bar).toHaveTextContent("风控 正常");
    expect(bar).toHaveTextContent(/v\d+\.\d+\.\d+/);
  });

  it("已连接时显示延迟", () => {
    useAppStore.getState().setConnection("connected", 32);
    renderApp();
    expect(screen.getByRole("contentinfo")).toHaveTextContent("行情已连接");
    expect(screen.getByRole("banner")).toHaveTextContent("32ms");
  });

  it("风控异常变为可点击链接并跳转风控页", async () => {
    useAppStore.getState().setRisk("restricted");
    renderApp();
    const link = within(screen.getByRole("contentinfo")).getByRole("link", { name: /限制开仓/ });
    expect(link).toHaveAttribute("href", "/risk");
    await userEvent.click(link);
    expect(screen.getByRole("heading", { level: 1, name: "风控" })).toBeInTheDocument();
  });

  it("熔断状态展示", () => {
    useAppStore.getState().setRisk("halted");
    renderApp();
    expect(screen.getByRole("contentinfo")).toHaveTextContent("熔断");
  });
});

describe("命令面板（⌘K / Ctrl+K）", () => {
  beforeEach(() => {
    useAppStore.setState(initialAppState());
  });

  it("Ctrl+K 打开，再按关闭", () => {
    renderApp();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog", { name: "命令面板" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("⌘K（metaKey）同样有效", () => {
    renderApp();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.getByRole("dialog", { name: "命令面板" })).toBeInTheDocument();
  });

  it("点击顶栏入口打开", async () => {
    renderApp();
    await userEvent.click(screen.getByRole("button", { name: /搜索|命令/ }));
    expect(screen.getByRole("dialog", { name: "命令面板" })).toBeInTheDocument();
  });

  it("Esc 关闭", async () => {
    renderApp();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("点击遮罩关闭", async () => {
    renderApp();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await userEvent.click(screen.getByTestId("palette-overlay"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("选择页面命令后跳转并关闭", async () => {
    renderApp();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await userEvent.click(screen.getByRole("option", { name: /回测/ }));
    expect(screen.getByRole("heading", { level: 1, name: "回测" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("输入关键字过滤命令", async () => {
    renderApp();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await userEvent.type(screen.getByRole("combobox"), "风控");
    expect(screen.getByRole("option", { name: /风控/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /回测/ })).toBeNull();
  });

  it("无匹配项时显示空态文案", async () => {
    renderApp();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await userEvent.type(screen.getByRole("combobox"), "zzzzqq");
    expect(screen.getByText("没有匹配的命令")).toBeInTheDocument();
  });

  it("命令面板包含切换密度命令并生效", async () => {
    renderApp();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await userEvent.click(screen.getByRole("option", { name: /切换密度/ }));
    expect(useAppStore.getState().density).toBe("comfortable");
  });
});

describe("全局快捷键（§17.12）", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState(initialAppState());
  });

  it("G 然后 P 跳转持仓", () => {
    renderApp();
    fireEvent.keyDown(window, { key: "g" });
    fireEvent.keyDown(window, { key: "p" });
    expect(screen.getByRole("heading", { level: 1, name: "持仓" })).toBeInTheDocument();
  });

  it("G 然后 R 跳转风控，随后再单按 R 不会再次跳转", () => {
    renderApp("/positions");
    fireEvent.keyDown(window, { key: "g" });
    fireEvent.keyDown(window, { key: "r" });
    expect(screen.getByRole("heading", { level: 1, name: "风控" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "p" });
    expect(screen.getByRole("heading", { level: 1, name: "风控" })).toBeInTheDocument();
  });

  it("G 后输入未知键，序列作废", () => {
    renderApp("/risk");
    fireEvent.keyDown(window, { key: "g" });
    fireEvent.keyDown(window, { key: "x" });
    fireEvent.keyDown(window, { key: "p" });
    expect(screen.getByRole("heading", { level: 1, name: "风控" })).toBeInTheDocument();
  });

  it("G 序列超时后失效", () => {
    let now = 1000;
    const spy = vi.spyOn(performance, "now").mockImplementation(() => now);
    renderApp("/risk");
    fireEvent.keyDown(window, { key: "g" });
    now += 2000;
    fireEvent.keyDown(window, { key: "p" });
    expect(screen.getByRole("heading", { level: 1, name: "风控" })).toBeInTheDocument();
    spy.mockRestore();
  });

  it("D 切换密度", () => {
    renderApp();
    fireEvent.keyDown(window, { key: "d" });
    expect(useAppStore.getState().density).toBe("comfortable");
    fireEvent.keyDown(window, { key: "d" });
    expect(useAppStore.getState().density).toBe("compact");
  });

  it("在输入框内打字时不触发快捷键", () => {
    renderApp("/dev/tokens");
    const input = screen.getAllByRole("textbox")[0];
    input.focus();
    fireEvent.keyDown(input, { key: "d" });
    fireEvent.keyDown(input, { key: "g" });
    fireEvent.keyDown(input, { key: "p" });
    expect(useAppStore.getState().density).toBe("compact");
    expect(screen.getByRole("heading", { level: 1, name: "设计令牌" })).toBeInTheDocument();
  });

  it("带 Ctrl/Meta/Alt 修饰键的组合不触发单键快捷键", () => {
    renderApp();
    fireEvent.keyDown(window, { key: "d", ctrlKey: true });
    fireEvent.keyDown(window, { key: "d", altKey: true });
    expect(useAppStore.getState().density).toBe("compact");
  });

  it("命令面板打开时屏蔽页面快捷键", () => {
    renderApp();
    act(() => useAppStore.getState().openPalette());
    fireEvent.keyDown(window, { key: "d" });
    expect(useAppStore.getState().density).toBe("compact");
  });
});
