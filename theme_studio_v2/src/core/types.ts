/**
 * Responsive document model (IR).
 *
 * Units: every geometry value is expressed in **design px** — the pixel space of
 * the authoring canvas (default 1080 x 2340). This mirrors the HarmonyOS theme
 * script, where x/y/w/h are plain pixels, so exporting stays lossless.
 *
 * Responsiveness comes from `constraints` + `sizing` + `variants`, not from the
 * numbers themselves. A frame is the *measured* result at design size; the
 * solver re-derives it for every device.
 */

export type NodeKind =
  | 'group'
  | 'image'
  | 'text'
  | 'rect'
  | 'time'
  | 'date'
  | 'unlocker'
  | 'button';

/** Horizontal anchoring, in Figma terms, extended with edge-stretch. */
export type HAnchor = 'left' | 'right' | 'leftRight' | 'center' | 'scale';

/** Vertical anchoring. `bottom` is what the legacy studio faked with `#screen_height-2160+y`. */
export type VAnchor = 'top' | 'bottom' | 'topBottom' | 'center' | 'scale';

/** How a dimension is decided. `fill` consumes the remaining space between margins. */
export type SizeMode = 'fixed' | 'fill' | 'hug';

export interface Frame {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Constraints {
  h: HAnchor;
  v: VAnchor;
  /** Lock width/height ratio. When set, height follows the solved width. */
  aspect?: number | null;
  /** Clamps in device-independent pixels; protects typography on tablets. */
  minW?: number;
  maxW?: number;
  minH?: number;
  maxH?: number;
}

export interface AutoLayout {
  dir: 'row' | 'col';
  gap: number;
  /** Padding in design px: [top, right, bottom, left]. */
  pad: [number, number, number, number];
  main: 'start' | 'center' | 'end' | 'spaceBetween';
  cross: 'start' | 'center' | 'end' | 'stretch';
}

export interface TextStyle {
  /** Literal copy; `time`/`date` nodes ignore it and use the runtime clock. */
  content: string;
  size: number;
  color: string;
  weight: 'regular' | 'medium' | 'bold';
  align: 'left' | 'center' | 'right';
  /** Multiplier of size. */
  lineHeight: number;
  maxLines: number;
  /** Shrink font until the copy fits instead of clipping. */
  autoShrink: boolean;
  letterSpacing: number;
}

export interface ImageStyle {
  src: string;
  scaleType: 'fill' | 'contain' | 'centerCrop';
  /** CSS gradient used by the studio when `src` is empty or unavailable. */
  placeholder?: string;
}

/** Per-breakpoint overrides. Merged shallowly over the base node. */
export interface NodePatch {
  variants?: Partial<Record<VariantKey, NodePatch>>;
  responsive?: Partial<ResponsiveHints>;
  frame?: Partial<Frame>;
  constraints?: Partial<Constraints>;
  sizing?: Partial<Node['sizing']>;
  autoLayout?: Partial<AutoLayout>;
  text?: Partial<TextStyle>;
  image?: Partial<ImageStyle>;
  fill?: string;
  radius?: number;
  alpha?: number;
  action?: string;
  safeArea?: 'top' | 'bottom' | 'both' | null;
  visible?: boolean;
  /** Drop the node on this breakpoint entirely. */
  hidden?: boolean;
}

export interface Node {
  id: string;
  name: string;
  kind: NodeKind;
  frame: Frame;
  constraints: Constraints;
  sizing: { w: SizeMode; h: SizeMode };
  autoLayout?: AutoLayout;
  text?: TextStyle;
  image?: ImageStyle;
  fill?: string;
  radius?: number;
  /** 0..255, matching the theme script's `alpha` attribute. */
  alpha?: number;
  /** Keep the node inside the status-bar / navigation-bar inset. */
  safeArea?: 'top' | 'bottom' | 'both' | null;
  /** Intent action for `button` nodes. */
  action?: string;
  visible?: boolean;
  locked?: boolean;
  /** Explicit design intent wins over inferred geometry. Never inferred from a theme name. */
  responsive?: ResponsiveHints;
  variants?: Partial<Record<VariantKey, NodePatch>>;
  children?: Node[];
}

export interface DeviceProfile {
  id: string;
  label: string;
  /** Short label for the preview strip. */
  short: string;
  formFactor:
    | 'phone'
    | 'phone-tall'
    | 'fold-cover'
    | 'fold-inner'
    | 'tri-fold'
    | 'wide-fold'
    | 'tablet'
    | 'tablet-land'
    | 'custom';
  width: number;
  height: number;
  /** Logical width in dp; drives breakpoint selection and density. */
  widthDp: number;
  /** System bar insets in px. */
  insets: { top: number; bottom: number; left: number; right: number };
  note?: string;
  /** Runtime posture is explicit; aspect ratio does not identify hardware. */
  posture?: 'flat' | 'folded' | 'half-open';
  features?: DisplayFeature[];
}

export interface DisplayFeature {
  bounds: Frame;
  kind: 'hinge' | 'cutout' | 'reserved';
  separating?: boolean;
  occluding?: boolean;
}

export type Composition = 'auto' | 'preserve' | 'stack' | 'split' | 'grid' | 'focus-dock';
export type SemanticRole =
  | 'background'
  | 'clock'
  | 'hero'
  | 'content'
  | 'footer'
  | 'status'
  | 'overlay';

export interface ResponsiveHints {
  role?: SemanticRole;
  /** Bind independent artwork and its hot area into one rigid component. */
  group?: string;
  position?: 'flow' | 'fixed' | 'overlay' | 'offscreen';
  priority?: number;
}

export interface ResponsivePolicy {
  mode: 'constraints' | 'auto';
  composition?: Composition;
  gapDp?: number;
  marginDp?: number;
  /** Maximum scale relative to the source/target density ratio. */
  maxScale?: number;
}

export interface Asset {
  /** Path relative to unlock/lockscreen; no absolute paths or parent traversal. */
  path: string;
  mime: string;
  base64: string;
}

export type Breakpoint = 'compact' | 'medium' | 'expanded';

/**
 * Variant keys are `breakpoint` optionally suffixed with a shape overlay:
 * `-landscape` or `-square`. The overlay is applied on top of the breakpoint
 * variant, the same way a CSS `@media (orientation: landscape)` block layers
 * over a width query. Near-square canvases (fold cover screens) need this
 * because width alone cannot describe them.
 */
export type ShapeClass = 'portrait' | 'landscape' | 'square';
export type VariantKey = Breakpoint | `${Breakpoint}-landscape` | `${Breakpoint}-square`;

export interface Doc {
  version: 2;
  meta: {
    name: string;
    author?: string;
    /** Authoring canvas, in px. */
    design: { w: number; h: number };
    /** Logical width of the design canvas, used to convert dp clamps. */
    designWidthDp: number;
    frameRate: number;
    /** Target script flavour. v1 only ships lockscreen. */
    scene: 'lockscreen';
  };
  /** Breakpoints the document is authored against; defaults come from devices.ts. */
  breakpoints?: { medium: number; expanded: number };
  layout?: ResponsivePolicy;
  devices?: DeviceProfile[];
  assets?: Asset[];
  /** Existing v2 documents used canvas coordinates. New documents can opt into parent coordinates. */
  coordinates?: 'canvas' | 'parent';
  root: Node[];
}
