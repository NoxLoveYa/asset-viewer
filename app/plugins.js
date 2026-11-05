// SPDX-License-Identifier: AGPL-3.0-or-later
// The client plugin registry. Core registers its own categories and views through it too, so a plugin kind is a peer:
//   viewer.addCategory({ kind, label, icon, view: () => import("./view.js"), rowMeta(item) })
//   viewer.addView(kind, loader)            a view for an existing kind
//   viewer.modelAddon({ id, applies(item), async prepare(api), destroy() })   see views/model.js
//   viewer.keyHelp(text)                    a line in the key hints
//   viewer.config / viewer.api(path, init)  the plugin's client config; fetch of /api/plugins/<name>/<path>
//   viewer.onConfig(fn)                     fn(config) after the config changed; viewer.config is replaced, so read it at use time
// A plugin's client.js exports register(viewer).
import { request } from "./api.js";

export const registry = {
  categories: new Map(), // kind -> { kind, label, icon, rowMeta }
  loaders: new Map(), // kind -> () => Promise<module with mount>
  addons: [], // model add-ons
  keyHelp: [],
};

export function addCategory({ kind, label, icon, view, rowMeta }) {
  if (!kind || !label) throw new Error("addCategory needs a kind and a label");
  if (registry.categories.has(kind)) console.warn(`plugins: category "${kind}" is registered twice, the later one wins`);
  registry.categories.set(kind, { kind, label, icon: icon || kind, rowMeta });
  if (view) registry.loaders.set(kind, view);
}

function addView(kind, loader) {
  if (typeof loader !== "function") throw new Error("addView needs a loader function");
  registry.loaders.set(kind, loader);
}

function modelAddon(def) {
  if (!def?.id || typeof def.applies !== "function" || typeof def.prepare !== "function") throw new Error("modelAddon needs id, applies() and prepare()");
  registry.addons = registry.addons.filter((a) => a.id !== def.id).concat(def);
}

const keyHelp = (text) => registry.keyHelp.push(String(text));

export const viewLoader = (kind) => registry.loaders.get(kind) || null;

// The `viewer` object one plugin's register() receives, kept by name: its `config` is replaced when the config changes.
const viewers = new Map();

function viewerFor(name, config) {
  const viewer = {
    name,
    config,
    onConfig: (fn) => { listeners.get(name).add(fn); },
    addCategory,
    addView,
    modelAddon,
    keyHelp,
    api: (path, init = {}) => request(`/api/plugins/${encodeURIComponent(name)}/${path.replace(/^\//, "")}`, init),
  };
  viewers.set(name, viewer);
  listeners.set(name, new Set());
  return viewer;
}

const listeners = new Map(); // plugin name -> Set<fn(config)>

export const configOf = (name) => viewers.get(name)?.config; // for tests and tools

// Gives each loaded plugin's viewer its new client config (in place: `viewer.config` read at use time is current) and calls
// its onConfig callbacks. Returns the names of the plugins whose config changed.
export function updatePluginConfigs(list) {
  const changed = [];
  for (const p of list || []) {
    const viewer = viewers.get(p.name);
    const next = p.config || {};
    if (!viewer || JSON.stringify(viewer.config) === JSON.stringify(next)) continue;
    viewer.config = next;
    changed.push(p.name);
    for (const fn of listeners.get(p.name)) {
      try { fn(next); } catch (e) { console.error(`plugin ${p.name} onConfig:`, e); }
    }
  }
  return changed;
}

// Adds each plugin's stylesheets and runs its client.js. A failing plugin is reported, not fatal.
export async function loadPlugins(list, report) {
  for (const p of list) {
    try {
      for (const href of p.styles || []) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = href;
        document.head.append(link);
      }
      if (p.client) {
        const mod = await import(p.client);
        if (typeof mod.register !== "function") throw new Error("client.js has no register(viewer)");
        await mod.register(viewerFor(p.name, p.config || {}));
      }
    } catch (e) {
      console.error(`plugin ${p.name}:`, e);
      report(`Plugin ${p.name} did not load: ${e.message}`);
    }
  }
}
