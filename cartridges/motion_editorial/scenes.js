"use strict";

const graphic = document.querySelector("#graphic");
const heading = document.querySelector("#headline");
const kicker = document.querySelector("#kicker");
const kpi = document.querySelector("#kpi");
const subtitle = document.querySelector("#subtitle");
const badge = document.querySelector("#badge");
const caption = document.querySelector("#caption");
const svgNS = "http://www.w3.org/2000/svg";
const amber = "#f59e0b";
const green = "#34e5ab";
const white = "#f3f5f8";
const dark = "#0b0f17";
const red = "#ff625f";
const formatter = new Intl.NumberFormat("pt-BR", { useGrouping: "always" });

function svg(parent, tag, attributes) {
  const element = document.createElementNS(svgNS, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  parent.append(element);
  return element;
}

function box(parent, x, y, width, height, fill, attributes = {}) {
  return svg(parent, "rect", { x, y, width, height, fill, ...attributes });
}

function line(parent, x1, y1, x2, y2, stroke, attributes = {}) {
  return svg(parent, "line", { x1, y1, x2, y2, stroke, ...attributes });
}

function label(parent, text, x, y, fill, size = 23) {
  const element = svg(parent, "text", {
    x, y, fill, "font-size": size, "font-family": "monospace", "font-weight": 700,
    "text-anchor": "middle", "letter-spacing": "1.2",
  });
  element.textContent = text;
  return element;
}

function art(parent = graphic, viewBox = "0 0 680 510") {
  return svg(parent, "svg", { viewBox, "aria-hidden": "true" });
}

function div(className, parent, text = "") {
  const element = document.createElement("div");
  element.className = className;
  element.textContent = text;
  parent.append(element);
  return element;
}

function gridPath(rows, origin, cell) {
  const path = [];
  rows.forEach((row, y) => {
    [...row].forEach((bit, x) => {
      if (bit === "1") path.push("M" + (origin + x * cell) + " " + (origin + y * cell)
                                + "h" + cell + "v" + cell + "h-" + cell + "Z");
    });
  });
  return path.join("");
}

function qrAt(parent, x, y, size) {
  const place = svg(parent, "g", { transform: "translate(" + x + " " + y + ")" });
  const symbol = svg(place, "g", { "shape-rendering": "crispEdges" });
  const cell = size / (qrSymbol.modules.length + 8);
  const origin = cell * 4;
  box(symbol, 0, 0, size, size, white);
  svg(symbol, "path", { d: gridPath(qrSymbol.modules, origin, cell), fill: dark });
  return { place, symbol, cell, origin };
}

function viewfinder(parent, x, y, size) {
  const radius = size / 2;
  const arm = size * .139;
  const group = svg(parent, "g", { fill: "none", stroke: green, "stroke-width": 3.4, "stroke-linecap": "round" });
  for (const [dx, dy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
    const cx = x + dx * radius;
    const cy = y + dy * radius;
    svg(group, "path", { d: "M" + (cx - dx * arm) + " " + cy + "H" + cx + "V" + (cy - dy * arm) });
  }
  return group;
}

function scanBeam(parent, x, y, w, color) {
  const g = svg(parent, "g", {});
  box(g, x, y - 5, w, 13, color, { rx: 5, opacity: .17 });
  box(g, x, y, w, 3, color, { rx: 1.5 });
  return g;
}

function parcel(parent, index) {
  const group = svg(parent, "g", {});
  box(group, 133, 115, 414, 249, "#263749", { rx: 18, stroke: "#55718a", "stroke-width": 2 });
  box(group, 148, 130, 384, 219, "#2f4050", { rx: 12, stroke: "#456075" });
  box(group, 188, 157, 304, 151, white, { rx: 5 });
  let x = 210;
  const widths = [3, 9, 4, 12, 7, 14, 5, 5, 11, 15, 6, 4, 14, 5, 12, 10, 5, 10, 9, 5, 10];
  for (const [i, width] of widths.entries()) {
    const bw = widths[(i + index * 5) % widths.length];
    box(group, x, 176, bw, i % 5 === index ? 94 : 84, dark);
    x += bw + 4;
  }
  return group;
}

function barcode_to_qr(tl, seconds) {
  const t = (p) => p * seconds;
  const s = art();
  const wires = svg(s, "g", { opacity: .44 });
  [85, 398].forEach((y) => line(wires, 78, y, 602, y, "#57809a", { "stroke-dasharray": "5 9" }));
  const parcels = [0, 1, 2].map((i) => parcel(s, i));
  const laser = scanBeam(s, 146, 160, 388, red);
  const errors = label(s, "ERRO ×1", 340, 451, red, 22);
  tl.set([...parcels, laser, errors], { autoAlpha: 0 }, 0);
  parcels.forEach((entry, i) => {
    const beat = i * .11;
    tl.fromTo(entry, { x: -151, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: t(.045), immediateRender: false }, t(beat));
    tl.to(entry, { x: 8, yoyo: true, repeat: 3, duration: t(.007), ease: "none" }, t(beat + .057));
    tl.to(entry, { x: 157, autoAlpha: 0, duration: t(.041) }, t(beat + .077));
  });
  tl.to(laser, { autoAlpha: 1, duration: t(.035) }, t(.025));
  tl.to(errors, { autoAlpha: 1, duration: t(.04) }, t(.051));
  tl.to([laser, errors], { autoAlpha: 0, duration: t(.05) }, t(.31));

  const symbol = qrAt(s, 167, 60, 346);
  const dust = svg(s, "g", {});
  const targets = [];
  qrSymbol.modules.forEach((row, y) => [...row].forEach((bit, x) => {
    if (bit === "1" && (x + y * 3) % 4 === 0) targets.push([x, y]);
  }));
  const pieces = targets.map(([x, y], i) => box(
    dust, 205 + 12 * (i % 23), 170 + 34 * (i % 3), 5, 35, i % 3 ? white : amber,
  ));
  tl.set([symbol.symbol, dust], { autoAlpha: 0 }, 0);
  tl.to(dust, { autoAlpha: 1, duration: t(.04) }, t(.335));
  pieces.forEach((piece, i) => {
    const [x, y] = targets[i];
    const size = symbol.cell;
    tl.to(piece, { attr: { x: 167 + symbol.origin + x * size, y: 60 + symbol.origin + y * size,
                          width: size, height: size }, duration: t(.21), ease: "power3.inOut" },
          t(.365 + (i % 10) * .008));
  });
  tl.fromTo(symbol.symbol, { autoAlpha: 0, scale: .94, rotation: -6 }, {
    autoAlpha: 1, scale: 1, rotation: 0, transformOrigin: "center", duration: t(.13), immediateRender: false,
  }, t(.57));
  tl.to(dust, { autoAlpha: 0, duration: t(.1) }, t(.6));
  const hud = viewfinder(s, 340, 233, 379);
  const scan = scanBeam(s, 157, 60, 366, green);
  tl.set([hud, scan], { autoAlpha: 0 }, 0);
  tl.to([hud, scan], { autoAlpha: 1, duration: t(.055) }, t(.696));

  return (p) => {
    const pass = Math.min(2, Math.floor(p / .11));
    errors.textContent = "ERRO ×" + (pass + 1);
    gsap.set(laser, { y: 138 * ((p / .11) % 1), opacity: p < .33 ? .48 + .52 * Math.abs(Math.sin(p * 180)) : 0 });
    gsap.set(scan, { y: 346 * Math.max(0, (p - .69) / .31) });
  };
}

function go_board_markers(tl, seconds) {
  const t = (p) => p * seconds;
  const s = art();
  const panel = svg(s, "g", {});
  box(panel, 106, 26, 389, 389, "#324354", { rx: 13, stroke: "#6f879a", "stroke-width": 2 });
  box(panel, 118, 38, 365, 365, "#233040", { rx: 9, stroke: "#394e61" });
  for (let i = 0; i < 13; i++) {
    const d = i * 26;
    line(panel, 143, 64 + d, 455, 64 + d, "#6d8599", { opacity: .57 });
    line(panel, 143 + d, 64, 143 + d, 376, "#6d8599", { opacity: .57 });
  }
  const arrangement = [[1,2],[7,4],[4,8],[9,6],[3,4],[8,1],[2,6],[10,4],[6,3],[5,1],
                       [10,8],[1,9],[3,12],[6,10],[8,8],[12,5],[4,7],[6,7],[8,6],[11,10]];
  const stones = arrangement.map(([x, y], i) => svg(panel, "circle", {
    cx: 143 + x * 26, cy: 64 + y * 26, r: 10.2, fill: i % 2 ? white : dark, stroke: "#9eabb8",
  }));
  tl.fromTo(panel, { scale: .95, y: 17, autoAlpha: .25 }, {
    scale: 1, y: 0, autoAlpha: 1, transformOrigin: "center", duration: t(.19),
  }, 0);
  tl.fromTo(stones, { autoAlpha: 0, y: -34, scale: .65 }, {
    autoAlpha: 1, y: 0, scale: 1, transformOrigin: "center", duration: t(.115),
    stagger: t(.010), ease: "back.out(1.45)",
  }, t(.024));
  const finders = [[154, 73], [365, 73], [154, 287]].map(([x, y]) => {
    const mark = svg(panel, "g", {});
    box(mark, x, y, 77, 77, dark);
    box(mark, x + 11, y + 11, 55, 55, white);
    box(mark, x + 22, y + 22, 33, 33, dark);
    box(mark, x - 5, y - 5, 87, 87, "none", { rx: 6, stroke: amber, "stroke-width": 3 });
    return mark;
  });
  tl.fromTo(finders, { autoAlpha: 0, scale: .72 }, {
    autoAlpha: 1, scale: 1, transformOrigin: "center", stagger: t(.055), duration: t(.11),
  }, t(.34));
  const ruler = svg(s, "g", {});
  let x = 165;
  const labels = [1, 1, 3, 1, 1].map((ratio, i) => {
    const segment = svg(ruler, "g", {});
    box(segment, x, 441, ratio * 38, 13, i % 2 ? white : amber, { rx: 2 });
    label(segment, String(ratio), x + ratio * 19, 480, white, 19);
    x += ratio * 38;
    return segment;
  });
  tl.fromTo(labels, { autoAlpha: 0, y: 10 }, { autoAlpha: 1, y: 0, duration: t(.09), stagger: t(.021) }, t(.452));
  tl.to([panel, ruler], { x: -128, scale: .71, transformOrigin: "center", duration: t(.17) }, t(.6));
  const lock = svg(s, "g", {});
  const qr = qrAt(lock, 381, 117, 218);
  viewfinder(lock, 490, 226, 301);
  const beam = scanBeam(lock, 357, 123, 266, green);
  const ok = label(lock, "✓ 360° SCAN OK", 490, 438, green, 22);
  tl.fromTo(lock, { autoAlpha: 0, x: 28 }, { autoAlpha: 1, x: 0, duration: t(.088) }, t(.632));
  tl.to(qr.symbol, { rotation: 270, transformOrigin: "center", duration: t(.35), ease: "sine.inOut" }, t(.65));
  tl.fromTo(ok, { autoAlpha: 0 }, { autoAlpha: 1, duration: t(.1) }, t(.752));
  return (p) => gsap.set(beam, { y: 205 * Math.max(0, ((p - .63) / .37)), opacity: .71 + .29 * Math.cos(p * 16) });
}

function bar_comparison(tl, seconds, payload) {
  const t = (p) => p * seconds;
  const chart = div("comparison", graphic);
  const hero = div("comparison-kpi", chart, payload.kpi || "354×");
  const more = document.createElement("small");
  more.textContent = " MAIS DADOS";
  hero.append(more);
  tl.fromTo(hero, { scale: .84, autoAlpha: 0, y: 10 }, {
    scale: 1, autoAlpha: 1, y: 0, duration: t(.16), ease: "back.out(1.35)",
  }, t(.02));
  const rows = div("bar-list", chart);
  const max = Math.max(...payload.bars.map(({ value }) => value));
  payload.bars.forEach(({ label, value, display }, index) => {
    const row = div("comparison-row", rows);
    const header = div("bar-header", row);
    div("bar-label", header, label);
    const number = div("bar-value", header, "0");
    const track = div("bar-track", row);
    const fill = div("bar-fill", track);
    fill.style.width = (100 * value / max) + "%";
    const count = { value: 0 };
    const start = t(.058 + index * .055);
    const duration = t(.22);
    tl.fromTo(row, { autoAlpha: 0, x: -15 }, { autoAlpha: 1, x: 0, duration: t(.105) }, start);
    tl.fromTo(fill, { scaleX: 0 }, { scaleX: 1, duration }, start);
    tl.fromTo(count, { value: 0 }, { value, duration, onUpdate: () => {
      number.textContent = count.value >= value ? display : formatter.format(Math.round(count.value));
    } }, start);
  });

  const demo = div("damage-demo", chart);
  const s = art(demo, "0 0 610 340");
  const right = svg(s, "g", {});
  const qr = qrAt(right, 175, 20, 260);
  const mask = svg(qr.symbol, "path", { d: gridPath(qrSymbol.damage, qr.origin, qr.cell), fill: "#fbbf24" });
  const defs = svg(s, "defs", {});
  const clip = svg(defs, "clipPath", { id: "damage-reveal", clipPathUnits: "userSpaceOnUse" });
  const reveal = box(clip, 260, 0, 260, 260, white);
  mask.setAttribute("clip-path", "url(#damage-reveal)");
  const brackets = viewfinder(right, 305, 150, 290);
  const scanner = scanBeam(right, 158, 22, 294, green);
  tl.fromTo(right, { autoAlpha: 0, y: 20, scale: .87 }, {
    autoAlpha: 1, y: 0, scale: 1, transformOrigin: "center", duration: t(.16),
  }, t(.332));
  tl.to(reveal, { attr: { x: 0 }, duration: t(.288), ease: "power2.inOut" }, t(.417));
  const damageReadout = label(s, "0% DANIFICADO", 305, 318, amber, 23);
  tl.fromTo(damageReadout, { autoAlpha: 0 }, { autoAlpha: 1, duration: t(.08) }, t(.438));
  tl.fromTo([brackets, scanner], { autoAlpha: 0 }, { autoAlpha: 1, duration: t(.09) }, t(.60));
  return (p) => {
    const covered = Math.min(1, Math.max(0, (p - .417) / .288));
    damageReadout.textContent = Math.round(30 * covered) + "% DANIFICADO";
    gsap.set(scanner, { y: 252 * Math.max(0, (p - .6) / .4), opacity: p < .6 ? 0 : .74 });
  };
}

function open_patent_network(tl, seconds) {
  const t = (p) => p * seconds;
  const s = art();
  const points = [[113,135],[340,57],[567,135],[609,255],[567,375],[340,453],[113,375],[71,255]];
  const icons = ["smartphone", "globe", "credit-card", "factory", "ticket", "utensils", "package", "train-front"];
  const spokes = svg(s, "g", {});
  const nodeLayer = svg(s, "g", {});
  const pulses = svg(s, "g", {});
  const links = [];
  const nodes = [];
  const sparks = [];
  points.forEach(([x, y], i) => {
    const path = svg(spokes, "path", {
      d: ["M", 340, 255, "Q", 340 + (x - 340) * .59 + (y - 255) * .22,
          255 + (y - 255) * .40 - (x - 340) * .105, x, y].join(" "),
      fill: "none", stroke: "#21bd93", "stroke-width": 2.2, "stroke-opacity": .78,
      pathLength: 1, "stroke-dasharray": 1, "stroke-dashoffset": 1,
    });
    links.push({ path, length: path.getTotalLength() });
    const mark = svg(nodeLayer, "g", {});
    svg(mark, "circle", { cx: x, cy: y, r: 37, fill: "#163737", stroke: "#28675d", "stroke-width": 1.3 });
    svg(mark, "circle", { cx: x, cy: y, r: 30.5, fill: "#14252b", stroke: green, "stroke-width": 1.8 });
    svg(mark, "use", { href: "vendor/editorial-icons.svg#" + icons[i], x: x - 14, y: y - 14,
      width: 28, height: 28, fill: "none", stroke: white, "stroke-width": 1.8, "stroke-linecap": "round",
      "stroke-linejoin": "round" });
    nodes.push(mark);
    sparks.push(svg(pulses, "circle", { cx: 340, cy: 255, r: 4.6, fill: green, stroke: "#b6fff1", "stroke-width": 1.2 }));
  });
  const seal = svg(s, "g", {});
  box(seal, 190, 201, 300, 108, "#102f2b", { rx: 17, stroke: "#2ce2a387", "stroke-width": 7 });
  box(seal, 196, 207, 288, 96, "#132e2b", { rx: 12, stroke: green, "stroke-width": 1.7 });
  label(seal, "PATENTE ABERTA", 340, 242, green, 19);
  label(seal, "$0 ROYALTIES", 340, 279, white, 31);
  tl.fromTo(seal, { autoAlpha: 0, scale: .67 }, {
    autoAlpha: 1, scale: 1, transformOrigin: "center", duration: t(.23), ease: "back.out(1.65)",
  }, t(.038));
  tl.to(links.map(({ path }) => path), {
    attr: { "stroke-dashoffset": 0 }, duration: t(.17), stagger: t(.022), ease: "power2.out",
  }, t(.35));
  tl.fromTo(nodes, { scale: .46, autoAlpha: 0 }, {
    scale: 1, autoAlpha: 1, transformOrigin: "center", duration: t(.11), stagger: t(.022),
  }, t(.47));
  return (p) => {
    links.forEach(({ path, length }, i) => {
      const phase = Math.max(0, (p - .46 - i * .021) / .186);
      const point = path.getPointAtLength((phase % 1) * length);
      const spark = sparks[i];
      spark.setAttribute("cx", point.x);
      spark.setAttribute("cy", point.y);
      spark.setAttribute("opacity", phase ? Math.min(1, phase * 3) * (.5 + .5 * Math.sin(Math.PI * (phase % 1))) : 0);
    });
  };
}

function fit(element, limit) {
  element.style.fontSize = "";
  let size = parseFloat(getComputedStyle(element).fontSize);
  while (element.scrollHeight > limit && size > 9) {
    size -= 1;
    element.style.fontSize = size + "px";
  }
}

function camera(tl, seconds, transition) {
  const stage = document.querySelector(".stage");
  tl.fromTo(stage, { scale: 1, rotationX: 1.5, rotationY: -2, transformPerspective: 1200 }, {
    scale: 1.04, rotationX: -2, rotationY: 2.3, duration: seconds, ease: "none",
  }, 0);
  tl.fromTo(".grid-plane", { transformPerspective: 740, rotationX: 51, x: -14, y: -13 }, {
    x: 24, y: 35, rotationX: 55, duration: seconds, ease: "none",
  }, 0);
  tl.fromTo(".glow-amber", { opacity: .55, xPercent: -4, scale: .94 }, {
    opacity: 1, xPercent: 0, scale: 1.09, duration: seconds / 2, yoyo: true, repeat: 1, ease: "sine.inOut",
  }, 0);
  tl.fromTo(".glow-emerald", { opacity: 1, yPercent: -3, scale: 1 }, {
    opacity: .58, yPercent: 1, scale: 1.14, duration: seconds / 2, yoyo: true, repeat: 1, ease: "sine.inOut",
  }, 0);
  tl.fromTo(".wipe", { xPercent: 0 }, { xPercent: 103, duration: transition, ease: "power3.inOut" }, 0);
  tl.fromTo(".wipe", { xPercent: -103 }, {
    xPercent: 0, duration: transition, ease: "power3.inOut", immediateRender: false,
  }, seconds - transition);
  tl.fromTo(graphic, { scale: 1.07 }, { scale: 1, duration: transition, ease: "power3.out" }, 0);
  tl.to(graphic, { scale: 1.055, duration: transition, ease: "power2.in" }, seconds - transition);
  tl.fromTo([kicker, heading, kpi, subtitle], { autoAlpha: 0, x: -23 }, {
    autoAlpha: 1, x: 0, duration: seconds * .16, stagger: seconds * .027, ease: "power3.out",
  }, transition * .64);
}

const components = { barcode_to_qr, go_board_markers, bar_comparison, open_patent_network };
const badges = {
  barcode_to_qr: "10 LEITURAS → 1",
  go_board_markers: "RAZÃO 1:1:3:1:1",
  bar_comparison: "✓ LEITURA OK · 30% DANIFICADO",
};
let composition;

function setup(payload) {
  composition?.timeline.kill();
  graphic.replaceChildren();
  kicker.textContent = payload.kicker;
  heading.textContent = payload.headline;
  kpi.textContent = payload.kpi;
  subtitle.textContent = payload.subtitle;
  const header = heading.parentElement;
  const horizontal = innerWidth > innerHeight;
  const gap = parseFloat(getComputedStyle(header).rowGap);
  const used = kicker.offsetHeight + gap + (horizontal ? kpi.offsetHeight + subtitle.offsetHeight + 2 * gap : 0);
  fit(heading, header.clientHeight - used);
  badge.textContent = payload.visual_type === "bar_comparison"
    ? badges.bar_comparison : badges[payload.visual_type] && (payload.metric_badge || badges[payload.visual_type]) || "";
  const timeline = gsap.timeline({ paused: true, defaults: { ease: "power2.out" } });
  camera(timeline, payload.durationSec, payload.transitionSec ?? .28);
  const beats = components[payload.visual_type](timeline, payload.durationSec, payload);
  if (badge.textContent) {
    timeline.fromTo(badge, { autoAlpha: 0, y: 14, scale: .86 }, {
      autoAlpha: 1, y: 0, scale: 1, duration: .145 * payload.durationSec, ease: "back.out(1.65)",
    }, (payload.visual_type === "go_board_markers" ? .52 : .71) * payload.durationSec);
  }
  return { payload, timeline, beats, captionIndex: -2, spans: [] };
}

function updateCaption(time, state) {
  const index = state.payload.captions.findIndex(({ start, end }) => start <= time && time < end);
  const words = state.payload.captions[index]?.words ?? [];
  if (index !== state.captionIndex) {
    caption.replaceChildren();
    state.spans = words.map(({ text }, i) => {
      const span = document.createElement("span");
      span.textContent = text;
      if (i) caption.append(" ");
      caption.append(span);
      return span;
    });
    fit(caption, caption.parentElement.clientHeight);
    state.captionIndex = index;
  }
  const active = words.findLastIndex(({ start, end }) => start <= time && time < end);
  state.spans.forEach((span, i) => span.classList.toggle("spoken", i === active));
}

window.renderAt = (tSec, progress, payload) => {
  if (composition?.payload !== payload) composition = setup(payload);
  const time = Math.min(payload.durationSec, Math.max(0, tSec));
  composition.timeline.seek(time, false);
  composition.beats(progress);
  updateCaption(time, composition);
  gsap.ticker.sleep();
};
