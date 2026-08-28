'use strict';

const {test, expect} = require('@playwright/test');

test('primary controls remain usable at desktop and mobile widths', async ({page}, testInfo) => {
  const volumeRequests = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/api/pc1/volume')) {
      volumeRequests.push(request.postDataJSON());
    }
  });
  await page.goto('/');
  await expect(page.locator('h1')).toHaveText('hAudio');
  await expect(page.locator('#graph-state')).toContainText('AUDIO GRAPH READY');
  await expect(page.locator('#pc1-volume-value')).toHaveText('50%');
  await expect(page.locator('#pc1-volume')).toBeVisible();
  await expect(page.locator('#mic-mute')).toBeVisible();
  await expect(page.locator('#recording-toggle')).toBeVisible();

  const pc1 = await page.locator('[data-device="pc1"]').boundingBox();
  const pc2 = await page.locator('[data-device="pc2"]').boundingBox();
  expect(pc1).not.toBeNull();
  expect(pc2).not.toBeNull();
  if (testInfo.project.name.startsWith('desktop')) {
    expect(Math.abs(pc1.y - pc2.y)).toBeLessThan(3);
  } else {
    expect(pc2.y).toBeGreaterThan(pc1.y + pc1.height - 3);
  }

  const soundboard = await page.locator('.soundboard-card').boundingBox();
  const system = await page.locator('.system-footer').boundingBox();
  expect(system.y).toBeGreaterThan(soundboard.y + soundboard.height - 3);

  await page.waitForTimeout(250);
  expect(volumeRequests).toHaveLength(0);
  const slider = await page.locator('#pc1-volume').boundingBox();
  expect(slider).not.toBeNull();
  await page.mouse.click(slider.x + slider.width * 0.42, slider.y + slider.height / 2);
  await expect.poll(() => volumeRequests.length).toBe(1);
  expect(volumeRequests[0].value).toBeGreaterThanOrEqual(35);
  expect(volumeRequests[0].value).toBeLessThan(50);
});
