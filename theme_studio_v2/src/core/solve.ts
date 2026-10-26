/** Public solver facade. Both the editor and every exporter call this API. */
import type { DeviceProfile, Doc } from './types.js';
import { resolveLegacyDoc, type ResolvedScene, type SolveOptions } from './layout/legacy.js';
import { resolveAdaptiveDoc } from './layout/adaptive.js';
import type { LayoutPlanner } from './layout/planner.js';
import { assertDocument, assertDevice } from './document.js';

export {
  flatten,
  measureText,
  wrapText,
  activeVariantKeys,
  mergeVariants,
} from './layout/legacy.js';
export type {
  Rect,
  ResolvedText,
  ResolvedNode,
  ResolvedScene,
  SolveOptions,
} from './layout/legacy.js';
export type { LayoutPlanner, PlannerInput } from './layout/planner.js';

export function resolveDoc(
  doc: Doc,
  device: DeviceProfile,
  options: SolveOptions & { planner?: LayoutPlanner } = {},
): ResolvedScene {
  assertDocument(doc);
  assertDevice(device);
  return doc.layout?.mode === 'auto' && !options.ignoreVariants
    ? resolveAdaptiveDoc(doc, device, options, options.planner)
    : resolveLegacyDoc(doc, device, options);
}
