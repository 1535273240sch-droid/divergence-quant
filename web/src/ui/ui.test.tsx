import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  Button,
  Input,
  ModeBadge,
  Num,
  Panel,
  Segmented,
  StatusDot,
  Symbol,
  Tag,
  formatNumber,
} from "./index";

describe("formatNumber", () => {
  it("固定小数位", () => {
    expect(formatNumber("186.4", { digits: 2 })).toBe("186.40");
  });

  it("带符号与单位（§17.13）", () => {
    expect(formatNumber("186.4", { digits: 2, signed: true, unit: "USDT" })).toBe("+186.40 USDT");
    expect(formatNumber("-1.5", { digits: 2, signed: true, unit: "%" })).toBe("-1.50%");
  });

  it("零不带符号", () => {
    expect(formatNumber("0", { digits: 2, signed: true })).toBe("0.00");
  });

  it("空值显示占位符", () => {
    expect(formatNumber(null)).toBe("—");
    expect(formatNumber(undefined)).toBe("—");
    expect(formatNumber("")).toBe("—");
  });

  it("非法输入抛出明确错误，不静默", () => {
    expect(() => formatNumber("abc")).toThrow(/无法格式化数值/);
    expect(() => formatNumber("NaN")).toThrow(/无法格式化数值/);
    expect(() => formatNumber("Infinity")).toThrow(/无法格式化数值/);
  });

  it("小数位非法时抛错", () => {
    expect(() => formatNumber("1", { digits: -1 })).toThrow(/digits/);
    expect(() => formatNumber("1", { digits: 1.5 })).toThrow(/digits/);
  });

  it("大数与千分位", () => {
    expect(formatNumber("1234567.891", { digits: 2, group: true })).toBe("1,234,567.89");
  });
});

describe("Num", () => {
  it("带 num 类（等宽数字），按涨跌着色", () => {
    render(<Num value="12.5" digits={1} signed colorize data-testid="n" />);
    const el = screen.getByTestId("n");
    expect(el).toHaveClass("num");
    expect(el).toHaveAttribute("data-tone", "up");
    expect(el).toHaveTextContent("+12.5");
  });

  it("负数为 down，零与空为 flat", () => {
    const { rerender } = render(<Num value="-3" colorize data-testid="n" />);
    expect(screen.getByTestId("n")).toHaveAttribute("data-tone", "down");
    rerender(<Num value="0" colorize data-testid="n" />);
    expect(screen.getByTestId("n")).toHaveAttribute("data-tone", "flat");
    rerender(<Num value={null} colorize data-testid="n" />);
    expect(screen.getByTestId("n")).toHaveAttribute("data-tone", "flat");
  });

  it("不着色时没有 data-tone", () => {
    render(<Num value="1" data-testid="n" />);
    expect(screen.getByTestId("n")).not.toHaveAttribute("data-tone");
  });
});

describe("Button", () => {
  it("默认 type=button，避免误提交表单", () => {
    render(<Button>确认</Button>);
    expect(screen.getByRole("button", { name: "确认" })).toHaveAttribute("type", "button");
  });

  it("variant 与 size 反映到 data 属性", () => {
    render(
      <Button variant="long" size="lg">
        开多
      </Button>,
    );
    const btn = screen.getByRole("button", { name: "开多" });
    expect(btn).toHaveAttribute("data-variant", "long");
    expect(btn).toHaveAttribute("data-size", "lg");
  });

  it("点击触发回调", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>点击</Button>);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("loading 时禁用并标记 aria-busy，重复点击不触发（幂等）", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        提交
      </Button>,
    );
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-busy", "true");
    await userEvent.click(btn);
    await userEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("disabled 时不触发", async () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        禁用
      </Button>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe("Tag / StatusDot", () => {
  it("Tag 使用语义色调", () => {
    render(<Tag tone="up">底背离</Tag>);
    expect(screen.getByText("底背离")).toHaveAttribute("data-tone", "up");
  });

  it("Tag 默认中性", () => {
    render(<Tag>永续</Tag>);
    expect(screen.getByText("永续")).toHaveAttribute("data-tone", "neutral");
  });

  it("StatusDot 暴露可读状态文字", () => {
    render(<StatusDot status="connecting" />);
    const dot = screen.getByRole("img", { name: "连接中" });
    expect(dot).toHaveAttribute("data-status", "connecting");
  });

  it.each([
    ["connected", "已连接"],
    ["error", "异常"],
    ["idle", "未连接"],
  ] as const)("StatusDot %s → %s", (status, label) => {
    render(<StatusDot status={status} />);
    expect(screen.getByRole("img", { name: label })).toBeInTheDocument();
  });
});

describe("ModeBadge（安全关键，F3）", () => {
  it("模拟盘", () => {
    render(<ModeBadge mode="paper" />);
    const badge = screen.getByText("模拟盘");
    expect(badge).toHaveAttribute("data-mode", "paper");
  });

  it("实盘", () => {
    render(<ModeBadge mode="live" />);
    const badge = screen.getByText("实盘");
    expect(badge).toHaveAttribute("data-mode", "live");
  });

  it("非法模式抛错，防止被误认为模拟盘", () => {
    // @ts-expect-error 故意传入非法值
    expect(() => render(<ModeBadge mode="demo" />)).toThrow(/未知交易模式/);
  });
});

describe("Symbol（§17.3.2 币种显示）", () => {
  it("永续合约拆分基础币、计价币与永续标签", () => {
    render(<Symbol instId="BTC-USDT-SWAP" />);
    expect(screen.getByText("BTC")).toHaveClass("symbol-base");
    expect(screen.getByText("-USDT")).toHaveClass("symbol-quote");
    expect(screen.getByText("永续")).toBeInTheDocument();
  });

  it("现货不显示永续标签", () => {
    render(<Symbol instId="ETH-USDT" />);
    expect(screen.getByText("ETH")).toBeInTheDocument();
    expect(screen.queryByText("永续")).toBeNull();
  });

  it.each(["", "BTC", "BTC-USDT-FOO", "a-b-c-d"])("非法 instId %j 抛错", (id) => {
    expect(() => render(<Symbol instId={id} />)).toThrow(/instId/);
  });
});

describe("Panel 四态（F7）", () => {
  it("ready 渲染子内容", () => {
    render(
      <Panel title="持仓" state="ready">
        <p>数据行</p>
      </Panel>,
    );
    expect(screen.getByText("数据行")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "持仓" })).toBeInTheDocument();
  });

  it("loading 显示骨架并不渲染子内容", () => {
    render(
      <Panel title="持仓" state="loading">
        <p>数据行</p>
      </Panel>,
    );
    expect(screen.getByRole("status", { name: "加载中" })).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText("数据行")).toBeNull();
  });

  it("empty 显示一行文字与可选操作，不放插画", () => {
    const onAction = vi.fn();
    render(
      <Panel title="持仓" state="empty" emptyText="暂无持仓" emptyAction={{ label: "去选币", onClick: onAction }} />,
    );
    expect(screen.getByText("暂无持仓")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "去选币" }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(document.querySelector("img, svg")).toBeNull();
  });

  it("empty 缺省文案", () => {
    render(<Panel title="持仓" state="empty" />);
    expect(screen.getByText("暂无数据")).toBeInTheDocument();
  });

  it("error 显示原因与重试按钮", () => {
    const onRetry = vi.fn();
    render(<Panel title="持仓" state="error" errorMessage="接口超时（8s）" onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("接口超时（8s）");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("error 无重试回调时不显示重试按钮", () => {
    render(<Panel title="持仓" state="error" errorMessage="无权限" />);
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  });

  it("error 缺省原因时给出明确文案，不留白", () => {
    render(<Panel title="持仓" state="error" />);
    expect(screen.getByRole("alert")).toHaveTextContent("加载失败");
  });

  it("子组件渲染异常被面板内错误边界捕获，不整页白屏", () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    function Boom(): never {
      throw new Error("渲染炸了");
    }
    render(
      <div>
        <Panel title="A" state="ready">
          <Boom />
        </Panel>
        <Panel title="B" state="ready">
          <p>B 正常</p>
        </Panel>
      </div>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("渲染炸了");
    expect(screen.getByText("B 正常")).toBeInTheDocument();
    errSpy.mockRestore();
  });

  it("出错后重试可恢复", () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    let shouldThrow = true;
    function Flaky() {
      if (shouldThrow) throw new Error("暂时失败");
      return <p>恢复了</p>;
    }
    render(
      <Panel title="A" state="ready">
        <Flaky />
      </Panel>,
    );
    shouldThrow = false;
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(screen.getByText("恢复了")).toBeInTheDocument();
    errSpy.mockRestore();
  });
});

describe("Input", () => {
  it("label 与输入框关联", () => {
    render(<Input label="止损价" />);
    expect(screen.getByLabelText("止损价")).toBeInTheDocument();
  });

  it("numeric 右对齐并带 inputMode", () => {
    render(<Input label="数量" numeric />);
    const el = screen.getByLabelText("数量");
    expect(el).toHaveAttribute("data-numeric", "true");
    expect(el).toHaveAttribute("inputmode", "decimal");
  });

  it("error 时标记 aria-invalid 并展示文案", () => {
    render(<Input label="数量" error="需大于 0" />);
    expect(screen.getByLabelText("数量")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("需大于 0");
  });

  it("输入触发 onChange", async () => {
    const onChange = vi.fn();
    render(<Input label="数量" onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("数量"), "12");
    expect(onChange).toHaveBeenCalledTimes(2);
  });
});

describe("Segmented", () => {
  const options = [
    { value: "1H", label: "1H" },
    { value: "4H", label: "4H" },
    { value: "1D", label: "1D" },
  ];

  it("渲染 radiogroup 并标记选中项", () => {
    render(<Segmented ariaLabel="周期" options={options} value="4H" onChange={() => {}} />);
    expect(screen.getByRole("radiogroup", { name: "周期" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "4H" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "1H" })).toHaveAttribute("aria-checked", "false");
  });

  it("点击切换", async () => {
    const onChange = vi.fn();
    render(<Segmented ariaLabel="周期" options={options} value="4H" onChange={onChange} />);
    await userEvent.click(screen.getByRole("radio", { name: "1D" }));
    expect(onChange).toHaveBeenCalledWith("1D");
  });

  it("点击已选项不重复触发", async () => {
    const onChange = vi.fn();
    render(<Segmented ariaLabel="周期" options={options} value="4H" onChange={onChange} />);
    await userEvent.click(screen.getByRole("radio", { name: "4H" }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("方向键切换并循环", () => {
    const onChange = vi.fn();
    render(<Segmented ariaLabel="周期" options={options} value="1D" onChange={onChange} />);
    fireEvent.keyDown(screen.getByRole("radio", { name: "1D" }), { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("1H");
    fireEvent.keyDown(screen.getByRole("radio", { name: "1D" }), { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("4H");
  });

  it("只有选中项在 Tab 序列中（roving tabindex）", () => {
    render(<Segmented ariaLabel="周期" options={options} value="4H" onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: "4H" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: "1H" })).toHaveAttribute("tabindex", "-1");
  });

  it("options 为空时抛错", () => {
    expect(() => render(<Segmented ariaLabel="x" options={[]} value="a" onChange={() => {}} />)).toThrow(/options/);
  });

  it("value 不在 options 中时抛错", () => {
    expect(() => render(<Segmented ariaLabel="x" options={options} value="5M" onChange={() => {}} />)).toThrow(
      /value/,
    );
  });
});
