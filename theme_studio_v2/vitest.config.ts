import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['test/**/*.test.ts'],
    // Isolation makes vitest write an SSR module cache into the OS temp dir,
    // which sandboxed runners can deny. The suite is pure functions with no
    // shared mutable state, so a single module graph is safe and faster.
    isolate: false,
  },
});
