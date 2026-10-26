import type { Doc, Node, NodePatch, VariantKey } from '../core/types.js';

/** Depth-first update of one node by id. Returns a new tree. */
export function patchNode(
  root: Node[],
  id: string,
  patch: NodePatch | ((n: Node) => NodePatch),
): Node[] {
  return root.map((n) => {
    if (n.id === id) {
      const p = typeof patch === 'function' ? patch(n) : patch;
      return applyLocal(n, p);
    }
    if (n.children) return { ...n, children: patchNode(n.children, id, patch) };
    return n;
  });
}

function applyLocal(n: Node, p: NodePatch): Node {
  return {
    ...n,
    variants: p.variants ?? n.variants,
    responsive: p.responsive ? { ...n.responsive, ...p.responsive } : n.responsive,
    frame: { ...n.frame, ...p.frame },
    constraints: { ...n.constraints, ...p.constraints },
    sizing: { ...n.sizing, ...p.sizing },
    autoLayout: p.autoLayout
      ? {
          ...(n.autoLayout ?? {
            dir: 'col',
            gap: 0,
            pad: [0, 0, 0, 0],
            main: 'start',
            cross: 'start',
          }),
          ...p.autoLayout,
        }
      : n.autoLayout,
    text: p.text ? { ...(n.text as NonNullable<Node['text']>), ...p.text } : n.text,
    image: p.image ? { ...(n.image as NonNullable<Node['image']>), ...p.image } : n.image,
    fill: p.fill ?? n.fill,
    radius: p.radius ?? n.radius,
    alpha: p.alpha ?? n.alpha,
    action: p.action ?? n.action,
    safeArea: p.safeArea !== undefined ? p.safeArea : n.safeArea,
    visible: p.visible ?? n.visible,
  };
}

/**
 * Patch the frame of whichever level actually drives the current device:
 * the variant override when one exists, otherwise the base node.
 */
export function patchFrameFor(
  root: Node[],
  id: string,
  keys: VariantKey[],
  frame: Partial<Node['frame']>,
): Node[] {
  return patchNode(root, id, (n) => {
    const activeKey = [...keys].reverse().find((k) => n.variants?.[k]?.frame !== undefined);
    if (activeKey === undefined) return { frame };
    const existing = n.variants?.[activeKey];
    return {
      variants: {
        ...n.variants,
        [activeKey]: { ...existing, frame: { ...(existing?.frame ?? {}), ...frame } },
      },
    } as NodePatch;
  });
}

export function patchVariant(root: Node[], id: string, key: VariantKey, patch: NodePatch): Node[] {
  return patchNode(root, id, (n) => {
    const existing = n.variants?.[key] ?? {};
    return {
      variants: {
        ...n.variants,
        [key]: {
          ...existing,
          ...patch,
          frame: patch.frame ? { ...(existing.frame ?? {}), ...patch.frame } : existing.frame,
          constraints: patch.constraints
            ? { ...(existing.constraints ?? {}), ...patch.constraints }
            : existing.constraints,
          sizing: patch.sizing ? { ...(existing.sizing ?? {}), ...patch.sizing } : existing.sizing,
          autoLayout: patch.autoLayout
            ? { ...(existing.autoLayout ?? {}), ...patch.autoLayout }
            : existing.autoLayout,
          image: patch.image ? { ...(existing.image ?? {}), ...patch.image } : existing.image,
          responsive: patch.responsive
            ? { ...(existing.responsive ?? {}), ...patch.responsive }
            : existing.responsive,
          text: patch.text ? { ...(existing.text ?? {}), ...patch.text } : existing.text,
        },
      },
    } as NodePatch;
  });
}

export function findNode(root: Node[], id: string): Node | undefined {
  for (const n of root) {
    if (n.id === id) return n;
    if (n.children) {
      const hit = findNode(n.children, id);
      if (hit) return hit;
    }
  }
  return undefined;
}

export function walkNodes(root: Node[]): Node[] {
  const out: Node[] = [];
  const go = (list: Node[]): void => {
    for (const n of list) {
      out.push(n);
      if (n.children) go(n.children);
    }
  };
  go(root);
  return out;
}

export function download(name: string, content: string | Uint8Array, mime = 'text/plain'): void {
  const blob =
    typeof content === 'string'
      ? new Blob([content], { type: `${mime};charset=utf-8` })
      : new Blob([content as unknown as BlobPart], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
