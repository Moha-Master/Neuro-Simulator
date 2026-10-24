/**
 * 占位符匹配的纯函数——与后端 `templates.py::_has_placeholder` 同一条规则。
 *
 * 为什么要单独一个模块：这条判断在两边各有一份（后端算 `missing_placeholders`、
 * 编辑器对未保存草稿即时提示），字面量比对曾同时误报 `{{ chat_messages }}`——
 * 默认模板自己亮"缺少占位符"，把人推向改一个本来没坏的东西。
 * 没有前端测试运行器，所以规则集中在这里，改动时只有一处需要盯。
 */

const cache = new Map<string, RegExp>();

function placeholderRe(key: string): RegExp {
  const hit = cache.get(key);
  if (hit) return hit;
  // Jinja 里 `{{key}}` / `{{ key }}` / `{{- key -}}` 是同一个变量
  const re = new RegExp(`\\{\\{-?\\s*${key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*-?\\}\\}`);
  cache.set(key, re);
  return re;
}

/** `{{ key }}` 是否出现在文本里（空白的有无/多少都算命中）。 */
export function hasPlaceholder(source: string, key: string): boolean {
  return placeholderRe(key).test(source);
}

/** required 里没出现在 source 中的键（只作提示，绝不拦保存）。 */
export function missingPlaceholders(source: string, required: string[]): string[] {
  return required.filter((key) => !hasPlaceholder(source, key));
}
