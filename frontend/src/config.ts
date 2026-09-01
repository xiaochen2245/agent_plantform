/**
 * Mock 开关：VITE_USE_MOCKS !== "false" 时启用（默认开，保开发体验）。
 * 关闭方式：`npm run dev:real`（等价 VITE_USE_MOCKS=false vite）。
 * 生产构建（DEV=false）下 main.tsx 不会加载 MSW，无 mock 泄漏。
 */
export const USE_MOCKS: boolean = import.meta.env.VITE_USE_MOCKS !== "false";
