// Worker معزول لتشغيل Python (Pyodide): كود المتعلم ما يجمّد الصفحة، ونقدر نوقفه لو دخل حلقة بلا نهاية.
import { loadPyodide } from '../vendor/pyodide/pyodide.mjs';

let runJson = null;

async function init() {
  const py = await loadPyodide({ indexURL: new URL('../vendor/pyodide/', import.meta.url).href });
  const harness = await (await fetch(new URL('./pyharness.py', import.meta.url))).text();
  py.runPython(harness);
  runJson = py.globals.get('run_json');
}

self.onmessage = async ({ data }) => {
  const { id, type, payload } = data;
  try {
    if (type === 'init') {
      await init();
      self.postMessage({ id, ok: true });
    } else {
      self.postMessage({ id, ok: true, result: JSON.parse(runJson(JSON.stringify(payload))) });
    }
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err && err.message ? err.message : err) });
  }
};
