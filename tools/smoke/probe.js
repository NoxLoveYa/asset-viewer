// SPDX-License-Identifier: AGPL-3.0-or-later
// Loaded into the app's frame by the smoke page (the app does not load it): the plugin viewer's config as the app's own
// plugins.js module holds it, for the page to read.
import { configOf } from "viewer/plugins.js";

window.__pluginConfig = (name) => JSON.parse(JSON.stringify(configOf(name) ?? null));
