// OpenHoop: traduzione dell'interfaccia. La pagina è scritta in italiano; qui ogni testo visibile (nodi di
// testo, title, placeholder, alert/confirm/prompt) viene cercato nel dizionario della lingua scelta.
// Chiavi del dizionario = frase italiana esatta. Con "{}" nella chiave è un modello: "{}" prende un pezzo
// qualsiasi (numeri, nomi), nella traduzione si rimette con {1}, {2}… (nell'ordine della chiave) o "{}" in fila.
// Il server mette prima di questo file: window.LINGUA = "en"; window.DIZ = {...}
(function () {
  const L = window.LINGUA || "it", D = window.DIZ || {};
  window.t = s => s;                                   // per il codice: t("frase") -> traduzione
  if (L === "it" || !Object.keys(D).length) return;
  const esatte = new Map(), modelli = [];
  for (const [k, v] of Object.entries(D)) {
    if (!v) continue;
    if (k.includes("{}")) {
      const pezzi = k.split("{}"), lungo = pezzi.reduce((a, b) => (b.length > a.length ? b : a), "");
      const re = new RegExp("^" + pezzi.map(p => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("(.*?)") + "$", "s");
      modelli.push({ re, v, lungo, tot: pezzi.join("").length });
    } else esatte.set(k, v);
  }
  modelli.sort((a, b) => b.tot - a.tot);                // prima i modelli più specifici
  const cache = new Map();
  function tr(s) {
    if (!s) return s;
    const m0 = s.match(/^(\s*)([\s\S]*?)(\s*)$/), x = m0[2].replace(/\s+/g, " ");
    if (!x || !/[A-Za-zÀ-ú]/.test(x)) return s;
    let r = esatte.get(x);
    if (r === undefined) {
      if (cache.has(x)) r = cache.get(x);
      else {
        r = null;
        for (const m of modelli) {
          if (m.lungo && !x.includes(m.lungo)) continue;
          const g = x.match(m.re);
          if (!g) continue;
          const arg = g.slice(1).map(tr);
          let n = 0;
          r = m.v.replace(/\{(\d*)\}/g, (_, i) => (i ? arg[+i - 1] ?? "" : arg[n++] ?? ""));
          break;
        }
        if (cache.size > 5000) cache.clear();
        cache.set(x, r);
      }
    }
    return r == null ? s : m0[1] + r + m0[3];
  }
  window.t = tr;
  const ATTR = ["title", "placeholder", "aria-label"];
  function nodo(n) {
    if (n.nodeType === 3) {
      const p = n.parentNode;
      if (p && (p.nodeName === "SCRIPT" || p.nodeName === "STYLE" || p.closest?.("[data-no-tr]"))) return;
      const v = tr(n.data);
      if (v !== n.data) n.data = v;
    } else if (n.nodeType === 1) {
      if (n.nodeName === "SCRIPT" || n.nodeName === "STYLE" || n.hasAttribute("data-no-tr")) return;
      for (const a of ATTR) if (n.hasAttribute(a)) { const o = n.getAttribute(a), v = tr(o); if (v !== o) n.setAttribute(a, v); }
      if (n.nodeName === "INPUT" && (n.type === "button" || n.type === "submit") && n.value) { const v = tr(n.value); if (v !== n.value) n.value = v; }
      for (let c = n.firstChild; c; c = c.nextSibling) nodo(c);
    }
  }
  new MutationObserver(ms => {
    for (const m of ms) {
      if (m.type === "characterData") nodo(m.target);
      else if (m.type === "attributes") attr(m.target, m.attributeName);
      else for (const a of m.addedNodes) nodo(a);
    }
  }).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTR });
  function attr(el, a) { if (el.closest && el.closest("[data-no-tr]")) return; const o = el.getAttribute(a), v = tr(o); if (o != null && v !== o) el.setAttribute(a, v); }
  document.addEventListener("DOMContentLoaded", () => { nodo(document.body); document.title = tr(document.title); });
  const al = window.alert, co = window.confirm, pr = window.prompt;
  window.alert = m => al.call(window, tr(String(m)));
  window.confirm = m => co.call(window, tr(String(m)));
  window.prompt = (m, d) => pr.call(window, tr(String(m)), d);
  document.documentElement.lang = L;
})();
