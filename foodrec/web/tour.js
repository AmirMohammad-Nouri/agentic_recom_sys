// Guided tours: hint bubbles that point at parts of the app (steps in tour.json), one tour for
// users and one for administrators. Each starts by itself the first time a person is on that side,
// and from the account menu at any time. Back / Next / Skip; the server remembers that the person
// closed it. The page stays usable.
import { can, emit, h, state, track } from "./lib.js";

let tours = null;
let side = "user";
let current = -1;
let bubble = null;
let marked = null;

async function load() {
  if (!tours) {
    const res = await fetch("/static/tour.json");
    tours = await res.json();
  }
  const demo = state.system?.mode === "demo";
  const cycle = !!state.user_state?.cycle?.enabled;
  return (tours[side] || []).filter((s) => (s.demo === undefined || s.demo === demo) && (s.cycle === undefined || s.cycle === cycle) &&
    (!s.feature || can(s.feature)) && !(s.needs_people && state.monitorEmpty));
}

function waitFor(selector, ms = 5000) {
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

function close(result) {
  clearMark();
  bubble?.remove();
  bubble = null;
  current = -1;
  const key = side === "admin" ? "tour_seen_admin" : "tour_seen";
  if (result && state.user_state && !state.user_state[key]) {
    state.user_state[key] = true;
    track("tour_closed", { result, side });
  }
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
    bubble.style.top = `${window.scrollY + Math.max(pad, Math.min(r.top + 40, window.innerHeight - hgt - pad))}px`;
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
  const here = location.hash.replace("#/", "") || (side === "user" ? "discover" : "");
  if (s.page && here !== s.page) {
    location.hash = `#/${s.page}`;
    await new Promise((r) => setTimeout(r, 450));  // the new page replaces what is on screen
    if (current !== i) return;
  }
  if (s.open === "dock") emit("open_dock");
  for (const sel of s.click || []) {  // e.g. open a person, then one of the tabs of their record
    const target = await waitFor(sel);
    if (current !== i) return;
    if (!target) return show(list, i + dir, dir);
    target.click();
  }
  let el = null;
  if (s.target) {
    el = await waitFor(s.target);
    if (current !== i) return;                  // the user moved on meanwhile
    if (!el) return show(list, i + dir, dir);   // not on screen for this role: skip the step
    // a panel taller than the window: show its top straight away, so the hint stays put beside it
    const tall = el.getBoundingClientRect().height > window.innerHeight / 2;
    el.style.scrollMarginTop = tall ? "70px" : "";
    el.scrollIntoView(tall ? { block: "start" } : { block: "center", behavior: "smooth" });
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

/** Start a tour; `auto`: only if this person has not finished or skipped this side's tour yet. */
export async function startTour({ auto = false, side: which = "user" } = {}) {
  if (auto && state.user_state?.[which === "admin" ? "tour_seen_admin" : "tour_seen"]) return;
  if (bubble) return;
  side = which;
  const list = await load();
  if (list.length) show(list, 0);
}

window.addEventListener("resize", () => { if (bubble && current >= 0) place(marked); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && bubble) close("skipped"); });
