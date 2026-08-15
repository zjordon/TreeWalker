import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import { useAppNav, MODEL_PRESETS } from "../appNav";
import { useLiveTask } from "../liveContext";

// T2 I4（M8）：⌘K / Ctrl+K 全局命令面板。五类硬编码命令源（MVP 不做注册表抽象）：
// 跳转（四模式）/ 流程（listFiles）/ 技能（listSkills）/ 最近任务（/task/history，重发=只回填
// 不启动）/ 模型（跟随默认 + 预设）。数据源在**打开面板时懒加载一次**（§8.10）——任一失败
// 静默降级为该组为空（只剩跳转/模型命令），不阻塞面板。过滤 = 子串不区分大小写（不做拼音）。

interface Cmd {
	group: string;
	label: string;
	run: () => void;
}

export default function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
	const nav = useAppNav();
	const live = useLiveTask();
	const [query, setQuery] = useState("");
	const [active, setActive] = useState(0);
	const [sources, setSources] = useState<{ flows: string[]; skills: string[]; history: string[] }>({
		flows: [],
		skills: [],
		history: [],
	});
	const inputRef = useRef<HTMLInputElement>(null);

	// 打开时懒加载一次（每次打开重拉，拿到最新列表）
	useEffect(() => {
		if (!open) return;
		setQuery("");
		setActive(0);
		Promise.allSettled([api.listFiles(), api.listSkills(), api.getTaskHistory()]).then(
			([flows, skills, history]) => {
				setSources({
					flows: flows.status === "fulfilled" ? flows.value.slice(0, 20) : [],
					skills: skills.status === "fulfilled" ? skills.value.slice(0, 20) : [],
					history: history.status === "fulfilled" ? [...history.value].slice(-10).reverse() : [],
				});
			},
		);
	}, [open]);

	useEffect(() => {
		if (open) inputRef.current?.focus();
	}, [open]);

	const commands = useMemo<Cmd[]>(() => {
		if (!nav) return [];
		const cmds: Cmd[] = [
			{ group: "跳转", label: "探索（运行视图）", run: () => nav.setMode("explore") },
			{ group: "跳转", label: "流程库", run: () => nav.setMode("flows") },
			{ group: "跳转", label: "技能", run: () => nav.setMode("skills") },
			{ group: "跳转", label: "设置", run: () => nav.setMode("settings") },
		];
		for (const f of sources.flows) {
			cmds.push({ group: "流程", label: `打开流程 ${f}`, run: () => nav.openFlow(f) });
		}
		for (const h of sources.skills) {
			cmds.push({ group: "技能", label: `打开技能 ${h}`, run: () => nav.openSkills(h) });
		}
		for (const t of sources.history) {
			cmds.push({
				group: "最近任务",
				label: `重发 ${t}`,
				run: () => {
					nav.setMode("explore");
					live?.dispatch({ type: "FIELD", key: "task", value: t }); // 只回填，不直接启动
				},
			});
		}
		cmds.push({ group: "模型", label: "模型：跟随默认", run: () => nav.setModel("") });
		for (const m of MODEL_PRESETS) {
			cmds.push({ group: "模型", label: `模型 ${m}`, run: () => nav.setModel(m) });
		}
		return cmds;
	}, [nav, sources, live]);

	if (!open) return null;

	const q = query.trim().toLowerCase();
	const filtered = q ? commands.filter((c) => c.label.toLowerCase().includes(q)) : commands;
	const idx = Math.min(active, Math.max(0, filtered.length - 1)); // 过滤后收窄时夹住高亮

	const exec = (cmd?: Cmd) => {
		if (!cmd) return;
		cmd.run();
		onClose();
	};

	const onKeyDown = (e: React.KeyboardEvent) => {
		if (e.key === "ArrowDown") {
			e.preventDefault();
			setActive(Math.min(idx + 1, filtered.length - 1));
		} else if (e.key === "ArrowUp") {
			e.preventDefault();
			setActive(Math.max(idx - 1, 0));
		} else if (e.key === "Enter") {
			e.preventDefault();
			exec(filtered[idx]);
		} else if (e.key === "Escape") {
			e.preventDefault();
			onClose();
		}
	};

	return (
		<div
			className="cmdk-backdrop"
			onMouseDown={(e) => {
				if (e.target === e.currentTarget) onClose(); // 点背景关闭（点面板自身不关）
			}}
		>
			<div className="cmdk">
				<input
					ref={inputRef}
					value={query}
					onChange={(e) => {
						setQuery(e.target.value);
						setActive(0);
					}}
					onKeyDown={onKeyDown}
					placeholder="搜索：流程 / 技能 / 最近任务 / 跳转 / 模型…"
				/>
				<ul className="cmdk-list">
					{filtered.length === 0 && <li className="muted cmdk-empty">（无匹配命令）</li>}
					{filtered.map((c, i) => (
						<li key={`${c.group}-${c.label}`} className={i === idx ? "active" : ""}>
							<button onMouseEnter={() => setActive(i)} onClick={() => exec(c)}>
								<span className="cmdk-group">{c.group}</span>
								{c.label}
							</button>
						</li>
					))}
				</ul>
				<div className="muted cmdk-hint">↑↓ 选择 · Enter 执行 · Esc 关闭 · Ctrl/⌘+K 呼出</div>
			</div>
		</div>
	);
}
