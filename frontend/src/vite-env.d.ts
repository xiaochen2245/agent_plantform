/// <reference types="vite/client" />

// 构建期注入（vite define，来源 env BUILD_SHA；缺省 'dev' 不参与陈旧比对）
declare const __BUILD_SHA__: string;
