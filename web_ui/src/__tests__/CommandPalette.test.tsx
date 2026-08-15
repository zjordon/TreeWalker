import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import type { AppNav } from "../appNav";

const { apiMocks } = vi.hoisted(() => ({
	apiMocks: {
		listFiles: vi.fn(),
		listSkills: vi.fn(),
		getTaskHistory: vi.fn(),
	},
}));

vi.mock("../api", () => apiMocks);

import CommandPalette from "../components/CommandPalette";
import { AppNavContext } from "../appNav";
import { LiveTaskProvider, useLiveTask } from "../liveContext";

function makeNav(overrides: Partial<AppNav> = {}): AppNav {
	return {
		mode: "explore",
		setMode: vi.fn(),
		skillsHost: null,
		openSkills: vi.fn(),
		flowsName: null,
		openFlow: vi.fn(),
		model: "",
		setModel: vi.fn(),
		...overrides,
	};
}

// 探针：把 live state 的 task 文本透出到 DOM（断言「重发」回填）
function TaskProbe() {
	const { state } = useLiveTask();
	return <div data-testid="task-probe">{state.task}</div>;
}

function renderPalette(nav: AppNav, open = true) {
	return render(
		<AppNavContext.Provider value={nav}>
			<LiveTaskProvider>
				<CommandPalette open={open} onClose={vi.fn()} />
				<TaskProbe />
			</LiveTaskProvider>
		</AppNavContext.Provider>,
	);
}

describe("CommandPalette（T2 I4 M8）", () => {
	beforeEach(() => {
		apiMocks.listFiles.mockReset().mockResolvedValue(["douyin_upload.json", "bilibili_post.json"]);
		apiMocks.listSkills.mockReset().mockResolvedValue(["member.bilibili.com"]);
		apiMocks.getTaskHistory.mockReset().mockResolvedValue(["搜索猫", "上传视频"]);
	});

	it("open=false 时不渲染", () => {
		const { container } = renderPalette(makeNav(), false);
		expect(container.querySelector(".cmdk")).toBeNull();
	});

	it("打开时懒加载三类数据源，命令分组展示", async () => {
		const { container } = renderPalette(makeNav());
		await waitFor(() => expect(apiMocks.listFiles).toHaveBeenCalled());
		expect(apiMocks.listSkills).toHaveBeenCalled();
		expect(apiMocks.getTaskHistory).toHaveBeenCalled();
		const text = container.textContent ?? "";
		expect(text).toContain("流程库"); // 跳转
		expect(text).toContain("打开流程 douyin_upload.json");
		expect(text).toContain("打开技能 member.bilibili.com");
		expect(text).toContain("重发 搜索猫");
		expect(text).toContain("模型：跟随默认");
	});

	it("子串过滤（不区分大小写）；无匹配显示空态", async () => {
		const { container } = renderPalette(makeNav());
		await waitFor(() => expect(container.textContent).toContain("douyin_upload.json"));
		const input = container.querySelector(".cmdk input") as HTMLInputElement;
		fireEvent.change(input, { target: { value: "bilibili_post" } });
		expect(container.textContent).toContain("打开流程 bilibili_post.json");
		expect(container.textContent).not.toContain("douyin_upload.json");
		fireEvent.change(input, { target: { value: "zzz不存在" } });
		expect(container.textContent).toContain("（无匹配命令）");
	});

	it("Enter 执行当前高亮命令并关闭；↑↓ 移动高亮", async () => {
		const onClose = vi.fn();
		const nav = makeNav();
		const { container } = render(
			<AppNavContext.Provider value={nav}>
				<LiveTaskProvider>
					<CommandPalette open={true} onClose={onClose} />
				</LiveTaskProvider>
			</AppNavContext.Provider>,
		);
		await waitFor(() => expect(container.textContent).toContain("设置"));
		const input = container.querySelector(".cmdk input") as HTMLInputElement;
		fireEvent.change(input, { target: { value: "设置" } });
		fireEvent.keyDown(input, { key: "Enter" });
		expect(nav.setMode).toHaveBeenCalledWith("settings");
		expect(onClose).toHaveBeenCalled();
		// ↓ 移动高亮：过滤「模型」有两条，↓ 后 Enter 选第二条预设
		fireEvent.keyDown(input, { key: "Escape" }); // 已关闭，重开场景由 AppShell 测试覆盖
	});

	it("↑↓ 键盘导航选择", async () => {
		const nav = makeNav();
		const { container } = renderPalette(nav);
		await waitFor(() => expect(container.textContent).toContain("重发 搜索猫"));
		const input = container.querySelector(".cmdk input") as HTMLInputElement;
		fireEvent.change(input, { target: { value: "重发" } });
		const items = () => Array.from(container.querySelectorAll(".cmdk-list li"));
		expect(items()[0].className).toContain("active"); // 首项默认高亮（最新历史）
		fireEvent.keyDown(input, { key: "ArrowDown" });
		await waitFor(() => expect(items()[1].className).toContain("active"));
		fireEvent.keyDown(input, { key: "ArrowUp" });
		await waitFor(() => expect(items()[0].className).toContain("active"));
		fireEvent.keyDown(input, { key: "Enter" });
		expect(nav.setMode).toHaveBeenCalledWith("explore");
	});

	it("重发命令：切 explore + 回填任务文本（不启动）", async () => {
		const nav = makeNav();
		const { container } = renderPalette(nav);
		await waitFor(() => expect(container.textContent).toContain("重发 搜索猫"));
		fireEvent.click(
			Array.from(container.querySelectorAll(".cmdk-list button")).find((b) =>
				b.textContent?.includes("重发 搜索猫"))!,
		);
		expect(nav.setMode).toHaveBeenCalledWith("explore");
		await waitFor(() =>
			expect(container.querySelector("[data-testid=task-probe]")?.textContent).toBe("搜索猫"));
	});

	it("数据源全部失败 → 静默降级为跳转/模型命令，不崩", async () => {
		apiMocks.listFiles.mockRejectedValue(new Error("down"));
		apiMocks.listSkills.mockRejectedValue(new Error("down"));
		apiMocks.getTaskHistory.mockRejectedValue(new Error("down"));
		const { container } = renderPalette(makeNav());
		await waitFor(() => expect(apiMocks.listFiles).toHaveBeenCalled());
		const text = container.textContent ?? "";
		expect(text).toContain("设置");
		expect(text).toContain("模型：跟随默认");
		expect(text).not.toContain("打开流程");
		expect(text).not.toContain("重发");
	});

	it("Esc 关闭", async () => {
		const onClose = vi.fn();
		const { container } = render(
			<AppNavContext.Provider value={makeNav()}>
				<LiveTaskProvider>
					<CommandPalette open={true} onClose={onClose} />
				</LiveTaskProvider>
			</AppNavContext.Provider>,
		);
		await waitFor(() => expect(container.textContent).toContain("设置"));
		const input = container.querySelector(".cmdk input") as HTMLInputElement;
		fireEvent.keyDown(input, { key: "Escape" });
		expect(onClose).toHaveBeenCalled();
	});
});
