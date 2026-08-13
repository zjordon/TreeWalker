import { createContext, useContext } from "react";

// P6 后续 I1：跨组件导航。RunView 的「活动技能」chip 用 openSkills(host) 跳到技能面并预选 host；
// AppShell 持有 mode/skillsHost，通过此 context 下传。
export interface AppNav {
	mode: string;
	setMode: (m: string) => void;
	skillsHost: string | null;
	openSkills: (host?: string | null) => void;
}

export const AppNavContext = createContext<AppNav | null>(null);

export function useAppNav(): AppNav | null {
	return useContext(AppNavContext);
}
