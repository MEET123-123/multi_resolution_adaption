import { createZip, type ZipEntry } from './zip.js';
import { exportLockscreenXml, exportConversionReport, type ExportMode } from './xml.js';
import type { Doc } from '../core/types.js';
import type { ResolvedScene } from '../core/solve.js';
import { resourceFiles } from './assets.js';
import { summarize, validateScene } from '../core/validate.js';

export interface PackageOptions {
  mode: ExportMode;
  scene?: ResolvedScene;
  /** Package title shown in the theme app. */
  title?: string;
  author?: string;
  version?: string;
}

const enc = new TextEncoder();

function entry(name: string, text: string): ZipEntry {
  return { name, data: enc.encode(text) };
}

/** `description.xml` — the theme manager manifest. */
export function buildDescriptionXml(
  options: PackageOptions & { title: string; author: string; version: string },
): string {
  const { title, author, version } = options;
  return `<?xml version="1.0" encoding="UTF-8"?>
<HwTheme>
    <title>${escapeXml(title)}</title>
    <title-cn>${escapeXml(title)}</title-cn>
    <author>${escapeXml(author)}</author>
    <designer>${escapeXml(author)}</designer>
    <!-- 兼容旧版包描述；实际几何由 manifest.xml 的设备求解结果决定 -->
    <screen>FHD</screen>
    <version>${escapeXml(version)}</version>
    <font>Default</font>
    <font-cn>默认</font-cn>
    <icon-show>false</icon-show>
    <wallpaper>HWThemeEngine</wallpaper>
    <theme-banner-show>true</theme-banner-show>
</HwTheme>
`;
}

/**
 * `unlock/theme.xml` — legacy engine entry.
 *
 * The old packages pinned `maxHeight="2160"`, which is exactly why anything
 * taller than 18:9 fell back to stretched or letterboxed rendering. We keep the
 * attribute for engines that read it, but point it at the authoring canvas so it
 * no longer caps the real screen.
 */
export function buildUnlockThemeXml(doc: Doc, scene?: ResolvedScene): string {
  return `<?xml version="1.0" encoding="utf-8"?>
<HWTheme>
   <item style="slide"/>
   <item package="com.huawei.ucdlockscreen"/>
   <item dynamicPath="lockscreen"/>
   <item minversion="2.0.24"/>
   <item baseVersion="6.0"/>
   <item engineType="HWThemeEngine"/>
   <!-- 旧版引擎提示，按本次目标高度填写；不是鸿蒙5+适配开关 -->
   <item maxHeight="${scene?.device.height ?? doc.meta.design.h}"/>
</HWTheme>
`;
}

function escapeXml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Build the file list of a lockscreen-only `.hwt`. */
export function buildPackageFiles(doc: Doc, options: PackageOptions): ZipEntry[] {
  if (options.scene) {
    const errors = validateScene(options.scene).filter((i) => i.severity === 'error');
    if (errors.length)
      throw new Error(`布局有 ${errors.length} 个错误，不能打包：${errors[0]!.message}`);
  }
  const title = options.title ?? doc.meta.name;
  const author = options.author ?? doc.meta.author ?? 'theme-studio-v2';
  const version = options.version ?? '1.0.0';

  const files: ZipEntry[] = [
    entry('description.xml', buildDescriptionXml({ ...options, title, author, version })),
    entry('unlock/theme.xml', buildUnlockThemeXml(doc, options.scene)),
    entry(
      'unlock/lockscreen/manifest.xml',
      exportLockscreenXml(doc, { mode: options.mode, scene: options.scene }),
    ),
  ];
  files.push(...resourceFiles(doc, options.scene));
  files.push(
    entry(
      'delivery.json',
      JSON.stringify(
        {
          schema: 'theme-studio-delivery/1',
          status: 'candidate',
          target: options.scene?.device ?? null,
          layout: options.scene?.explanation ?? null,
          checks: options.scene ? summarize(validateScene(options.scene)) : null,
          nativeDeviceVerified: false,
          source: 'design-ir',
          engine: 'legacy-lockscreen-subset',
          note: '需要官方工具和真机验收；不是鸿蒙5+引擎迁移。',
        },
        null,
        2,
      ),
    ),
  );

  if (options.scene) {
    files.push(
      entry('unlock/lockscreen/resolution-report.md', exportConversionReport(doc, [options.scene])),
    );
  }
  return files;
}

export function packHwt(doc: Doc, options: PackageOptions): Uint8Array {
  return createZip(buildPackageFiles(doc, options));
}
