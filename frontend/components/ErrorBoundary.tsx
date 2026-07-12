'use client';
import { Component, ReactNode } from 'react';

interface Props { children: ReactNode; fallback?: ReactNode; }
interface State { hasError: boolean; error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };
  static getDerivedStateFromError(e: Error) { return { hasError: true, error: e }; }
  componentDidCatch(e: Error, info: any) { console.error('[ErrorBoundary]', e, info.componentStack); }
  render() {
    if (this.state.hasError) {
      return this.props.fallback || (
        <div className="p-4 m-2 border border-red-300 bg-red-50 rounded text-sm">
          <div className="font-bold text-red-700 mb-1">组件加载失败</div>
          <div className="text-red-600 text-xs">{this.state.error?.message}</div>
          <button className="mt-2 text-xs text-blue-600 underline"
            onClick={() => this.setState({ hasError: false, error: null })}>重试</button>
        </div>
      );
    }
    return this.props.children;
  }
}
