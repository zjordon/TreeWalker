# P5 评估：原生 `<select>` 测试站点清单

> 2026-08-07 整理。用途：P5（原生 select 选择动作变量化）方案落地前，找几个仍在用原生 `<select>` 的站点做小评估。
> 配套方案文档：[`01-select-as-variable-plan.md`](./01-select-as-variable-plan.md)。

## 背景

原生 `<select>` 现在越来越少（多数站点改用 AntD/Semi/MUI 等自定义下拉，那些走 `role=option` + click，**不在 P5 范围**）。
抖音/B 站这类常测站全是自定义组件，无法用来验证原生 select 的 value+label 链路，因此需要外部站点。

## 已核实（抓原始 HTML 确认）

### the-internet Dropdown —— ✅ 已确认

- 链接：<https://the-internet.herokuapp.com/dropdown>
- 抓到的原始 HTML：

  ```html
  <select id="dropdown">
    <option value="" disabled selected>Please select an option</option>
    <option value="1">Option 1</option>
    <option value="2">Option 2</option>
  </select>
  ```

- 适用理由：
  - **value ≠ label**（`1` 对 `Option 1`）→ 能验证录制是否同时抓 value 和 label、重放是否按 label 选。
  - `id="dropdown"` 可识别 → 能测 `_detect_from_attributes` 自动检测分支。
  - 单元素、结构可预测、零干扰，适合先做冒烟。
- 注意：占位项 `value=""`，且只有 2 个选项；the-internet 是免费 Heroku 应用，偶发慢/不可用。

## 推荐站点（按好用程度排序）

### 已验证在线的外部站点（2026-08-07 抓原始 HTML 核实）

> 现状：公网**服务端直出**的原生 select 在 2026 年已极少——练习/目录站大多下线、403、或改 SPA（原始 HTML 抓不到 `<select>`）。下表是逐个抓 raw HTML 确认 `<select>` 在线、且非 SPA 的站点。

| 站点 | 链接 | select 结构（已核实） | 适合测什么 |
|---|---|---|---|
| **the-internet Dropdown** | <https://the-internet.herokuapp.com/dropdown> | 单个 `<select id="dropdown">`，`value="1"` 对 `Option 1`、`value="2"` 对 `Option 2` | ✅ **首选**。value≠label + 可识别 id，覆盖方案多数验证点。Heroku 免费应用，偶发慢。 |
| **scrapethissite Hockey Teams** | <https://www.scrapethissite.com/pages/forms/> | `<select id="per_page">`，`value="25/50/100"` 对 label `25/50/100` | 真实页，但 **value==label** → 测「value 兜底」路径（方案设计决策 3 的 fallback）。 |
| **htmlQuick select 参考** | <https://www.htmlquick.com/reference/tags/select.html> | 多个静态 `<select>`，`<option>` **无 value、无 id/name** | 反例：测「无可识别属性 → 不应自动检测」（方案设计决策 4 保守检测）。 |

> ⚠️ **原列入、现已不可用的站点**（2026-08-07 复核确认）：
> - **DemoQA Select Menu**（demoqa.com/select-menu）：HTTP 200 但 raw HTML 已无 `<select>`，整站改 React SPA 客户端渲染；用户实测访问也有问题。
> - **Selenium 官方表单**（selenium.dev/selenium/web/webForm.html）：**404**，页面已下线。
> - 另探过 letcode.in/dropdowns、practicesoftwaretesting.com(403)、automatenow.io(连接关闭)、w3schools 示例(进 iframe)、loc catalog(403)、imdb/worldcat(全 SPA 或拦截)，均不可作服务端直出样本。

### 本地 fixture（最推荐的评估方式）

外部 value≠label 原生 select 样本几乎只剩 the-internet 一个。要**一次性覆盖方案全部边界**，最省事是本地起一个 HTML fixture（`file://` 直接开或本地静态服务），完全可控、不依赖网络、不被反爬拦截：

- value≠label（如 `value="cn"` 对 `中国`）→ 测 value/label 同时录制 + label 作替换键。
- value==label → 测 value 兜底。
- 带 `id`/`name`（如 `<select id="country">`）→ 测自动检测；不带 → 测「不误报」反例。
- 多个 select 同页 → 测变量名去重。
- 含占位空 value 项（`value=""`）→ 测录制/重放对空值的处理。

> ✅ 已生成：[`fixtures/native-select-fixture.html`](./fixtures/native-select-fixture.html) —— 7 个 case + 2 个非 DOM 验证点注释，覆盖方案每条验证点（录制 value+label、保守检测正/反例、label 作重放键、value 兜底、option 无 value 属性、变量名去重、text/select 混合）。`file://` 直接打开即可录制。

### 真实站点（要端到端真实感时用）

真正在用原生 select 的场景现在主要剩这几类，挑能匿名访问的：

1. **生日选择器（月/日/年）** —— 野生原生 select 存活最多的地方。很多注册流的 DOB 三连下拉就是原生 `<select>`。缺点：常要走到注册页深处。
2. **gov.uk 的各类事务表单**（如 <https://www.gov.uk> 的服务页）—— 出于无障碍立场坚持用原生控件，select 不少，结构规范（有 label/`id`）。
3. **Wikipedia 偏好设置**（登录后 `Special:Preferences`）—— 部分设置项是原生 select，`id`/`name` 规整，适合测变量名推导。
4. **phpMyAdmin / 老式后台** —— 几乎全是原生 select，但需自建环境。
5. **中文方向**（抖音/B 站是 Semi/AntD 自定义，不在范围）：**12306** 订单页、**政务服务网** 部分表单仍有原生 select，可作中文场景真实样本。

## 挑站点的关键判据（针对本方案）

本方案核心是 **value + label 同时记、label 作替换键**，因此：

- ❌ 避开 `value == label` 的页（测不出两者区别）；
- ✅ 选 **value ≠ label**（如 the-internet 的 `1` vs `Option 1`）；
- ✅ select 要带 **可识别的 `id`/`name`**（如 `country`/`state`），否则只能走 P4 手动标注，验证不到「自动检测」分支。

按这两条，外部公网能零成本跑通 value≠label 全链路的目前只有 **the-internet/dropdown**；要覆盖更多边界（value 兜底、无属性反例、多 select 去重、空占位项），建议用上面的**本地 fixture**。

## 待办（评估）

- [ ] 用 TreeWalker 对 the-internet/dropdown 跑一次录制冒烟，确认现状（改动前基线）：落盘 history 的 `select_dropdown` step 是否已带 label。
- [ ] 手动开 scrapethissite / htmlQuick，确认在真机浏览器渲染正常（raw 已确认是原生 `<select>`）。
- [ ] 列最小测试矩阵：每个站点 ↔ 方案哪条验证点。
