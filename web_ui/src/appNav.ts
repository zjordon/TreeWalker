import { createContext, useContext } from "react";

// P6 后续 I1：跨组件导航。RunView 的「活动技能」chip 用 openSkills(host) 跳到技能面并预选 host；
// AppShell 持有 mode/skillsHost，通过此 context 下传。
// T2 I5（M6）：model/setModel——TopBar 模型选择器持有，RunView 发任务时读取（仅对新任务生效；
// 空 = 跟随设置默认，设置面 LLM_MODEL 是「改默认」，两层并存 §8.9）。
// T2 I4（M8）：flowsName/openFlow——命令面板「打开流程」跳流程库并预选加载（仿 openSkills）。
export interface AppNav {
	mode: string;
	setMode: (m: string) => void;
	skillsHost: string | null;
	openSkills: (host?: string | null) => void;
	flowsName: string | null;
	openFlow: (name?: string | null) => void;
	model: string;
	setModel: (m: string) => void;
}

export const AppNavContext = createContext<AppNav | null>(null);

export function useAppNav(): AppNav | null {
	return useContext(AppNavContext);
}

// 模型静态候选（TopBar ModelPicker 与 ⌘K 面板共用；放这里避免 AppShell↔CommandPalette 循环依赖）
export const MODEL_PRESETS = ["glm-5.1", "glm-4-flash"];
