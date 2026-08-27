'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const frontend = require('../opt/haudio/frontend/app.js');

class MockClassList {
  constructor() {
    this.values = new Set();
  }

  toggle(name, force) {
    if (force) this.values.add(name);
    else this.values.delete(name);
  }

  add(name) {
    this.values.add(name);
  }

  contains(name) {
    return this.values.has(name);
  }
}

class MockElement {
  constructor(id) {
    this.id = id;
    this.classList = new MockClassList();
    this.className = '';
    this.style = {};
    this.textContent = '';
    this.value = '';
    this.disabled = false;
    this.hidden = false;
    this.attributes = {};
    this.children = [];
    this.listeners = {};
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  matches() {
    return false;
  }

  append(...children) {
    this.children.push(...children);
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  replaceChildren(...children) {
    this.children = children;
  }

  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }
}

function createDocument() {
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, new MockElement(id));
    return elements.get(id);
  };
  return {
    activeElement: null,
    getElementById: get,
    createElement: (tagName) => new MockElement(tagName),
    querySelector: (selector) => selector === '[data-preset="mute-all"]' ? get('mute-all') : null,
    elements,
  };
}

function status(overrides = {}) {
  return {
    pc1: {connected: false, volume: 50, mute: false},
    pc2: {connected: true, volume: 50, mute: false},
    headset: {connected: true, volume: 100},
    microphone: {connected: true, volume: 100, mute: false, route_pc1: true, route_pc2: false},
    recording: {session: false},
    soundboard: {active: false, playing: '', volume: 80},
    presets: {mute_all_active: false},
    levels: {pc1: -60, pc2: -24.4, headset: -25, microphone: -60},
    system: {
      pipewire: true,
      graph_ready: true,
      disk_free_gb: 24.3,
      cpu_load: 1.2,
      ram_used_percent: 29.4,
      temperature_c: 58.5,
      uptime_seconds: 18000,
      connection_type: 'LAN',
      network_interface: 'eth0',
      primary_ip: '192.0.2.10',
      wlan_connected: false,
    },
    errors: [],
    ...overrides,
  };
}

test('soundboard stop state follows actual playback state', () => {
  global.document = createDocument();

  frontend.applyStatus(status());
  const stop = document.getElementById('soundboard-stop');
  assert.equal(stop.disabled, true);
  assert.equal(stop.classList.contains('danger'), false);
  assert.equal(stop.attributes['aria-pressed'], 'false');
  assert.equal(document.getElementById('soundboard-status').textContent, '');

  frontend.applyStatus(status({
    soundboard: {active: true, playing: 'alert.mp3', volume: 80},
  }));
  assert.equal(stop.disabled, false);
  assert.equal(stop.classList.contains('danger'), true);
  assert.equal(stop.attributes['aria-pressed'], 'true');
  assert.equal(document.getElementById('soundboard-status').textContent, 'Playing: alert.mp3');

  frontend.applyStatus(status());
  assert.equal(stop.disabled, true);
  assert.equal(stop.classList.contains('danger'), false);
});

test('disconnected inputs remain at the silent meter floor', () => {
  global.document = createDocument();
  frontend.applyStatus(status());

  assert.equal(document.getElementById('pc1-state').className, 'device-state off');
  assert.equal(document.getElementById('pc1-meter').style.width, '0%');
  assert.equal(document.getElementById('pc1-level').textContent, '-60.0 dB');
});

test('microphone routes are not shown active while globally muted', () => {
  global.document = createDocument();
  frontend.applyStatus(status({
    microphone: {
      connected: true,
      volume: 100,
      mute: true,
      route_pc1: true,
      route_pc2: true,
    },
  }));

  const pc1 = document.getElementById('mic-pc1');
  const pc2 = document.getElementById('mic-pc2');
  assert.equal(pc1.classList.contains('active'), false);
  assert.equal(pc2.classList.contains('active'), false);
  assert.equal(pc1.textContent, 'PC1 MUTED');
  assert.equal(pc2.textContent, 'PC2 MUTED');
  assert.equal(pc1.attributes['aria-pressed'], 'false');
  assert.equal(pc2.attributes['aria-pressed'], 'false');
  assert.equal(frontend.nextMicrophoneRouteValue({mute: true, route_pc1: true}, 'pc1'), true);
  assert.equal(frontend.nextMicrophoneRouteValue({mute: false, route_pc1: true}, 'pc1'), false);
  assert.equal(frontend.nextMicrophoneRouteValue({mute: false, route_pc1: false}, 'pc1'), true);
});

test('remote updates do not move a slider while it is active or pending', () => {
  global.document = createDocument();
  frontend.ui.pendingVolumes.clear();
  const slider = document.getElementById('pc1-volume');
  slider.value = 17;

  document.activeElement = slider;
  frontend.updateVolume('pc1', 75);
  assert.equal(slider.value, 17);

  document.activeElement = null;
  frontend.ui.pendingVolumes.add('pc1');
  frontend.updateVolume('pc1', 75);
  assert.equal(slider.value, 17);

  frontend.ui.pendingVolumes.delete('pc1');
  frontend.updateVolume('pc1', 75);
  assert.equal(slider.value, 75);
  assert.equal(document.getElementById('pc1-volume-value').textContent, '75%');
});

test('recording rows expose a headset play and stop toggle', () => {
  global.document = createDocument();
  frontend.ui.recordings = [{
    path: '2026-08-28/session.opus',
    name: 'session.opus',
    size: 1024,
    active: false,
    playing: false,
  }];

  frontend.renderRecordings();
  let row = document.getElementById('recording-list').children[0];
  let playback = row.children[1].children[0];
  assert.equal(playback.textContent, '▶');
  assert.equal(playback.className, 'icon-button play');
  assert.equal(playback.attributes['aria-label'], 'Play session.opus');

  frontend.ui.recordings[0].playing = true;
  frontend.renderRecordings();
  row = document.getElementById('recording-list').children[0];
  playback = row.children[1].children[0];
  assert.equal(playback.textContent, '■');
  assert.equal(playback.className, 'icon-button danger');
  assert.equal(playback.attributes['aria-label'], 'Stop session.opus');
});
