import { setupWorker } from "msw/browser";
import { handlers } from "./handlers";

/** 仅 dev 模式启用（main.tsx 中按需动态加载）。 */
export const worker = setupWorker(...handlers);
