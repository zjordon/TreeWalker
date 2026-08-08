# 本地托管 fixture（内置 http.server）

> 2026-08-07。如何用 Python 内置 `http.server` 把 `fixtures/native-select-fixture.html` 托管到本机，
> 供浏览器人工查看 / 给 agent example 指向一个稳定 URL。

## 为什么要托管

fixture 是纯静态 HTML，`file://` 双击也能开，但有些场景要 `http://`：

- agent example 指向一个**稳定、常驻**的 URL，反复跑不用每次随机端口。
- 某些浏览器行为（扩展 content script 注入、CSP、fetch）在 `http://` 下才贴近真实站点——`file://` 下 MV3 扩展默认不注入。

> nginx 未安装也无妨——「谁来托管页面」与本项目要确认的结论（agent 是否记录原生 select 等）无关，内置 `http.server` 足够。

## 启动

```
uv run python -m http.server 8000 --directory docs/p5/fixtures
```

- `--directory` 直接指到 fixtures 目录，**无需 cd**；URL 就是文件名。
- 浏览器打开：**http://localhost:8000/native-select-fixture.html**
- 目录列表：http://localhost:8000/
- 换端口：把 `8000` 改掉（被占用时换一个，如 8010）。
- 停止：终端 `Ctrl+C`。

## 给 agent example 复用这个常驻服务

`examples/p5_agent_records_select.py` 内部自启临时 server（随机端口）。要让它改用上面的常驻服务：

- 把脚本里 `url = f"http://127.0.0.1:{port}/{FIXTURE_FILE}"` 改成 `url = "http://localhost:8000/native-select-fixture.html"`；
- 去掉 `_start_static_server()` 调用与对应的 `httpd.shutdown()`。

这样多跑几次 / 多个 example 共享同一个常驻 URL，不必各起各的 server。

## nginx（可选）

若确需部署到 nginx（比如要「真站点」环境或跨机器访问）：把 `native-select-fixture.html` 放进 nginx 静态目录（如 `/usr/share/nginx/html/`），配置 `root` 或一个 `location` 指向它，`nginx -s reload`。对本项目的确认目标无额外价值，仅当需要真实站点环境时才用。
