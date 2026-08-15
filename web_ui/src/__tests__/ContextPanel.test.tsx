import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import ContextPanel, { EventDetail } from "../components/ContextPanel";
import type { TaskEvent } from "../types";

// T2 G（M7）：ContextPanel 通用容器（标题/折叠/✕）+ EventDetail 按事件类型渲染。

describe("ContextPanel 容器（T2 G M7）", () => {
	it("展开态：标题 + children + 折叠/关闭按钮", () => {
		const { container, getByText } = render(
			<ContextPanel title="tool_call · step 3" collapsed={false} onToggle={vi.fn()} onClose={vi.fn()}>
				<div>详情内容</div>
			</ContextPanel>,
		);
		expect(getByText("tool_call · step 3")).toBeTruthy();
		expect(getByText("详情内容")).toBeTruthy();
		expect(container.querySelector(".ctx-toggle")).toBeTruthy();
		expect(container.querySelector(".ctx-close")).toBeTruthy();
	});

	it("折叠态：只剩细条展开按钮，children 不渲染", () => {
		const onToggle = vi.fn();
		const { container } = render(
			<ContextPanel title="t" collapsed={true} onToggle={onToggle}>
				<div>详情内容</div>
			</ContextPanel>,
		);
		expect(container.textContent).not.toContain("详情内容");
		fireEvent.click(container.querySelector(".ctx-toggle")!);
		expect(onToggle).toHaveBeenCalled();
	});

	it("折叠/关闭回调触发", () => {
		const onToggle = vi.fn();
		const onClose = vi.fn();
		const { container } = render(
			<ContextPanel title="t" collapsed={false} onToggle={onToggle} onClose={onClose}>
				null
			</ContextPanel>,
		);
		fireEvent.click(container.querySelector(".ctx-toggle")!);
		expect(onToggle).toHaveBeenCalled();
		fireEvent.click(container.querySelector(".ctx-close")!);
		expect(onClose).toHaveBeenCalled();
	});
});

describe("EventDetail 渲染器（T2 G M7）", () => {
	it("tool_call：动作名/参数 JSON/xpath/index/bbox", () => {
		const e: TaskEvent = {
			type: "tool_call", step: 3, action_name: "click",
			params: { index: 7 }, element_index: 7,
			element_xpath: "//button[@title='发布']",
			element_bbox: { left: 0.1, top: 0.2, width: 0.05, height: 0.03 },
		};
		const { container } = render(<EventDetail event={e} />);
		expect(container.textContent).toContain("click");
		expect(container.textContent).toContain("\"index\": 7");
		expect(container.textContent).toContain("//button[@title='发布']");
		expect(container.textContent).toContain("bbox");
	});

	it("model_result：next_goal + token", () => {
		const e: TaskEvent = {
			type: "model_result", step: 2, next_goal: "点击发布按钮",
			input_tokens: 1200, output_tokens: 80,
		};
		const { container } = render(<EventDetail event={e} />);
		expect(container.textContent).toContain("点击发布按钮");
		expect(container.textContent).toContain("↑1200");
		expect(container.textContent).toContain("↓80");
	});

	it("skill_active：host + 字数 + 进技能面按钮回调", () => {
		const onOpenSkills = vi.fn();
		const e: TaskEvent = { type: "skill_active", host: "member.bilibili.com", char_count: 800 };
		const { container } = render(<EventDetail event={e} onOpenSkills={onOpenSkills} />);
		expect(container.textContent).toContain("member.bilibili.com");
		fireEvent.click(Array.from(container.querySelectorAll("button")).find((b) =>
			b.textContent?.includes("查看/编辑技能"))!);
		expect(onOpenSkills).toHaveBeenCalledWith("member.bilibili.com");
	});

	it("其他类型：原始 JSON 兜底", () => {
		const e: TaskEvent = { type: "anomaly", step: 5, reason: "loop" };
		const { container } = render(<EventDetail event={e} />);
		expect(container.textContent).toContain("\"anomaly\"");
		expect(container.textContent).toContain("loop");
	});
});
