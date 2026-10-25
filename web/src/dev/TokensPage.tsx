import { useState } from "react";
import { useAppStore } from "../app/store";
import { Button, Input, ModeBadge, Num, Panel, Segmented, StatusDot, SymbolLabel, Tag } from "../ui";

const SWATCHES: { group: string; tokens: string[] }[] = [
  { group: "表面", tokens: ["--bg-canvas", "--bg-surface-1", "--bg-surface-2", "--bg-surface-3"] },
  { group: "文字", tokens: ["--text-primary", "--text-secondary", "--text-tertiary", "--text-disabled"] },
  { group: "语义", tokens: ["--up", "--down", "--neutral-regime", "--warn"] },
];

const TYPE_SCALE: { token: string; label: string; sample: string }[] = [
  { token: "--fs-display", label: "display", sample: "12,480.52" },
  { token: "--fs-h1", label: "h1", sample: "页面标题" },
  { token: "--fs-h2", label: "h2", sample: "面板标题" },
  { token: "--fs-body", label: "body", sample: "正文 BTC-USDT 183.20" },
  { token: "--fs-small", label: "small", sample: "辅助信息 10-24 14:00" },
  { token: "--fs-label", label: "label", sample: "SYMBOL  PRICE  CHANGE" },
  { token: "--fs-price-lg", label: "price-lg", sample: "67,214.30" },
];

const SPACING = ["--sp-1", "--sp-2", "--sp-3", "--sp-4", "--sp-5", "--sp-6", "--sp-7", "--sp-8"];
const RADII = ["--r-sm", "--r-md", "--r-lg", "--r-xl"];

const DENSITY_OPTIONS = [
  { value: "compact", label: "紧凑" },
  { value: "comfortable", label: "舒适" },
];

/** /dev/tokens：设计令牌验收页（T9.2a），仅供开发与审美验收 */
export function TokensPage() {
  const density = useAppStore((s) => s.density);
  const setDensity = useAppStore((s) => s.setDensity);
  const [timeframe, setTimeframe] = useState("4H");

  return (
    <div className="tokens-page">
      <div className="tokens-header">
        <h1>设计令牌</h1>
        <Segmented
          ariaLabel="密度"
          options={DENSITY_OPTIONS}
          value={density}
          onChange={(v) => setDensity(v === "comfortable" ? "comfortable" : "compact")}
        />
      </div>

      <section className="tokens-section">
        <h2>色彩</h2>
        {SWATCHES.map((g) => (
          <div key={g.group} className="swatch-row" aria-label={g.group}>
            {g.tokens.map((t) => (
              <div key={t} className="swatch">
                <span className="swatch-chip" style={{ background: `var(${t})` }} />
                <code>{t}</code>
              </div>
            ))}
          </div>
        ))}
      </section>

      <section className="tokens-section">
        <h2>字阶</h2>
        {TYPE_SCALE.map((s) => (
          <div key={s.token} className="type-row">
            <code>{s.token}</code>
            <span className="num" style={{ fontSize: `var(${s.token})` }}>
              {s.sample}
            </span>
          </div>
        ))}
      </section>

      <section className="tokens-section">
        <h2>间距与圆角</h2>
        <div className="swatch-row">
          {SPACING.map((t) => (
            <div key={t} className="swatch">
              <span className="space-bar" style={{ width: `var(${t})` }} />
              <code>{t}</code>
            </div>
          ))}
        </div>
        <div className="swatch-row">
          {RADII.map((t) => (
            <div key={t} className="swatch">
              <span className="radius-chip" style={{ borderRadius: `var(${t})` }} />
              <code>{t}</code>
            </div>
          ))}
        </div>
      </section>

      <section className="tokens-section">
        <h2>组件</h2>
        <div className="demo-row">
          <ModeBadge mode="paper" />
          <ModeBadge mode="live" />
          <StatusDot status="connected" />
          <StatusDot status="connecting" />
          <StatusDot status="error" />
          <Tag tone="up">底背离</Tag>
          <Tag tone="down">顶背离</Tag>
          <Tag>震荡</Tag>
        </div>
        <div className="demo-row">
          <Button variant="primary">确认下单</Button>
          <Button>次级按钮</Button>
          <Button variant="ghost">幽灵按钮</Button>
          <Button variant="long">开多</Button>
          <Button variant="short">开空</Button>
          <Button variant="danger">全部平仓</Button>
          <Button loading>提交中</Button>
        </div>
        <div className="demo-row">
          <Segmented
            ariaLabel="周期"
            options={[
              { value: "1H", label: "1H" },
              { value: "4H", label: "4H" },
              { value: "1D", label: "1D" },
            ]}
            value={timeframe}
            onChange={setTimeframe}
          />
          <Input label="数量" numeric defaultValue="1.20" />
          <Input label="止损价" numeric error="需低于开仓价" defaultValue="190.5" />
        </div>
        <div className="demo-row">
          <SymbolLabel instId="BTC-USDT-SWAP" />
          <Num value="186.4" digits={2} signed unit="USDT" colorize />
          <Num value="-1.52" digits={2} signed unit="%" colorize />
        </div>
        <div className="panel-grid">
          <Panel title="持仓" state="ready">
            <Num value="12480.5" digits={2} group unit="USDT" />
          </Panel>
          <Panel title="加载态" state="loading" />
          <Panel title="空态" state="empty" emptyText="暂无持仓" emptyAction={{ label: "去选币", onClick: () => {} }} />
          <Panel title="错误态" state="error" errorMessage="接口超时（8s）" onRetry={() => {}} />
        </div>
      </section>
    </div>
  );
}
