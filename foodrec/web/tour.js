// Guided tour: hint bubbles that point at parts of the app (steps in tour.json). It starts by
// itself on the demo (once per browser, after onboarding) and from the account menu anywhere.
// Back / Next / Skip; skipped and finished tours are remembered. The page stays usable.
import { demoView, emit, h, safeGet, safeSet, state } from "./lib.js";

const KEY = "foodrec.tour";
let steps = null;
let current = -1;
let bubble = null;
let marked = null;

async function load() {
  if (!steps) {
    const res = await fetch("/static/tour.json");
    steps = (await res.json()).steps || [];
  }
  const view = state.system?.mode === "demo" ? demoView() : null;
  return steps.filter((s) => !s.views || (view && s.views.includes(view)));
}

function waitFor(selector, ms = 2500) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    (function poll() {
      const el = document.querySelector(selector);
      if (el && el.getClientRects().length) return resolve(el);
      if (Date.now() - t0 > ms) return resolve(null);
      setTimeout(poll, 100);
    })();
  });
}

function clearMark() {
  marked?.classList.remove("tour-target");
  marked = null;
}

function close(remember) {
  clearMark();
  bubble?.remove();
  bubble = null;
  current = -1;
  if (remember) safeSet(KEY, remember);
}

function place(el) {
  const pad = 10, w = bubble.offsetWidth, hgt = bubble.offsetHeight;
  if (!el) {  // centred step
    bubble.style.left = `${Math.max(pad, (window.innerWidth - w) / 2)}px`;
    bubble.style.top = `${window.scrollY + Math.max(pad, (window.innerHeight - hgt) / 3)}px`;
    bubble.dataset.arrow = "none";
    return;
  }
  const r = el.getBoundingClientRect();
  if (r.height > window.innerHeight / 2) {  // a tall panel (the assistant): put the hint beside it
    const left = r.left > w + 2 * pad ? r.left - w - pad : Math.min(r.right + pad, window.innerWidth - w - pad);
    bubble.style.left = `${Math.max(pad, left)}px`;
    bubble.style.top = `${window.scrollY + Math.max(pad, r.top + 40)}px`;
    bubble.dataset.arrow = "none";
    return;
  }
  const below = r.bottom + hgt + pad < window.innerHeight || r.top < hgt + pad;
  const top = below ? r.bottom + pad : r.top - hgt - pad;
  const left = Math.min(Math.max(pad, r.left + r.width / 2 - w / 2), window.innerWidth - w - pad);
  bubble.style.left = `${left}px`;
  bubble.style.top = `${window.scrollY + Math.max(pad, top)}px`;
  bubble.dataset.arrow = below ? "up" : "down";
}

async function show(list, i, dir = 1) {
  if (i < 0) i = 0;
  if (i >= list.length) return close("done");
  current = i;
  const s = list[i];
  clearMark();
  if (s.page && (location.hash.replace("#/", "") || "discover") !== s.page) location.hash = `#/${s.page}`;
  if (s.open === "dock") emit("open_dock");
  let el = null;
  if (s.target) {
    el = await waitFor(s.target);
    if (!el) return show(list, i + dir, dir);  // not on screen for this role: skip the step
    if (current !== i) return;                  // the user moved on meanwhile
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    el.classList.add("tour-target");
    marked = el;
  }
  bubble?.remove();
  bubble = h("div.tour", { role: "dialog", "aria-label": s.title },
    h("div.tour-head", h("b", s.title), h("span.small.muted", `${i + 1} of ${list.length}`)),
    h("p", s.text),
    h("div.tour-foot",
      h("button.btn.ghost.sm", { onclick: () => close("skipped") }, "Skip tour"),
      h("span.grow"),
      i > 0 ? h("button.btn.sm", { onclick: () => show(list, i - 1, -1) }, "Back") : null,
      h("button.btn.primary.sm", { onclick: () => show(list, i + 1, 1) }, i === list.length - 1 ? "Finish" : "Next")));
  document.body.append(bubble);
  setTimeout(() => bubble && place(el), el ? 350 : 0);  // after scrolling
}

/** Start the tour; `auto`: only if this browser has not finished or skipped it yet. */
export async function startTour({ auto = false } = {}) {
  if (auto && safeGet(KEY)) return;
  if (bubble) return;
  const list = await load();
  if (list.length) show(list, 0);
}

window.addEventListener("resize", () => { if (bubble && current >= 0) place(marked); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && bubble) close("skipped"); });
