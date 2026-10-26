import type { Doc } from '../core/types.js';
import type { ResolvedScene } from '../core/solve.js';
import { exportLockscreenXml } from '../export/xml.js';
import { packHwt } from '../export/hwt.js';
import { exportBundle } from '../export/bundle.js';
import { download } from './store.js';

export type ExportAction = 'responsive' | 'baked' | 'hwt' | 'json' | 'preview' | 'bundle';

/** The current solved scene is authoritative for preview, XML and HWT. */
export function exportDesign(kind: ExportAction, doc: Doc, scene: ResolvedScene): string | null {
  if (kind === 'json')
    download(`${doc.meta.name}.json`, JSON.stringify(doc, null, 2), 'application/json');
  else if (kind === 'bundle') {
    const result = exportBundle(doc);
    download(`${doc.meta.name}-多设备.zip`, result.bytes, 'application/zip');
  } else if (kind === 'hwt') {
    download(
      `${doc.meta.name}-${scene.device.id}.hwt`,
      packHwt(doc, { mode: 'baked', scene }),
      'application/octet-stream',
    );
  } else {
    const options =
      kind === 'responsive' ? { mode: 'responsive' as const } : { mode: 'baked' as const, scene };
    const xml = exportLockscreenXml(doc, options);
    if (kind === 'preview') return xml;
    download(`manifest-${scene.device.id}.xml`, xml, 'application/xml');
  }
  return null;
}
