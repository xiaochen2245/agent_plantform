/** jsdom 环境下 AntD 依赖的浏览器 API stub。 */

if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  // Chat 页滚动到底部依赖；jsdom 未实现（缺失会导致 React 卸载整树）
  Element.prototype.scrollIntoView = () => undefined;
}

// jsdom 的 AbortSignal 与 Node undici fetch 不同 realm：直接传会抛
// "Expected signal to be an instance of AbortSignal"。
// MSW 的 fetch 拦截器在 server.listen() 时包最外层（内部构造 Request 先校验），
// 所以流式测试需在 listen() 之后两调 installSignalSafeFetch() 使重试层成为最外层。
export function installSignalSafeFetch(): void {
  if (typeof window === "undefined" || typeof globalThis.fetch !== "function") return;
  const nativeFetch = globalThis.fetch.bind(globalThis);
  if ((globalThis.fetch as { __signalSafe?: boolean }).__signalSafe) return;
  const wrapped = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    try {
      return await nativeFetch(input, init);
    } catch (e) {
      if (
        init?.signal &&
        (e as { name?: string }).name === "TypeError" &&
        /signal/i.test(String((e as Error).message))
      ) {
        return nativeFetch(input, { ...init, signal: undefined });
      }
      throw e;
    }
  }) as typeof fetch;
  (wrapped as { __signalSafe?: boolean }).__signalSafe = true;
  globalThis.fetch = wrapped;
}

installSignalSafeFetch();

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

if (typeof globalThis !== "undefined" && !(globalThis as { ResizeObserver?: unknown }).ResizeObserver) {
  class ResizeObserverStub {
    observe(): void {
      /* noop */
    }
    unobserve(): void {
      /* noop */
    }
    disconnect(): void {
      /* noop */
    }
  }
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
}
