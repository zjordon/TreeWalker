# TreeWalker Recorder 扩展

录制用户在浏览器中的真实操作（click / input 等），发元素线索（xpath + rect）给本地
Python 后端，后端经 CDP 算指纹、拼成 `AgentHistory` 落盘，供 TreeWalker `load_and_rerun` 重放。

技术栈：**WXT（MV3）+ TypeScript + React**。借鉴 `D:\dev\git\ai\Browser-BC\extension`
（action-recorder / selector），精简到只保留 action 采集 + navigation（重放用得到的）。

详见 `docs/user_recording/README.md`。

## 开发

```bash
cd recording_extension
npm install         # 或 pnpm install
npm run dev         # wxt dev：构建到 .output/ 并热重载
```

然后在 Chrome `chrome://extensions/` 打开 Developer Mode → Load unpacked → 选
`.output/chrome-mv3-dev/`。

## 使用流程

1. Chrome 以远程调试端口启动（录制专用 profile，提前登录目标站点）：
   ```
   chrome --remote-debugging-port=9222 --user-data-dir=<录制 profile>
   ```
2. 启动后端：
   ```
   uv run python examples/record_user_actions.py --out myflow.json
   ```
3. 点扩展图标 → 「开始录制」→ 执行操作 → 「停止录制」。
4. 产物落 `rerun-history/myflow.json`，重放：
   ```python
   await agent.load_and_rerun("myflow.json", variables={"email": "new@x.com"})
   ```

## 架构（三层，借鉴 Browser-BC）

- **content script**（`entrypoints/content.ts`，`allFrames: true`）：监听 DOM 事件，
  用 `capture/selector.ts` 的 `buildElementRef` 采集元素线索，经 `chrome.runtime.sendMessage`
  发给 background。
- **background**（`entrypoints/background.ts`）：维护录制状态，把事件 POST 到后端
  `http://127.0.0.1:8765`。无状态 HTTP，MV3 友好（SW 按需唤醒）。
- **popup**（`entrypoints/popup/`）：开始 / 停止录制 UI（React）。

不负责指纹 —— 那由后端经 CDP 算（录制/重放同源，全对齐）。
