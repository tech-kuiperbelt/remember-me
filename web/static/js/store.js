// store.js — shared state: namespace registry, maintenance snapshot and the
// data-change events that replace cross-module direct calls.

let namespaces = [];
let namespaceCounts = {};
let categories = {};
let maintenance = null;

const namespaceListeners = new Set();
const categoryListeners = new Set();
const maintenanceListeners = new Set();
const memoriesListeners = new Set();
const statsListeners = new Set();

export function getNamespaces() { return namespaces; }
export function getNamespaceCounts() { return namespaceCounts; }

export function setNamespaces(list, counts) {
  namespaces = list || [];
  namespaceCounts = counts || {};
  namespaceListeners.forEach(fn => fn());
}
export function onNamespacesChanged(fn) { namespaceListeners.add(fn); }

export function getCategories() { return categories; }
export function setCategories(map) {
  categories = map || {};
  categoryListeners.forEach(fn => fn());
}
export function onCategoriesChanged(fn) { categoryListeners.add(fn); }

export function getMaintenance() { return maintenance; }
export function setMaintenance(state) {
  maintenance = state;
  maintenanceListeners.forEach(fn => fn(state));
}
export function onMaintenanceChanged(fn) { maintenanceListeners.add(fn); }

export function onMemoriesChanged(fn) { memoriesListeners.add(fn); }
export function emitMemoriesChanged() { memoriesListeners.forEach(fn => fn()); }

export function onStatsChanged(fn) { statsListeners.add(fn); }
export function emitStatsChanged() { statsListeners.forEach(fn => fn()); }
