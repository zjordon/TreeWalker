# 任务：为收到的商品到货更新库存（每码数的简单商品各改 Quantity）

Magento 管理后台（http://localhost:7780/admin）。可配置商品（如 Aero Daily Fitness Tee）的每个颜色+尺码组合是一条独立的 Simple Product（名称形如 `Aero Daily Fitness Tee-<SIZE>-<Color>`），需逐条进入编辑页修改 Quantity。

## 步骤

1. **进入产品列表**：侧边栏点击 Catalog → Products，进入 `http://localhost:7780/admin/catalog/product/`。
2. **过滤目标商品**：
   - 在 `input id=fulltext`（placeholder="Search by keyword"）中输入商品名（如 `Aero Daily Fitness Tee`），点击 `button aria-label=Search`。此时返回该可配置商品的全部变体（十几条）。
   - 点击 `Filters` 按钮，在 Name 过滤输入框（`input name=name`）填入颜色（如 `brown`），点击 `Apply Filters`。这样列表只显示 5 条棕色变体（XS/S/M/L/XL-Brown），不含可配置父商品。
3. **逐个更新每个尺码**：对每一行（识别方式：行内 Name 单元格文本 `Aero Daily Fitness Tee-<SIZE>-Brown`，行尾 `a aria-label="Edit Aero Daily Fitness Tee-<SIZE>-Brown"`）：
   - 点击该行 `Edit` 链接进入编辑页 `/admin/catalog/product/edit/id/<ID>/`。
   - 滚动（`scroll(1~2)`）使表单可见，找到 `Quantity` 输入框：`input name=product[quantity_and_stock_status][qty]`（每个商品编辑页其随机 id 不同，靠 name 定位）。
   - `input_text(index, text)` 写入新数量。到货语义 = 现有库存 + 到货数（本例每码数原 100，到货 378 → 填 478；各行原有数量可从列表的 Salable Quantity 列读取）。若是"set to N"语义则直接填 N。
   - 点击 `button id=save-button`（Save）保存。
   - 点击 `button id=back`（Back）返回产品列表，重复下一行。返回列表后可能需 `scroll` 才能再次看到各行 Edit 链接。
4. 全部尺码保存完成后 `done`。