import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Result } from "antd";

interface Props {
  children: ReactNode;
  /** 局部边界（如消息列表）用轻量文案，整树边界用重文案 */
  scope?: "root" | "local";
}

interface State {
  error: Error | null;
}

/**
 * 渲染异常边界（A2）：任何组件抛错不再白屏整树。
 * 根级兜住全部路由；消息列表再包一层局部边界，聊天区域崩溃时壳与输入框仍可用。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 结构化留痕：控制台可查（后续可接入日志上报）
    console.error("[ErrorBoundary]", this.props.scope ?? "root", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    if (this.props.scope === "local") {
      return (
        <div className="error-boundary-local" role="alert">
          <div>消息渲染出现异常，其余功能不受影响。</div>
          <Button size="small" onClick={() => this.setState({ error: null })}>
            重试渲染
          </Button>
        </div>
      );
    }
    return (
      <Result
        status="error"
        title="页面出现异常"
        subTitle={this.state.error.message || "渲染过程中发生未知错误"}
        extra={
          <Button type="primary" onClick={() => window.location.reload()}>
            重新加载
          </Button>
        }
      />
    );
  }
}
