// "Exclude outliers": one setting for every chart and table on the site, remembered per browser.
import { useSyncExternalStore } from 'react';
import { storage } from './api';

const KEY = 'fs.excludeOutliers';
let on = storage.get<unknown>(KEY, false) === true;  // kept in memory too, so it works without storage
const listeners = new Set<() => void>();

function set(v: boolean) {
  on = v;
  storage.set(KEY, v);
  listeners.forEach((cb) => cb());
}

function subscribe(cb: () => void) {
  const onStorage = (e: StorageEvent) => {  // flipped in another tab
    if (e.key === KEY) { on = storage.get<unknown>(KEY, false) === true; cb(); }
  };
  listeners.add(cb);
  window.addEventListener('storage', onStorage);
  return () => { listeners.delete(cb); window.removeEventListener('storage', onStorage); };
}

export function useExcludeOutliers(): [boolean, (v: boolean) => void] {
  return [useSyncExternalStore(subscribe, () => on), set];
}
