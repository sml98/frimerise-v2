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
  tl.set([...parcels.slice(1), laser, errors], { autoAlpha: 0 }, 0);
  parcels.forEach((entry, i) => {
    const beat = i * .11;
    if (i) tl.fromTo(entry, { x: -151, autoAlpha: 0 }, {
      x: 0, autoAlpha: 1, duration: t(.045), immediateRender: false,
    }, t(beat));
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
  tl.fromTo(panel, { scale: .95, y: 17, autoAlpha: .95 }, {
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
    if (index) tl.fromTo(row, { autoAlpha: 0, x: -15 }, { autoAlpha: 1, x: 0, duration: t(.105) }, start);
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

function open_patent_network(tl, seconds, payload) {
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
  tl.fromTo(seal, { scale: .84 }, {
    scale: 1, transformOrigin: "center", duration: t(.23), ease: "back.out(1.65)",
  }, 0);
  tl.to(seal, { scale: 1.045, duration: .075, repeat: 1, yoyo: true }, payload.stampAt ?? t(.14));
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

function camera(tl, seconds, transition, enterAt, exitAt) {
  const stage = document.querySelector(".stage");
  tl.fromTo(stage, { scale: 1, rotationX: 1.5, rotationY: -2, transformPerspective: 1200 }, {
    scale: 1.04, rotationX: -2, rotationY: 2.3, duration: seconds, ease: "none",
  }, 0);
  tl.fromTo(".grid-plane", { transformPerspective: 740, rotationX: 51, x: -14, y: -13 }, {
    x: 24, y: 35, rotationX: 55, duration: seconds, ease: "none",
  }, 0);
  tl.fromTo(".backdrop", { scale: 1.03, xPercent: -.5, yPercent: .2 }, {
    scale: 1.13, xPercent: 1.2, yPercent: -.5, duration: seconds, ease: "none",
  }, 0);
  tl.fromTo(".glow-amber", { opacity: .55, xPercent: -4, scale: .94 }, {
    opacity: 1, xPercent: 0, scale: 1.09, duration: seconds / 2, yoyo: true, repeat: 1, ease: "sine.inOut",
  }, 0);
  tl.fromTo(".glow-emerald", { opacity: 1, yPercent: -3, scale: 1 }, {
    opacity: .58, yPercent: 1, scale: 1.14, duration: seconds / 2, yoyo: true, repeat: 1, ease: "sine.inOut",
  }, 0);
  tl.fromTo(".wipe", { xPercent: -112, opacity: .78 }, {
    xPercent: 510, opacity: .2, duration: transition, ease: "power2.inOut",
  }, enterAt);
  tl.fromTo(".wipe", { xPercent: -112, opacity: .2 }, {
    xPercent: 510, opacity: .85, duration: transition, ease: "power2.inOut", immediateRender: false,
  }, exitAt);
  tl.fromTo(graphic, { scale: 1.07 }, { scale: 1, duration: transition, ease: "power3.out" }, 0);
  tl.to(graphic, { scale: 1.055, duration: transition, ease: "power2.in" }, exitAt);
  tl.fromTo([kicker, heading, kpi, subtitle], { x: -8 }, {
    x: 0, duration: transition, stagger: .018, ease: "power3.out",
  }, 0);
}

const palettes = { amber: "#f59e0b", emerald: "#34e5ab", cyan: "#56d9f6", crimson: "#fb687a", violet: "#ba9dff" };

function stagePanel(className) {
  return div("module-panel " + className, graphic);
}

function enter(tl, nodes, seconds, start = .11, stride = .08) {
  tl.fromTo(nodes, { y: 19, autoAlpha: 0, scale: .97 }, {
    y: 0, autoAlpha: 1, scale: 1, duration: seconds * .17, stagger: seconds * stride,
    ease: "power3.out",
  }, start * seconds);
}

function count(tl, target, value, seconds, { start = .12, end = .64, unit = "", display } = {}) {
  const data = { n: 0 };
  tl.to(data, { n: value, duration: seconds * (end - start), ease: "power2.out", onUpdate: () => {
    const finished = Math.abs(data.n - value) < .001;
    target.textContent = finished && display ? display : formatter.format(finished ? value : Math.round(data.n)) + unit;
  } }, seconds * start);
  target.textContent = "0" + unit;
}

function kpi_hero(tl, seconds, { data }) {
  const panel = stagePanel("hero-module");
  const number = div("module-number", panel);
  const delta = div("module-tag", panel, data.delta);
  const context = div("module-context", panel, data.context);
  enter(tl, [delta, context], seconds);
  count(tl, number, data.value, seconds, {unit: data.unit});
}

function generic_bars(tl, seconds, { bars, data }) {
  const chart = stagePanel("bars-module");
  const max = Math.max(...bars.map(({ value }) => value));
  bars.forEach(({ label, value, display }, i) => {
    const row = div("comparison-row", chart);
    const header = div("bar-header", row);
    div("bar-label", header, label);
    const number = div("bar-value", header);
    const fill = div("bar-fill", div("bar-track", row));
    fill.style.width = (100 * value / max) + "%";
    const start = .12 + i * .065;
    if (i) enter(tl, row, seconds, start);
    tl.fromTo(fill, { scaleX: 0 }, { scaleX: 1, duration: seconds * .37 }, seconds * start);
    count(tl, number, value, seconds, {start, end: start + .37, display});
  });
  if (data.context) enter(tl, div("module-context", chart, data.context), seconds, .65);
}

function line_chart(tl, seconds, { data }) {
  const s = art();
  const low = Math.min(0, ...data.points.map(({ value }) => value));
  const high = Math.max(...data.points.map(({ value }) => value), low + 1);
  const step = 535 / (data.points.length - 1);
  const yAt = (value) => 416 - 304 * (value - low) / (high - low);
  const pts = data.points.map((point, i) => ({ ...point, x: 85 + i * step, y: yAt(point.value) }));
  for (let i = 0; i < 5; i++) {
    const value = low + (high - low) * i / 4;
    const y = yAt(value);
    line(s, 84, y, 625, y, "#536879", { opacity: .56, "stroke-dasharray": "3 6" });
    label(s, formatter.format(Math.round(value)), 48, y + 6, "#aebcca", 19);
  }
  pts.forEach(({ x, label: stamp }, i) => {
    if (pts.length <= 7 || i % 2 === 0 || i === pts.length - 1) label(s, stamp, x, 455, "#aebcca", 18);
  });
  const path = "M" + pts.map(({ x, y }) => x + " " + y).join("L");
  const defs = svg(s, "defs", {});
  const gradient = svg(defs, "linearGradient", { id: "line-fill", x1: "0", x2: "0", y1: "0", y2: "1" });
  svg(gradient, "stop", { offset: "0%", "stop-color": "var(--act)", "stop-opacity": .48 });
  svg(gradient, "stop", { offset: "100%", "stop-color": "var(--act)", "stop-opacity": .015 });
  const clip = svg(defs, "clipPath", { id: "line-reveal" });
  const window = box(clip, 84, 53, 0, 366, white);
  svg(s, "path", { d: path + "L620 416H85Z", fill: "url(#line-fill)", "clip-path": "url(#line-reveal)" });
  const trace = svg(s, "path", { d: path, fill: "none", stroke: "var(--act)", "stroke-width": 5.5,
    "stroke-linecap": "round", "stroke-linejoin": "round", pathLength: 1,
    "stroke-dasharray": 1, "stroke-dashoffset": 1 });
  tl.to(trace, { attr: { "stroke-dashoffset": 0 }, duration: seconds * .52, ease: "none" }, seconds * .12);
  tl.to(window, { attr: { width: 544 }, duration: seconds * .52, ease: "none" }, seconds * .12);
  pts.forEach(({ x, y, peak, value }, i) => {
    const mark = svg(s, "g", {});
    svg(mark, "circle", { cx: x, cy: y, r: peak ? 8 : 4, fill: peak ? "var(--act)" : white,
      stroke: dark, "stroke-width": 2 });
    if (peak) label(mark, formatter.format(value) + data.unit, Math.min(607, x), y - 19, "var(--act)", 23);
    enter(tl, mark, seconds, .16 + i / (pts.length - 1) * .48);
  });
  if (data.context) label(s, data.context, 341, 487, "#aebcca", 17);
}

function timeline_steps(tl, seconds, { data }) {
  const panel = stagePanel("timeline-module");
  const track = div("timeline-items", panel);
  const rail = div("timeline-rail", track);
  track.style.setProperty("--columns", data.items.length);
  const items = data.items.map(({ label, date, detail }) => {
    const item = div("event-card", track);
    div("event-date", item, date);
    div("event-pin", item);
    div("event-title", item, label);
    if (detail) div("event-note", item, detail);
    return item;
  });
  tl.fromTo(rail, { scaleX: 0 }, { scaleX: 1, duration: seconds * .61, ease: "none" }, seconds * .10);
  enter(tl, items.slice(1), seconds, .13, .48 / data.items.length);
}

function split_vs(tl, seconds, { data }) {
  const panel = stagePanel("split-module");
  const sides = data.items.map(({ label, detail, date }) => {
    const card = div("comparison-side", panel);
    div("module-tag", card, date);
    div("split-title", card, label);
    div("module-context", card, detail);
    return card;
  });
  const mark = div("vs-mark", panel, "×");
  enter(tl, [sides[1], mark], seconds, .11, .115);
}

function process_flow(tl, seconds, { data }) {
  const panel = stagePanel("process-module");
  const rail = div("flow-rail", panel);
  const pulse = div("flow-pulse", rail);
  const rows = data.items.map(({ label, detail }, index) => {
    const row = div("flow-row", panel);
    div("flow-step", row, String(index + 1).padStart(2, "0"));
    const copy = div("flow-copy", row);
    div("event-title", copy, label);
    if (detail) div("event-note", copy, detail);
    return row;
  });
  enter(tl, rows.slice(1), seconds, .12);
  return (p) => {
    pulse.style.top = Math.max(0, ((p - .2) * 2.6) % 1) * 100 + "%";
  };
}

function network_nodes(tl, seconds, { data }) {
  const s = art();
  const links = svg(s, "g", {});
  const layer = svg(s, "g", {});
  const sparks = svg(s, "g", {});
  const paths = data.items.map(({ label: name, icon }, index) => {
    const angle = (index / data.items.length - .25) * Math.PI * 2;
    const x = 340 + Math.cos(angle) * 212;
    const y = 242 + Math.sin(angle) * 155;
    const route = svg(links, "path", {
      d: "M340 242Q" + ((340 + x) / 2 + (y - 242) * .15) + " " + ((242 + y) / 2 - (x - 340) * .17)
         + " " + x + " " + y,
      fill: "none", stroke: "var(--act)", "stroke-width": 2.3,
      pathLength: 1, "stroke-dasharray": 1, "stroke-dashoffset": 1, opacity: .66,
    });
    const node = svg(layer, "g", {});
    svg(node, "circle", { cx: x, cy: y, r: 30, fill: "#152331", stroke: "var(--act)", "stroke-width": 2.6 });
    svg(node, "use", { href: "vendor/editorial-icons.svg#" + icon, x: x - 13, y: y - 13,
      width: 26, height: 26, fill: "none", stroke: white, "stroke-width": 1.8,
      "stroke-linecap": "round", "stroke-linejoin": "round" });
    label(node, name.length > 21 ? name.slice(0, 20) + "…" : name, x, y + 51, white, 18);
    const spark = svg(sparks, "circle", { cx: 340, cy: 242, r: 4.8, fill: "var(--act)", opacity: 0 });
    tl.to(route, { attr: { "stroke-dashoffset": 0 }, duration: seconds * .22 }, seconds * (.18 + index * .043));
    enter(tl, node, seconds, .24 + index * .043);
    return { route, spark, length: route.getTotalLength() };
  });
  const center = svg(s, "g", {});
  box(center, 247, 208, 186, 69, "#102332", {rx: 14, stroke: "var(--act)", "stroke-width": 3});
  label(center, data.context || "Rede", 340, 251, white, 21);
  return (p) => paths.forEach(({ route, spark, length }, i) => {
    const elapsed = p - .29 - .047 * i;
    const phase = Math.max(0, elapsed * 4.6) % 1;
    const point = route.getPointAtLength(phase * length);
    spark.setAttribute("cx", point.x);
    spark.setAttribute("cy", point.y);
    spark.setAttribute("opacity", elapsed > 0 ? .48 + .52 * Math.sin(Math.PI * phase) : 0);
  });
}

function checklist_reveal(tl, seconds, { data, captions }) {
  const panel = stagePanel("checklist-module");
  const words = captions.flatMap((block) => block.words);
  const key = (text) => text.normalize("NFD").replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();
  let previous = .07;
  data.items.forEach(({ label, detail }, i) => {
    const row = div("check-row", panel);
    div("check-icon", row, "✓");
    const copy = div("flow-copy", row);
    div("event-title", copy, label);
    if (detail) div("event-note", copy, detail);
    const first = key(label.split(" ")[0]);
    const word = words.find(({ text, start }) => key(text) === first && start / seconds > previous);
    const moment = Math.min(.79, Math.max(previous + .075, word ? word.start / seconds : .14 + i * .125));
    if (i) enter(tl, row, seconds, moment);
    previous = moment;
  });
}

function quote_callout(tl, seconds, { data }) {
  const panel = stagePanel("quote-module");
  div("quote-mark", panel, "“");
  const quote = div("quote-text", panel);
  const tokens = data.quote.split(/\s+/).filter(Boolean).map((word, i) => {
    if (i) quote.append(" ");
    const span = document.createElement("span");
    span.textContent = word;
    quote.append(span);
    return span;
  });
  const credit = div("module-context", panel, data.author);
  if (tokens.length > 2) enter(tl, tokens.slice(2), seconds, .12, Math.min(.045, .57 / tokens.length));
  enter(tl, credit, seconds, .68);
}

function news_headline(tl, seconds, { data, headline }) {
  const panel = stagePanel("news-module");
  const paper = div("news-paper", panel);
  div("news-source", paper, data.source);
  div("news-title", paper, data.headline || headline);
  const body = div("news-body", paper);
  const offset = data.highlight ? data.excerpt.indexOf(data.highlight) : -1;
  if (offset < 0) {
    body.textContent = data.excerpt;
  } else {
    body.append(data.excerpt.slice(0, offset));
    const mark = document.createElement("mark");
    mark.textContent = data.highlight;
    body.append(mark, data.excerpt.slice(offset + data.highlight.length));
    tl.fromTo(mark, { backgroundSize: "0% 100%" }, {
      backgroundSize: "100% 100%", duration: seconds * .34, ease: "power1.inOut",
    }, seconds * .32);
  }
  tl.fromTo(paper, { rotation: -3, y: 12, scale: .95 }, {
    rotation: -1.1, y: 0, scale: 1, duration: seconds * .21, ease: "power2.out",
  }, 0);
}

function creditText(credit) {
  return credit ? [credit.source, credit.author, credit.license].filter(Boolean).join(" · ") : "";
}

function mediaPhoto(parent, payload) {
  const frame = div("photo-frame", parent);
  if (payload.media.photo) {
    const image = document.createElement("img");
    image.alt = payload.data.caption;
    image.src = payload.media.photo;
    frame.append(image);
  } else {
    div("photo-empty", frame, "Imagem indisponível");
  }
  return frame;
}

function photo_spotlight(tl, seconds, payload) {
  const panel = stagePanel("photo-module");
  const frame = mediaPhoto(panel, payload);
  const [x, y] = payload.data.focus;
  if (payload.media.photo) {
    const focus = div("photo-focus", frame);
    focus.style.left = x * 100 + "%";
    focus.style.top = y * 100 + "%";
    tl.fromTo(focus, { autoAlpha: 0, scale: .52 }, { autoAlpha: 1, scale: 1,
      duration: seconds * .29, ease: "back.out(1.35)" }, seconds * .38);
  }
  const copy = div("photo-meta", panel);
  div("event-title", copy, payload.data.caption);
  div("media-credit", copy, creditText(payload.media.photoCredit));
  tl.fromTo(frame, { rotationY: -7, rotationX: 4, transformPerspective: 900, scale: .94 }, {
    rotationY: 0, rotationX: 0, scale: 1, duration: seconds * .42,
  }, 0);
  enter(tl, copy, seconds, .27);
}

function photo_split_kpi(tl, seconds, payload) {
  const panel = stagePanel("photo-split-module");
  const photo = div("photo-module", panel);
  mediaPhoto(photo, payload);
  const meta = div("photo-meta", photo);
  div("photo-note", meta, payload.data.caption);
  div("media-credit", meta, creditText(payload.media.photoCredit));
  const stats = div("photo-stats", panel);
  const number = div("module-number", stats);
  div("module-context", stats, payload.data.context);
  count(tl, number, payload.data.value, seconds, {unit: payload.data.unit});
  enter(tl, [meta, stats], seconds, .09, .065);
}

function gauge_meter(tl, seconds, { data }) {
  const s = art();
  const settings = { d: "M142 322 A198 198 0 0 1 538 322", fill: "none", "stroke-width": 22,
    "stroke-linecap": "round", pathLength: 100 };
  svg(s, "path", { ...settings, stroke: "#425365" });
  const meter = svg(s, "path", { ...settings, stroke: "var(--act)", "stroke-dasharray": 100,
    "stroke-dashoffset": 100 });
  tl.to(meter, { attr: { "stroke-dashoffset": 100 - data.value }, duration: seconds * .50 }, seconds * .12);
  const number = label(s, "0%", 340, 298, "var(--act)", 92);
  count(tl, number, data.value, seconds, { unit: "%" });
  label(s, "0%", 143, 365, "#aebcca", 20);
  label(s, "100%", 538, 365, "#aebcca", 20);
  label(s, data.context, 340, 418, white, 24);
}

function terminal_code(_tl, _seconds, { data }) {
  const panel = stagePanel("terminal-module");
  const window = div("terminal-window", panel);
  div("terminal-title", window, data.context);
  const output = div("terminal-output", window);
  const lines = data.lines.map(() => div("terminal-line", output));
  const total = data.lines.reduce((sum, line) => sum + line.length + 3, 0);
  return (p) => {
    const initial = data.lines[0].length + 3;
    let visible = initial + Math.floor(Math.min(1, Math.max(0, (p - .035) / .865)) * (total - initial));
    lines.forEach((row, i) => {
      const text = data.lines[i];
      let state = "";
      if (visible > 0) {
        state = text.slice(0, visible);
        if (visible < text.length + 3 && Math.floor(p * 18) % 2 === 0) state += " ▋";
      }
      row.textContent = state;
      visible -= text.length + 3;
    });
  };
}

const modules = { kpi_hero, bar_comparison: generic_bars, line_chart, timeline_steps, split_vs,
  process_flow, network_nodes, checklist_reveal, quote_callout, news_headline,
  photo_spotlight, photo_split_kpi, gauge_meter, terminal_code };

function setupBackground(payload) {
  const plate = document.querySelector(".backdrop");
  const credit = document.querySelector(".background-credit");
  plate.replaceChildren();
  document.body.dataset.bg = payload.bg_mode;
  document.body.style.setProperty("--act", palettes[payload.accent] || amber);
  credit.textContent = "";
  if (payload.bg_mode === "broll_video") {
    credit.textContent = payload.media.videoCredit ? "Arquivo ilustrativo · " + creditText(payload.media.videoCredit) : "";
  }
  if (["photo_duotone", "broll_dark"].includes(payload.bg_mode) && payload.media.photo) {
    const image = document.createElement("img");
    image.alt = "";
    image.src = payload.media.photo;
    plate.append(image);
    if (!["photo_spotlight", "photo_split_kpi"].includes(payload.module)) {
      credit.textContent = creditText(payload.media.photoCredit);
    }
  }
}

function applyOverlay(tl, seconds, payload) {
  const zone = document.querySelector(".overlays");
  zone.replaceChildren();
  if (!payload.overlay_fx) return;
  const text = payload.overlay_fx === "stamp_badge" ? payload.metric_badge : "";
  const fx = div("overlay overlay-" + payload.overlay_fx, zone, text);
  if (payload.overlay_fx === "laser_scan") {
    tl.fromTo(fx, { yPercent: 0, opacity: 0 }, { yPercent: 1500, opacity: .9,
      duration: seconds * .48, ease: "none" }, seconds * .27);
    tl.to(fx, { opacity: 0, duration: seconds * .1 }, seconds * .73);
  } else {
    const at = payload.overlay_fx === "stamp_badge" ? (payload.stampAt ?? seconds * .61) : seconds * .61;
    tl.fromTo(fx, { autoAlpha: 0, scale: .55, rotation: -7 }, { autoAlpha: 1, scale: 1, rotation: 0,
      duration: seconds * .20, ease: "back.out(1.6)" }, at);
  }
}

const components = { barcode_to_qr, go_board_markers, bar_comparison, open_patent_network };
const badges = {
  barcode_to_qr: "10 LEITURAS → 1",
  go_board_markers: "RAZÃO 1:1:3:1:1",
  bar_comparison: "✓ LEITURA OK · 30% DANIFICADO",
};
let composition;

async function setup(payload) {
  composition?.timeline.kill();
  graphic.replaceChildren();
  setupBackground(payload);
  kicker.textContent = payload.kicker;
  heading.textContent = payload.headline;
  kpi.textContent = payload.kpi;
  subtitle.textContent = payload.subtitle;
  const header = heading.parentElement;
  const horizontal = innerWidth > innerHeight;
  const gap = parseFloat(getComputedStyle(header).rowGap);
  const used = kicker.offsetHeight + gap + (horizontal ? kpi.offsetHeight + subtitle.offsetHeight + 2 * gap : 0);
  fit(heading, header.clientHeight - used);
  badge.textContent = payload.module
    ? (payload.overlay_fx === "stamp_badge" ? "" : payload.metric_badge)
    : payload.visual_type === "bar_comparison"
      ? badges.bar_comparison : badges[payload.visual_type] && (payload.metric_badge || badges[payload.visual_type]) || "";
  const timeline = gsap.timeline({ paused: true, defaults: { ease: "power2.out" } });
  const transition = payload.transitionSec ?? .28;
  camera(timeline, payload.durationSec, transition, payload.enterAt ?? 0,
    payload.exitAt ?? payload.durationSec - transition);
  const build = payload.module ? modules[payload.module] : components[payload.visual_type];
  const beats = build(timeline, payload.durationSec, payload);
  applyOverlay(timeline, payload.durationSec, payload);
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

window.renderAt = async (tSec, progress, payload) => {
  if (composition?.payload !== payload) composition = await setup(payload);
  const time = Math.min(payload.durationSec, Math.max(0, tSec));
  composition.timeline.seek(time, false);
  composition.beats?.(progress);
  updateCaption(time, composition);
  gsap.ticker.sleep();
};
