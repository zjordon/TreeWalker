import { useState } from "react";
import type { ComponentType } from "react";
import RunView from "./components/RunView";
import FlowWorkspace from "./FlowWorkspace";

// 注册表驱动的顶部模式（§4.2 机制 1）：技能/设置首期 disabled，后期改 enabled 即出现，不改 shell。
interface ModeDef {
	id: string;
	label: string;
	enabled: boolean;
	Component: ComponentType;
}

function SkillsPlaceholder() {
	return <div className="panel">技能面（后置，P6 首期未启用）</div>;
}

function SettingsPlaceholder() {
	return <div className="panel">设置面（后置，P6 首期未启用）</div>;
}

const MODES: ModeDef[] = [
	{ id: "explore", label: "探索", enabled: true, Component: RunView },
	{ id: "flows", label: "流程库", enabled: true, Component: FlowWorkspace },
	{ id: "skills", label: "技能", enabled: false, Component: SkillsPlaceholder },
	{ id: "settings", label: "设置", enabled: false, Component: SettingsPlaceholder },
];

export default function AppShell() {
	const [mode, setMode] = useState("explore");
	const active = MODES.find((m) => m.id === mode) ?? MODES[0];
	const ActiveComponent = active.Component;
	return (
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
	);
}
