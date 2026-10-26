/** Convert the solver result to the exporter IR; never solve layout a second time. */
import type { Doc, Node } from '../core/types.js';
import type { ResolvedNode, ResolvedScene } from '../core/solve.js';

export function materialize(doc: Doc, scene: ResolvedScene): Node[] {
  const originals = new Map<string, Node>();
  const index = (nodes: Node[]): void =>
    nodes.forEach((n) => {
      originals.set(n.id, n);
      index(n.children ?? []);
    });
  index(doc.root);
  const convert = (n: ResolvedNode): Node => {
    const base = originals.get(n.id);
    if (!base) throw new Error(`求解结果含未知节点：${n.id}`);
    return {
      ...base,
      frame: n.rect,
      visible: n.visible,
      fill: n.fill,
      alpha: n.alpha,
      radius: n.radius,
      action: n.action,
      image: n.image,
      text: n.text ? { ...n.text, size: n.text.effectiveSize } : undefined,
      variants: undefined,
      children: n.children.map(convert),
    };
  };
  return scene.nodes.map(convert);
}
