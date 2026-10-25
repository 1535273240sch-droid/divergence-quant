import { type ReactNode, Component, type ErrorInfo } from "react";
import { Button } from "./Button";

export type PanelState = "loading" | "empty" | "error" | "ready";

export interface PanelProps {
  title: string;
  state: PanelState;
  children?: ReactNode;
  emptyText?: string;
  emptyAction?: { label: string; onClick: () => void };
  errorMessage?: string;
  onRetry?: () => void;
  /** 标题右侧的操作区 */
  actions?: ReactNode;
}

function ErrorView({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="panel-error" role="alert">
      <p>{message}</p>
      {onRetry ? (
        <Button size="sm" onClick={onRetry}>
          重试
        </Button>
      ) : null}
    </div>
  );
}

interface BoundaryProps {
  children: ReactNode;
  onRetry?: () => void;
}

interface BoundaryState {
  error: Error | null;
}

/** 每个面板独立的错误边界：单个面板失败不影响其他面板，不整页白屏 */
class PanelErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[Panel] 渲染异常", error, info.componentStack);
  }

  private retry = () => {
    this.setState({ error: null });
    this.props.onRetry?.();
  };

  render() {
    if (this.state.error) {
      return <ErrorView message={this.state.error.message || "渲染失败"} onRetry={this.retry} />;
    }
    return this.props.children;
  }
}

/** 数据面板：加载（骨架）/ 空 / 错误 / 正常 四态（F7） */
export function Panel({
  title,
  state,
  children,
  emptyText = "暂无数据",
  emptyAction,
  errorMessage = "加载失败",
  onRetry,
  actions,
}: PanelProps) {
  let body: ReactNode;
  switch (state) {
    case "loading":
      body = (
        <div className="panel-skeleton" role="status" aria-label="加载中" aria-busy="true">
          <span />
          <span />
          <span />
        </div>
      );
      break;
    case "empty":
      body = (
        <div className="panel-empty">
          <p>{emptyText}</p>
          {emptyAction ? (
            <Button size="sm" onClick={emptyAction.onClick}>
              {emptyAction.label}
            </Button>
          ) : null}
        </div>
      );
      break;
    case "error":
      body = <ErrorView message={errorMessage} onRetry={onRetry} />;
      break;
    case "ready":
      body = <PanelErrorBoundary onRetry={onRetry}>{children}</PanelErrorBoundary>;
      break;
    default:
      throw new Error(`未知面板状态: ${String(state)}`);
  }

  return (
    <section className="panel panel-box" data-state={state}>
      <div className="panel-header">
        <h2>{title}</h2>
        {actions ? <div className="panel-actions">{actions}</div> : null}
      </div>
      <div className="panel-body">{body}</div>
    </section>
  );
}
