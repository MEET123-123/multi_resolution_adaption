import { DEVICES } from '../src/core/devices.js';
import { resolveDoc, flatten } from '../src/core/solve.js';
import { validateScene, summarize } from '../src/core/validate.js';
import { createDemoDoc } from '../src/demo/lockscreen.js';

const doc = createDemoDoc();
let total = 0;
for (const device of DEVICES) {
  const scene = resolveDoc(doc, device);
  const issues = validateScene(scene);
  const s = summarize(issues);
  total += s.errors;
  console.log(`\n=== ${device.id} (${device.width}×${device.height}, ${device.widthDp}dp, ${scene.breakpoint}) sx=${scene.scale.sx.toFixed(3)} sy=${scene.scale.sy.toFixed(3)}`);
  for (const n of flatten(scene.nodes)) {
    if (!n.visible) continue;
    const r = n.rect;
    console.log(
      `  ${n.id.padEnd(14)} ${String(Math.round(r.x)).padStart(5)},${String(Math.round(r.y)).padStart(5)} ${String(Math.round(r.w)).padStart(5)}×${String(Math.round(r.h)).padStart(4)}` +
        (n.text ? `  size=${n.text.effectiveSize.toFixed(0)} lines=${n.text.lines}${n.text.overflow ? ' OVERFLOW' : ''}` : ''),
    );
  }
  for (const i of issues) console.log(`  [${i.severity}] ${i.code} ${i.nodeName}: ${i.message}`);
}
console.log(`\nerrors=${total}`);
