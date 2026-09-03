# Magento 2.4 admin 交互怪癖（列表页 / 表单）

## 列表页（product_listing / sales_order_grid 等 KO 网格）
- 首次进入先点 **Clear all** / **Default View** 清残留书签过滤器，再搜索——
  上一个会话的过滤器/分页/排序存在**服务端 bookmark**（ui_bookmark 表，按
  admin 用户），**跨浏览器实例存活**：换全新 Chrome 也会继承（2026-08-28 探针
  实测：新实例首屏即 153 行 complete 过滤 + pageSize=400 残留）。表现为
  "N records" 偏小或结果全是别的商品。取数前先核对网格声称的 total 与预期
  全集一致，不一致=有残留过滤。
- "N records found" 可见但**行文本为空** = 网格行渲染冻结：先**截一张图**
  （强制渲染一帧即可解锁），再读行。行渲染冻结**不影响组件数据层**——
  uiRegistry 里的数据照常可读。
- **读网格数据优先用 `read_grid` 动作**（结构化行 + total/sorting/filters
  元信息，支持 sorting/过滤/翻页参数）。无该动作时用 uiRegistry 直读配方：
  1. 枚举组件定位 data source（别猜名字）：
     `require(['uiRegistry'], r => r.get(c => { names.push(c.name); return false; }))`，
     取**排除 `notification_area.*`** 后形如 `<ns>.<ns>_data_source` 的名字
     （如 `sales_order_grid.sales_order_grid_data_source`）；
  2. 读行：`ds.data.items`（行数组，字段=列名）+ `ds.data.totalRecords` +
     `ds.params.{filters,paging,sorting}`（活动状态全在这）；
  3. 改查询后重载：`ds.set('params.paging', {pageSize:1000, current:1})` /
     `ds.set('params.sorting', {field:'created_at', direction:'desc'})` /
     `ds.set('params.filters', {placeholder:false, status:'complete'})`，
     然后 `ds.set('params.t', Date.now())` 触发重载，轮询 `data.totalRecords`
     + `items.length` 稳定后再读。
     ⚠️ 编程过滤**只改数据不更新 UI**——active filters 标签栏
     （`div.admin__data-grid-filters-current`）不会显示筛选条件。"查找/筛选
     并展示"类任务（判分或用户要求界面上体现筛选状态）必须走 UI Filter
     弹窗（点 Filters → 勾条件 → Apply），编程过滤仅用于自己读数据。
- **`POST /admin/mui/index/render/` 在本环境不可用**（2026-08-28 探针证伪：
  4 种请求变体 × sales_order_grid/product_listing 全部返回脚手架 HTML、
  无行数据，与 form_key/XHR 头/isAjax 参数无关）——别再试这条通道。
- 网格未渲染时 Clear all/Search/Filters/Actions 按钮原生点击全部无效——
  先解锁渲染（截图），再交互；或直接 JS `el.click()`。
- 评论网格（Marketing > Reviews）是 **legacy ExtJS 网格**，没有 uiRegistry：
  `window.reviewGridJsObject` 持 `{url, pageVar, sortVar, dirVar, filterVar}`，
  `fetch(url + '?isAjax=true&limit=100&form_key=' + fk)` 返回 HTML 片段，
  DOMParser 解析 tbody 行（2026-08-28 探针实测可用）；翻页/排序走对应
  pageVar/sortVar/dirVar 参数。

## 计数 / 全量清单提取（"how many / list all / total items" 类）
- **禁止只凭快照或截图数行**：列表在快照里每行膨胀几十行（属性+金额单元格
  全展开），实测 5 行的产品表模型只数出前 4 行（漏序列末端的行）——长表
  计数/枚举在膨胀文本上系统性漏尾。
- 正确做法：**evaluate 一次提取全量结构化清单，模型只做汇总不做枚举**。
  订单详情 Items 表：`[...document.querySelectorAll('.order-tables tbody tr, #order_items tbody tr')].map(r => r.innerText)`；
  普通表格换对应选择器。可配置产品父子双行注意去重（按产品名/SKU 归并）。
- 网格类（KO 异步）DOM 里读不到行时，用上面 uiRegistry 通道——
  `ds.data.items` 本身就是结构化全量，天然适合计数；**最值类（最近/最大）
  查询必须显式传 sorting 参数**，网格初始行序不保证任何排序。
- 交叉验证：提取的行数与页脚计数徽标（"N records found" / "N items"）
  一致才可下结论；不一致说明提取不全（分页/父子行/懒渲染）。

## 表单提交
- KO 表单（商品/价格规则）填完后**必须让字段收到 change 事件**：若字段带
  mage-error 或 Save 后报 "This is a required field"（值明明填过），用
  evaluate 走 native setter 重设（`Object.getOwnPropertyDescriptor(
  HTMLInputElement.prototype,'value').set` + input/change 事件）再提交。
- Save 点击无效时降级顺序：`el.click()` → `jQuery(el).trigger('click')` →
  `form.requestSubmit()` → fetch POST（注意：fetch POST 会跳过订单评论等
  页面副作用，仅读数据用它）。
- 商品描述 textarea 在 **Content 区块**懒加载：querySelector 找不到时先展开
  Content 区块再取 `textarea[name="product[description]"]`。
