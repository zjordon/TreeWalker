import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import BrowserView from "../components/BrowserView";

describe("BrowserView (P6 后续 I3)", () => {
	it("无截图时显示占位", () => {
		const { container } = render(<BrowserView mode="screenshots" screenshot={null} />);
		expect(container.textContent).toContain("等待截图");
	});

	it("有截图时渲染 img", () => {
		const { container } = render(<BrowserView mode="screenshots" screenshot="data:image/png;base64,xxx" />);
		expect(container.querySelector("img")).toBeTruthy();
	});

	it("highlights → 渲染归一化百分比的 .hl-box（left/width 换算）", () => {
		const { container } = render(
			<BrowserView
				mode="screenshots"
				screenshot="data:image/png;base64,xxx"
				highlights={[
					{ index: 0, bbox: { left: 0.1, top: 0.2, width: 0.3, height: 0.4 } },
				]}
			/>,
		);
		const box = container.querySelector(".hl-box") as HTMLElement;
		expect(box).toBeTruthy();
		const style = box.style;
		expect(style.left).toBe("10%");
		expect(style.top).toBe("20%");
		expect(style.width).toBe("30%");
		expect(style.height).toBe("40%");
		expect(box.querySelector(".hl-label")?.textContent).toBe("0");
	});

	it("无 highlights 不渲染 .hl-box", () => {
		const { container } = render(<BrowserView mode="screenshots" screenshot="data:x" />);
		expect(container.querySelector(".hl-box")).toBeNull();
	});

	it("livestream 模式同样渲染 img + 高亮 + 直播徽标", () => {
		const { container } = render(
			<BrowserView
				mode="livestream"
				screenshot="data:image/jpeg;base64,xxx"
				highlights={[{ index: 2, bbox: { left: 0.25, top: 0.25, width: 0.5, height: 0.5 } }]}
			/>,
		);
		expect(container.querySelector("img")).toBeTruthy();
		expect(container.querySelector(".mode-badge.live")?.textContent).toContain("直播");
		const box = container.querySelector(".hl-box") as HTMLElement;
		expect(box.style.left).toBe("25%");
		expect(box.style.width).toBe("50%");
	});

	it("livestream 无帧时显示直播占位", () => {
		const { container } = render(<BrowserView mode="livestream" screenshot={null} />);
		expect(container.textContent).toContain("等待直播帧");
	});
});
