import type { Doc, Node } from '../core/types.js';

const DESIGN = { w: 1080, h: 2340 };

/**
 * Demo lockscreen.
 *
 * Built the way a designer builds it: absolute frames on a 1080×2340 canvas,
 * then constraints that describe *intent* — this sits at the bottom, this
 * stretches edge to edge, this must not exceed 420dp on a tablet.
 *
 * Only two things are hand-authored per breakpoint: the two-column switch on
 * `expanded` and the compact column on `medium-landscape` (fold inner screen).
 * Every pixel value elsewhere is derived.
 */
export function createDemoDoc(): Doc {
  const wallpaper: Node = {
    id: 'wallpaper',
    name: '壁纸',
    kind: 'image',
    frame: { x: 0, y: 0, w: 1080, h: 2340 },
    constraints: { h: 'scale', v: 'scale' },
    sizing: { w: 'fill', h: 'fill' },
    image: { src: 'wallpaper/lockscreen.jpg', scaleType: 'fill' },
    fill: '#141c2b',
    locked: true,
  };

  const time: Node = {
    id: 'time',
    name: '时间',
    kind: 'time',
    frame: { x: 140, y: 372, w: 800, h: 236 },
    constraints: { h: 'center', v: 'top', maxW: 420, aspect: 0.295 },
    sizing: { w: 'fixed', h: 'fixed' },
    text: {
      content: '09:41',
      size: 236,
      color: '#FFFFFF',
      weight: 'bold',
      align: 'center',
      lineHeight: 1,
      maxLines: 1,
      autoShrink: false,
      letterSpacing: 0,
    },
    image: { src: 'time.png', scaleType: 'contain' },
    alpha: 255,
    variants: {
      expanded: {
        frame: { x: 200, y: 236, w: 700, h: 206 },
        constraints: { h: 'left', v: 'top', maxW: 380, aspect: 0.295 },
        text: { align: 'left' },
      },
      'medium-landscape': {
        frame: { x: 100, y: 200, w: 700, h: 206 },
        constraints: { h: 'left', v: 'top', maxW: 240, aspect: 0.295 },
        text: { align: 'left' },
      },
      'compact-square': {
        frame: { x: 140, y: 150, w: 800, h: 236 },
        constraints: { h: 'center', v: 'top', maxW: 250, aspect: 0.295 },
      },
    },
  };

  const date: Node = {
    id: 'date',
    name: '日期',
    kind: 'date',
    frame: { x: 140, y: 624, w: 800, h: 62 },
    constraints: { h: 'center', v: 'top', maxW: 420 },
    sizing: { w: 'fixed', h: 'hug' },
    text: {
      content: '10月24日 星期六',
      size: 42,
      color: '#E8EEF9',
      weight: 'medium',
      align: 'center',
      lineHeight: 1.2,
      maxLines: 1,
      autoShrink: true,
      letterSpacing: 1,
    },
    alpha: 235,
    variants: {
      expanded: {
        frame: { x: 200, y: 462, w: 700, h: 62 },
        constraints: { h: 'left', v: 'top', maxW: 380 },
        text: { align: 'left' },
      },
      'medium-landscape': {
        frame: { x: 100, y: 428, w: 700, h: 62 },
        constraints: { h: 'left', v: 'top', maxW: 240 },
        text: { align: 'left' },
      },
      'compact-square': { constraints: { h: 'center', v: 'top', maxW: 250 } },
    },
  };

  const clock: Node = {
    id: 'clock',
    name: '时钟组',
    kind: 'group',
    frame: { x: 0, y: 372, w: 1080, h: 314 },
    constraints: { h: 'center', v: 'top' },
    sizing: { w: 'fixed', h: 'hug' },
    autoLayout: { dir: 'col', gap: 16, pad: [0, 140, 0, 140], main: 'start', cross: 'center' },
    children: [time, date],
    variants: {
      expanded: {
        frame: { x: 200, y: 320, w: 700, h: 314 },
        constraints: { h: 'left', v: 'top', maxW: 380 },
        autoLayout: { dir: 'col', gap: 16, pad: [0, 0, 0, 0], main: 'start', cross: 'start' },
      },
      'medium-landscape': {
        frame: { x: 100, y: 200, w: 700, h: 290 },
        constraints: { h: 'left', v: 'top', maxW: 240 },
        autoLayout: { dir: 'col', gap: 16, pad: [0, 0, 0, 0], main: 'start', cross: 'start' },
      },
      'compact-square': {
        frame: { x: 0, y: 150, w: 1080, h: 314 },
        constraints: { h: 'center', v: 'top' },
      },
    },
  };

  const weatherChip: Node = {
    id: 'weather',
    name: '天气胶囊',
    kind: 'rect',
    frame: { x: 786, y: 132, w: 214, h: 64 },
    constraints: { h: 'right', v: 'top', maxW: 130 },
    sizing: { w: 'fixed', h: 'hug' },
    fill: '#FFFFFF',
    alpha: 38,
    radius: 32,
    safeArea: 'top',
    variants: {
      expanded: {
        frame: { x: 200, y: 120, w: 214, h: 64 },
        constraints: { h: 'left', v: 'top', maxW: 130 },
      },
      'medium-landscape': { hidden: true },
      'compact-square': { hidden: true },
    },
  };

  const weatherText: Node = {
    id: 'weather-text',
    name: '天气文字',
    kind: 'text',
    frame: { x: 786, y: 144, w: 214, h: 42 },
    constraints: { h: 'right', v: 'top', maxW: 130 },
    sizing: { w: 'fixed', h: 'hug' },
    text: {
      content: '18° 多云',
      size: 38,
      color: '#FFFFFF',
      weight: 'medium',
      align: 'center',
      lineHeight: 1.25,
      maxLines: 1,
      autoShrink: true,
      letterSpacing: 0,
    },
    safeArea: 'top',
    variants: {
      expanded: {
        frame: { x: 200, y: 132, w: 214, h: 42 },
        constraints: { h: 'left', v: 'top', maxW: 130 },
      },
      'medium-landscape': { hidden: true },
      'compact-square': { hidden: true },
    },
  };

  const noticeTitle: Node = {
    id: 'notice-title',
    name: '通知标题',
    kind: 'text',
    frame: { x: 0, y: 0, w: 836, h: 46 },
    constraints: { h: 'leftRight', v: 'top' },
    sizing: { w: 'fill', h: 'hug' },
    text: {
      content: '日程提醒 · 15:00',
      size: 46,
      color: '#FFFFFF',
      weight: 'bold',
      align: 'left',
      lineHeight: 1.25,
      maxLines: 1,
      autoShrink: true,
      letterSpacing: 0,
    },
  };

  const noticeBody: Node = {
    id: 'notice-body',
    name: '通知正文',
    kind: 'text',
    frame: { x: 0, y: 58, w: 836, h: 96 },
    constraints: { h: 'leftRight', v: 'top' },
    sizing: { w: 'fill', h: 'hug' },
    text: {
      content: '产品评审会 · 会议室 A，请提前十分钟到场',
      size: 42,
      color: '#D6DEEC',
      weight: 'regular',
      align: 'left',
      lineHeight: 1.4,
      maxLines: 2,
      autoShrink: true,
      letterSpacing: 0,
    },
  };

  const notice: Node = {
    id: 'notice',
    name: '通知卡片',
    kind: 'group',
    frame: { x: 0, y: 0, w: 900, h: 190 },
    constraints: { h: 'leftRight', v: 'top', maxW: 620 },
    sizing: { w: 'fill', h: 'hug' },
    fill: '#0B1220',
    alpha: 150,
    radius: 36,
    autoLayout: { dir: 'col', gap: 14, pad: [32, 32, 32, 32], main: 'start', cross: 'stretch' },
    children: [noticeTitle, noticeBody],
    variants: {
      'medium-landscape': { constraints: { h: 'leftRight', v: 'top', maxW: 300 } },
    },
  };

  const quote: Node = {
    id: 'quote',
    name: '个性签名',
    kind: 'text',
    frame: { x: 0, y: 0, w: 900, h: 90 },
    constraints: { h: 'leftRight', v: 'top', maxW: 600 },
    sizing: { w: 'fill', h: 'hug' },
    text: {
      content: '所爱隔山河，山河皆可平',
      size: 40,
      color: '#FFFFFF',
      weight: 'regular',
      align: 'center',
      lineHeight: 1.4,
      maxLines: 2,
      autoShrink: true,
      letterSpacing: 2,
    },
    alpha: 200,
    variants: {
      expanded: { text: { align: 'left' } },
      'medium-landscape': { hidden: true },
      'compact-square': { hidden: true },
    },
  };

  const content: Node = {
    id: 'content',
    name: '内容列',
    kind: 'group',
    frame: { x: 90, y: 760, w: 900, h: 340 },
    constraints: { h: 'center', v: 'center' },
    sizing: { w: 'fixed', h: 'hug' },
    autoLayout: { dir: 'col', gap: 24, pad: [0, 0, 0, 0], main: 'start', cross: 'stretch' },
    children: [notice, quote],
    variants: {
      expanded: {
        frame: { x: 90, y: 236, w: 900, h: 340 },
        constraints: { h: 'right', v: 'top', maxW: 470 },
      },
      'medium-landscape': {
        frame: { x: 90, y: 200, w: 900, h: 340 },
        constraints: { h: 'right', v: 'top', maxW: 300 },
      },
      'compact-square': {
        frame: { x: 90, y: 900, w: 900, h: 340 },
        constraints: { h: 'center', v: 'top', maxW: 560 },
      },
    },
  };

  const sliderTrack: Node = {
    id: 'slider-track',
    name: '解锁底槽',
    kind: 'rect',
    frame: { x: 260, y: 2004, w: 560, h: 148 },
    constraints: { h: 'center', v: 'bottom', maxW: 560, maxH: 76 },
    sizing: { w: 'fixed', h: 'hug' },
    fill: '#FFFFFF',
    alpha: 46,
    radius: 74,
    safeArea: 'bottom',
    variants: {
      expanded: { constraints: { h: 'center', v: 'bottom', maxW: 380, maxH: 76 } },
      'medium-landscape': { constraints: { h: 'center', v: 'bottom', maxW: 300, maxH: 76 } },
    },
  };

  const unlocker: Node = {
    id: 'unlocker',
    name: '上滑解锁',
    kind: 'unlocker',
    frame: { x: 260, y: 2004, w: 560, h: 148 },
    constraints: { h: 'center', v: 'bottom', maxW: 560, maxH: 76, minH: 44 },
    sizing: { w: 'fixed', h: 'hug' },
    safeArea: 'bottom',
    variants: {
      expanded: { constraints: { h: 'center', v: 'bottom', maxW: 380, maxH: 76, minH: 44 } },
      'medium-landscape': {
        constraints: { h: 'center', v: 'bottom', maxW: 300, maxH: 76, minH: 44 },
      },
    },
  };

  const camera: Node = {
    id: 'camera',
    name: '相机',
    kind: 'button',
    frame: { x: 100, y: 2180, w: 108, h: 108 },
    constraints: { h: 'left', v: 'bottom', aspect: 1, minH: 44, maxW: 60 },
    sizing: { w: 'fixed', h: 'fixed' },
    action: 'android.media.action.STILL_IMAGE_CAMERA_SECURE',
    fill: '#FFFFFF',
    alpha: 235,
    radius: 54,
    safeArea: 'bottom',
    variants: {
      expanded: { constraints: { h: 'left', v: 'bottom', aspect: 1, minH: 44, maxW: 60 } },
    },
  };

  const phone: Node = {
    id: 'phone',
    name: '电话',
    kind: 'button',
    frame: { x: 872, y: 2180, w: 108, h: 108 },
    constraints: { h: 'right', v: 'bottom', aspect: 1, minH: 44, maxW: 60 },
    sizing: { w: 'fixed', h: 'fixed' },
    action: 'android.intent.action.DIAL',
    fill: '#FFFFFF',
    alpha: 235,
    radius: 54,
    safeArea: 'bottom',
  };

  return {
    version: 2,
    meta: {
      name: '示例 · 锁屏响应式',
      author: 'theme-studio-v2',
      design: DESIGN,
      designWidthDp: 360,
      frameRate: 60,
      scene: 'lockscreen',
    },
    root: [
      wallpaper,
      clock,
      weatherChip,
      weatherText,
      content,
      sliderTrack,
      unlocker,
      camera,
      phone,
    ],
  };
}

/** No per-device coordinates: this example exercises structural inference. */
export function createAdaptiveDemoDoc(): Doc {
  const doc = createDemoDoc();
  const clean = (n: Node): Node => ({
    ...n,
    variants: undefined,
    ...(n.id === 'wallpaper'
      ? {
          kind: 'rect' as const,
          image: undefined,
          fill: '#18243B',
          responsive: { role: 'background' as const },
        }
      : {}),
    ...(n.kind === 'time' ? { image: undefined } : {}),
    children: n.children?.map(clean),
  });
  return {
    ...doc,
    layout: { mode: 'auto', composition: 'auto', gapDp: 12, marginDp: 16, maxScale: 1.2 },
    root: doc.root.map(clean),
    meta: { ...doc.meta, name: '设计推断 · 多设备锁屏' },
  };
}
