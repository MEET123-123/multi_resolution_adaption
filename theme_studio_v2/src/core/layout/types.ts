import type { Frame, SemanticRole, Composition } from '../types.js';

export interface DesignGroup {
  id: string;
  members: string[];
  bounds: Frame;
  role: SemanticRole;
  h: 'left' | 'center' | 'right' | 'scale';
  v: 'top' | 'center' | 'bottom' | 'scale';
  confidence: number;
  reasons: string[];
  position: 'flow' | 'fixed' | 'overlay' | 'offscreen';
  manual: boolean;
}

export interface GroupPlacement {
  group: string;
  source: Frame;
  target: Frame;
  scale: number;
  reason: string;
}

export interface LayoutExplanation {
  algorithm: string;
  composition: Composition;
  groups: DesignGroup[];
  placements: GroupPlacement[];
  regions: Frame[];
  warnings: string[];
}
