// 元素线索生成 —— 移植自 Browser-BC capture/selector.ts（buildElementRef/bestSelector/xpathFor）。
// 仅作「录制瞬间定位线索」用：跨会话稳定性交给后端算的指纹，所以这里只要录制瞬间能唯一
// 标识节点即可。xpath 用 BB 风格 /html/...，后端 normalize_xpath 会 strip 前导 /。

import type { ElementRef } from '../shared/types';

const STABLE_ATTRS = ['data-testid', 'data-test', 'data-cy', 'aria-label', 'name', 'title'];
const MAX_TEXT_LENGTH = 120;

export function buildElementRef(element: Element): ElementRef {
  const rect = element.getBoundingClientRect();
  const text = normalizeText(element.textContent ?? '');
  const classes = Array.from(element.classList).filter(Boolean);
  const htmlElement = element instanceof HTMLElement ? element : null;

  const ref: ElementRef = {
    tag: element.tagName.toLowerCase(),
    selector: bestSelector(element),
    xpath: xpathFor(element),
    rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
  };
  if (htmlElement?.id) ref.id = htmlElement.id;
  if (classes.length) ref.classes = classes;
  const role = element.getAttribute('role');
  if (role) ref.role = role;
  const name = accessibleName(element);
  if (name) ref.name = name;
  if (text) ref.text = text;
  return ref;
}

/** 多级回退 CSS 选择器：id → 测试友好属性 → role → 路径回退 → xpath 兜底。 */
export function bestSelector(element: Element): string {
  const id = element.getAttribute('id');
  if (id) {
    const selector = `#${cssIdent(id)}`;
    if (isUnique(element, selector)) return selector;
  }
  for (const attr of STABLE_ATTRS) {
    const value = element.getAttribute(attr);
    if (!value) continue;
    const selector = `[${attr}="${cssString(value)}"]`;
    if (isUnique(element, selector)) return selector;
  }
  const role = element.getAttribute('role');
  if (role) {
    const selector = `[role="${cssString(role)}"]`;
    if (isUnique(element, selector)) return selector;
  }
  // 路径回退：tag.class:nth-of-type，逐层校验唯一
  const segments: string[] = [];
  let current: Element | null = element;
  while (current && current.nodeType === Node.ELEMENT_NODE) {
    segments.unshift(current.tagName.toLowerCase());
    const selector = segments.join(' > ');
    if (isUnique(element, selector)) return selector;
    if (current.tagName.toLowerCase() === 'html') break;
    current = current.parentElement;
  }
  return xpathFor(element);
}

/** 绝对 XPath：/html/body/form/input[1]（同标签多元素加 [n]）。 */
export function xpathFor(element: Element): string {
  const parts: string[] = [];
  let current: Element | null = element;
  while (current && current.nodeType === Node.ELEMENT_NODE) {
    const tag = current.tagName.toLowerCase();
    if (tag === 'html') {
      parts.unshift('html');
      break;
    }
    const parent = current.parentElement;
    const siblings = parent
      ? Array.from(parent.children).filter((s) => s.tagName === current!.tagName)
      : [];
    const index = siblings.length > 1 ? `[${siblings.indexOf(current) + 1}]` : '';
    parts.unshift(`${tag}${index}`);
    current = current.parentElement;
  }
  return `/${parts.join('/')}`;
}

function accessibleName(element: Element): string | undefined {
  return (
    normalizeText(element.getAttribute('aria-label') ?? '') ||
    normalizeText(element.getAttribute('name') ?? '') ||
    normalizeText(element.getAttribute('title') ?? '') ||
    undefined
  );
}

function normalizeText(value: string): string {
  return value.replace(/\s+/g, ' ').trim().slice(0, MAX_TEXT_LENGTH);
}

function isUnique(element: Element, selector: string): boolean {
  try {
    const matches = Array.from(element.ownerDocument.querySelectorAll(selector));
    return matches.length === 1 && matches[0] === element;
  } catch {
    return false;
  }
}

function cssString(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\A ');
}

function cssIdent(value: string): string {
  if (globalThis.CSS?.escape) return globalThis.CSS.escape(value);
  return value.replace(/(^-?\d)|[^\w-]/g, (match, firstDigit?: string) =>
    firstDigit ? `\\3${firstDigit} ` : `\\${match}`,
  );
}
