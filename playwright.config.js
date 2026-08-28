'use strict';

const {defineConfig, devices} = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  testMatch: 'e2e.spec.js',
  timeout: 15000,
  use: {
    baseURL: 'http://127.0.0.1:8876',
    trace: 'retain-on-failure',
  },
  projects: [
    {name: 'desktop-chromium', use: {...devices['Desktop Chrome']}},
    {name: 'mobile-chromium', use: {...devices['Pixel 7']}},
  ],
  webServer: {
    command: 'python3 tests/e2e_server.py',
    url: 'http://127.0.0.1:8876/health/live',
    reuseExistingServer: true,
  },
});
