import { useState, useCallback, useEffect } from "react";
import type { ComponentType } from "react";
import RunView from "./components/RunView";
import FlowWorkspace from "./FlowWorkspace";
import SkillsShell from "./components/SkillsShell";
import SettingsShell from "./components/SettingsShell";
import CommandPalette from "./components/CommandPalette";
import { AppNavContext, useAppNav, MODEL_PRESETS, type AppNav } from "./appNav";
import { LiveTaskProvider, useLiveTask } from "./liveContext";

// 注册表驱动的顶部模式（§4.2 机制 1）：加模式 = 加一行，不改 shell。
// 设置面 T2 C（M4）启用（原 disabled 占位已删）。
interface ModeDef {
	id: string;
	label: string;
	enabled: boolean;
	Component: ComponentType;
}

const MODES: ModeDef[] = [
	{ id: "explore", label: "探索", enabled: true, Component: RunView },
	{ id: "flows", label: "流程库", enabled: true, Component: FlowWorkspace },
	{ id: "skills", label: "技能", enabled: true, Component: SkillsShell },
	{ id: "settings", label: "设置", enabled: true, Component: SettingsShell },
];

// T2 H（M1）：TopBar 运行状态点——live state 提升后任何模式下一眼可见有任务在跑，点击回运行视图。
// 必须是 Provider 内的子组件（AppShell 自身在 Provider 外，直接 hook 拿不到 context）。
function LiveStatusDot({ onOpen }: { onOpen: () => void }) {
	const { state } = useLiveTask();
	if (state.phase !== "running" && state.phase !== "paused") return null;
	return (
		<button
			className="live-dot"
			onClick={onOpen}
			title="有 agent 任务进行中，点击回到运行视图"
		>
			● {state.phase === "paused" ? "任务已暂停" : "任务运行中"}
		</button>
	);
}

// T2 I5（M6）：TopBar 模型选择。datalist 原生 combobox——静态候选 + 任意自定义输入；
// 留空 = 跟随设置默认（设置面 LLM_MODEL 是「改默认」，两层并存）；仅对新任务生效。
// 候选列表 MODEL_PRESETS 移至 appNav.ts（与 ⌘K 面板共用，避免循环依赖）。

function ModelPicker() {
	const nav = useAppNav();
	if (!nav) return null;
	return (
		<span className="model-picker">
			<input
				list="tw-model-presets"
				value={nav.model}
				onChange={(e) => nav.setModel(e.target.value)}
				placeholder="模型（默认）"
				title="LLM 模型，仅对新任务生效；留空 = 跟随设置默认"
			/>
			<datalist id="tw-model-presets">
				{MODEL_PRESETS.map((m) => (
					<option key={m} value={m} />
				))}
			</datalist>
		</span>
	);
}

export default function AppShell() {
	const [mode, setMode] = useState("explore");
	const [skillsHost, setSkillsHost] = useState<string | null>(null);
	const [flowsName, setFlowsName] = useState<string | null>(null);
	// T2 I4（M8）：⌘K 命令面板开关（Ctrl/Cmd+K 全局呼出）
	const [paletteOpen, setPaletteOpen] = useState(false);

	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
				e.preventDefault();
				setPaletteOpen((o) => !o);
			}
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, []);
	// T2 I5（M6）：模型选择（""=跟随设置默认），localStorage 记忆（读写失败静默——隐私模式等）
	const [model, setModelState] = useState(() => {
		try {
			return localStorage.getItem("tw-web.model") ?? "";
		} catch {
			return "";
		}
	});
	const setModel = useCallback((m: string) => {
		setModelState(m);
		try {
			localStorage.setItem("tw-web.model", m);
		} catch {
			/* 静默 */
		}
	}, []);

	// I1：从 RunView chip 跳到技能面并预选 host
	const openSkills = useCallback((host?: string | null) => {
		if (host) setSkillsHost(host);
		setMode("skills");
	}, []);

	// T2 I4（M8）：命令面板「打开流程」→ 流程库并预选加载（仿 openSkills）
	const openFlow = useCallback((name?: string | null) => {
		if (name) setFlowsName(name);
		setMode("flows");
	}, []);

	const nav: AppNav = { mode, setMode, skillsHost, openSkills, flowsName, openFlow, model, setModel };
	const active = MODES.find((m) => m.id === mode) ?? MODES[0];
	const ActiveComponent = active.Component;
	return (
		<AppNavContext.Provider value={nav}>
			<LiveTaskProvider>
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
						<ModelPicker />
						<LiveStatusDot onOpen={() => setMode("explore")} />
					</header>
					<main>
						<ActiveComponent />
					</main>
				</div>
				<CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
			</LiveTaskProvider>
		</AppNavContext.Provider>
	);
}
