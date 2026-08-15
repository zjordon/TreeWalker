import { createContext, useContext, useReducer } from "react";
import type { Dispatch, ReactNode } from "react";
import { initialLiveState, liveReducer } from "./liveReducer";
import type { LiveAction } from "./liveReducer";
import type { LiveState } from "./types";

// P6 后续 T2 H（M1）：live 任务状态提升到 AppShell 级 context。
// 原状：RunView 内部 useReducer，AppShell 条件渲染切模式即卸载组件 → state 全丢。
// 现状：reducer 唯一实例挂在 <LiveTaskProvider>（AppShell 放一层），RunView 变消费者——
// 切模式往返后 state 完整保留，SSE 由 RunView 的 useEffect（依赖 state.taskId）重订续播。
export interface LiveTaskCtx {
	state: LiveState;
	dispatch: Dispatch<LiveAction>;
}

export const LiveTaskContext = createContext<LiveTaskCtx | null>(null);

export function useLiveTask(): LiveTaskCtx {
	const ctx = useContext(LiveTaskContext);
	if (ctx === null) {
		throw new Error("useLiveTask 必须在 <LiveTaskProvider> 内使用");
	}
	return ctx;
}

// 持有唯一 live reducer 实例。放 AppShell 内（shell 主体外层），跨模式共享。
export function LiveTaskProvider({ children }: { children: ReactNode }) {
	const [state, dispatch] = useReducer(liveReducer, initialLiveState);
	return <LiveTaskContext.Provider value={{ state, dispatch }}>{children}</LiveTaskContext.Provider>;
}
