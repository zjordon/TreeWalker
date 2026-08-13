import { useState, useCallback } from "react";
import type { ComponentType } from "react";
import RunView from "./components/RunView";
import FlowWorkspace from "./FlowWorkspace";
import SkillsShell from "./components/SkillsShell";
import { AppNavContext, type AppNav } from "./appNav";

// 注册表驱动的顶部模式（§4.2 机制 1）：技能/设置首期 disabled，后期改 enabled 即出现，不改 shell。
interface ModeDef {
	id: string;
	label: string;
	enabled: boolean;
	Component: ComponentType;
}

function SettingsPlaceholder() {
	return <div className="panel">设置面（后置，P6 首期未启用）</div>;
}

const MODES: ModeDef[] = [
	{ id: "explore", label: "探索", enabled: true, Component: RunView },
	{ id: "flows", label: "流程库", enabled: true, Component: FlowWorkspace },
	{ id: "skills", label: "技能", enabled: true, Component: SkillsShell },
	{ id: "settings", label: "设置", enabled: false, Component: SettingsPlaceholder },
];

export default function AppShell() {
	const [mode, setMode] = useState("explore");
	const [skillsHost, setSkillsHost] = useState<string | null>(null);

	// I1：从 RunView chip 跳到技能面并预选 host
	const openSkills = useCallback((host?: string | null) => {
		if (host) setSkillsHost(host);
		setMode("skills");
	}, []);

	const nav: AppNav = { mode, setMode, skillsHost, openSkills };
	const active = MODES.find((m) => m.id === mode) ?? MODES[0];
	const ActiveComponent = active.Component;
	return (
		<AppNavContext.Provider value={nav}>
			<div className="shell">
				<header className="topbar">
					<span className="brand">TreeWalker</span>
					<nav>
						{MODES.map((m) => (
							<button
								key={m.id}
								disabled={!m.enabled}
								className={m.id === mode ? "active" : ""}
								onClick={() => setMode(m.id)}
							>
								{m.label}
							</button>
						))}
					</nav>
				</header>
				<main>
					<ActiveComponent />
				</main>
			</div>
		</AppNavContext.Provider>
	);
}
