// Foreground occlusion follows the layer separation in Lemo-Opuscar's MIT rig.js.
// Actors are image assets. Motion is computed from scene-local t.
const root = document.querySelector("#root");
const art = document.querySelector("#art");
const stage = "0 0 720 1280";
const clamp = (x) => Math.max(0, Math.min(1, x));
const ease = (x) => x < .5 ? 4*x*x*x : 1 - (-2*x+2)**3/2;
const ellipse = (cx, cy, rx, ry, fill) => `<ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}" fill="${fill}"/>`;
const svg = (body, klass = "fill") => `<svg class="${klass}" viewBox="${stage}" preserveAspectRatio="xMidYMid slice" aria-hidden="true">${body}</svg>`;
const escape = (s = "") => String(s).replace(/[&<>"']/g, a => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[a]);
const seeded = (i, salt = 0) => ((Math.sin(i * 99.13 + salt * 17.33) * 43317.44) % 1 + 1) % 1;
const motion = (t, span, offset = 0) => ease(clamp((t-offset)/span));
const curve = "M7 41 Q25 9 74 11 Q139 13 161 44 Q183 82 136 92 Q52 105 19 88 Q0 79 7 41";
const line = "M8 33 Q31 24 48 26 T93 20 T146 23 T175 10";

function grain() {
  return svg('<defs><filter id="grain"><feTurbulence baseFrequency=".59" numOctaves="2" seed="9" stitchTiles="stitch"/></filter></defs><rect width="720" height="1280" filter="url(#grain)"/>', "grain");
}
function printImage(path, klass, id, role = true) {
  if (!path) return "";
  return `<img src="${escape(path)}" class="${klass}" ${role ? `data-qc-image="${id}"` : ""} alt="">`;
}
function paperLandscape(setting, darkness) {
  const cemetery = setting === "graveyard_night";
  const outdoor = cemetery || ["village_road", "open_field", "city_street"].includes(setting);
  const hue = setting === "city_street" ? "#393142" : "#1d3237";
  const tombs = [85,217,380,558,674].map((x,i) => {
    const y=734+(i%3)*39;
    return `<path d="M${x-32} ${y+132}v-96q32-64 72-8l13 104z" fill="${i%2 ? "#353f3e" : "#566264"}" stroke="#93a09a" stroke-width="2"/>
      <path d="M${x+8} ${y+35}v42m-15-28h30" stroke="#131d20" stroke-width="5" opacity=".55"/>`;
  }).join("");
  const houses = Array.from({length: 7}, (_, i) => {
    const x=i*120-42, y=690+55*seeded(i,3);
    return `<path d="M${x} ${y+159}V${y}l65-71 65 74v153z" fill="${i%2 ? "#544538":"#38353a"}"/>
      <path d="M${x+30} ${y+39}h21v36h-21zm45 17h23v42h-23z" fill="#b38758" opacity=".62"/>`;
  }).join("");
  const grass = Array.from({length: 32}, (_, i) => {
    const x= i*24-25, y=1019+seeded(i,8)*48;
    return `<path d="M${x} 1260l-7 ${y-1260} 13 81 18-106 -9 169 19-53 -12 169z" fill="#111a1d"/>`;
  }).join("");
  const fences = Array.from({length: 11}, (_, i) => {
    const x = -31 + i * 79;
    return `<path d="M${x} 1091v-242l12-19 8 19v242z" fill="#102126" stroke="#344146" stroke-width="2"/>`;
  }).join("");
  return svg(`<defs>
   <linearGradient id="sky" x2=".3" y2="1"><stop stop-color="${hue}"/><stop offset="1" stop-color="#102025"/></linearGradient>
   <radialGradient id="lamp"><stop stop-color="#ffcf79" stop-opacity=".67"/><stop offset="1" stop-color="#ffcc71" stop-opacity="0"/></radialGradient>
   <radialGradient id="edges"><stop offset=".43" stop-color="transparent"/><stop offset="1" stop-color="#0e0e13" stop-opacity=".88"/></radialGradient>
  </defs><rect width="720" height="1280" fill="${outdoor ? "url(#sky)" : "#252126"}"/>
  <g id="scenery-back">${outdoor ? `${ellipse(519,305,73,77,"#ebc985")}<path d="M-20 685l155-71 75 18 89-120 90 54 134-109 34 39 192 36v720H-20z" fill="#273738"/>` :
    '<path d="M0 208L720 116v792L0 790z" fill="#322927"/><path d="M0 330L720 207M0 471L720 363M0 657L720 539" stroke="#524037" stroke-width="5"/>'}</g>
  <g id="scenery-mid">${cemetery ? tombs : outdoor && setting !== "open_field" ? houses :
    outdoor ? '<path d="M0 740l108-48 140 41 153-72 319 118v501H0z" fill="#35423a"/>' :
    '<path d="M0 714L720 622v450L0 1129z" fill="#56372e" stroke="#a27a5c" stroke-width="6"/>'}</g>
  <g id="scenery-fog" opacity=".40"><path d="M-180 889q176-124 366-9t395-37 331-12v150q-182-104-352 20T174 997q-154-63-354 21z" fill="#536365"/></g>
  <g id="scenery-front">${outdoor ? grass : '<path d="M-20 1174L720 978v332H-20z" fill="#1a1418"/>'}
    ${cemetery ? `<path d="M-30 917L752 860M-30 963L752 912" stroke="#102126" stroke-width="13"/>${fences}` : ""}</g>
  ${ellipse(365,591,515,670,"url(#edges)")}
  ${darkness ? '<ellipse id="archive-lamp" cx="341" cy="531" rx="290" ry="300" fill="url(#lamp)"/>' : ""}`);
}

function actionArt(data) {
  return paperLandscape(data.setting, data.action === "inspect_with_lantern")
    + data.assets.actors.map((path, i) =>
      `<figure class="actor stock" data-actor="${i}"><img src="${escape(path)}" data-source-asset alt=""></figure>`
    ).join("");
}

function puppetAt(t, p, data) {
  const walking = data.action === "walk_to_target" || data.action === "flee_run";
  const running = data.action === "flee_run";
  const step = running ? data.runPeriod : data.stepPeriod;
  const progress = ease(clamp(p / data.actionStop));
  for (const actor of art.querySelectorAll("[data-actor]")) {
    const i = Number(actor.dataset.actor);
    const spacing = (i - (data.assets.actors.length - 1) / 2) * 24;
    const movement = walking ? (running ? 20 - 65 * progress : -24 + 38 * progress) : 0;
    const rise = walking && p < data.actionStop ? -1.2 * Math.abs(Math.sin((t - data.stepStart) * Math.PI / step)) : 0;
    const sway = Math.sin(t * 2.5 + i) * 1.7;
    const lean = data.action === "dig_or_open" ? -5 * Math.sin(progress * 2 * Math.PI) : 0;
    actor.style.transform = `translate(${spacing + movement}vw,${rise + i % 2 * 2}vh) rotate(${sway + lean}deg) scale(${1 - i * .08})`;
  }
}

let data;
function initialize(model) {
  data=model;
  const m=data.module;
  root.className = ({
    action_cutout_puppet:"dark", dark_archive:"dark", full_bleed_macro:"full",
    impact_slam:"dark", evidence_pan:"evidence", newspaper_clipping:"light",
    photo_cutout:data.photoBackdrop === "dark" ? "dark" : "light",
  })[m] || "";
  document.querySelector("#mast").innerHTML = data.showHeader && data.headline
    ? `<strong data-qc-text="cabeçalho">${escape(data.headline)}</strong>` : "";
  if (m === "action_cutout_puppet") {
    art.innerHTML=actionArt(data);
    const foreground=art.querySelector("#scenery-front");
    const frontSVG=art.querySelector("svg").cloneNode(false);
    frontSVG.append(foreground);
    art.append(frontSVG);
  }
  if (m === "full_bleed_macro") {
    const {x,y} = data.focus;
    const cx=720*x, cy=1280*y;
    const markY=cy-(data.mark === "underline" ? 3 : 48);
    art.innerHTML=svg(`<defs><radialGradient id="macro-shadow" gradientUnits="userSpaceOnUse" cx="${cx}" cy="${cy}" r="177">
        <stop offset=".22" stop-color="#140d09" stop-opacity="0"/><stop offset="1" stop-color="#140d09" stop-opacity=".75"/>
      </radialGradient></defs>
      <g id="macro"><image href="${escape(data.assets.primary)}" width="720" height="1280" preserveAspectRatio="xMidYMid slice"/>
        <rect width="720" height="1280" fill="url(#macro-shadow)"/>
        <g transform="translate(${cx-53} ${markY}) scale(${data.mark === "underline" ? .61 : .76})"><path id="ink"
          d="${data.mark === "underline" ? line : curve}" fill="none" stroke="#a92725" stroke-width="5"
          stroke-linecap="round" pathLength="1"/></g></g>`);
  }
  if (m === "dark_archive") {
    art.innerHTML=paperLandscape("archive_desk",true)
      + `<figure class="hero stock${data.lanternFocus ? " lantern-hero" : ""}">${printImage(data.assets.primary,"","arquivo")}</figure>`
      + (data.label ? `<div class="archive-label" data-qc-text="identificação">${escape(data.label)}</div>` : "")
      + (data.photoLabel ? `<div class="note-label" data-qc-text="origem da gravura">${escape(data.photoLabel)}</div>` : "");
  }
  if (m === "impact_slam") {
    const size=Math.min(24,100/Math.max(4,...data.impact.split("\n").map(s=>s.length))*1.68);
    art.innerHTML=svg('<rect width="720" height="1280" fill="#8d282b"/><g id="impact-swipe"><path d="M-35 424L740 293v631L-35 1043z" fill="#a33a2e"/><path d="M-30 462L740 326M-30 997L740 860" stroke="#df774e" stroke-width="9"/></g>')
      + `<div id="impact" style="--impact-size:${size}vmin"><h1 data-qc-text="impacto">${escape(data.impact)}</h1>${data.label ? `<div class="sub" data-qc-text="contexto">${escape(data.label)}</div>` : ""}</div>`;
  }
  if (m === "evidence_pan") art.innerHTML=paperLandscape("archive_desk",true)
    + printImage(data.assets.primary,"evidence-photo evidence-a stock","evidência A")
    + printImage(data.assets.secondary,"evidence-photo evidence-b stock","evidência B")
    + svg('<path id="ink" d="M293 450 C244 514 221 565 367 607 Q400 622 429 645" fill="none" stroke="#e85245" stroke-width="6" stroke-linecap="round" pathLength="1"/>');
  if (m === "newspaper_clipping") art.innerHTML=`<div class="proof"><figure>${printImage(data.assets.primary,"stock","foto do jornal")}</figure>
    <article><header data-qc-text="publicação">${escape(data.publication)}</header><h1 data-qc-text="manchete">${escape(data.clippingHeadline)}</h1>
      <p data-qc-text="texto do jornal">${escape(data.clippingText)}</p><span class="note" data-qc-text="destaque">${escape(data.highlight)}
       <svg viewBox="0 0 180 42" preserveAspectRatio="none"><path id="ink" d="${line}" fill="none" stroke="#b9322d" stroke-width="12" pathLength="1"/></svg></span></article></div>`;
  if (m === "photo_cutout") art.innerHTML=(data.photoBackdrop === "dark" ? paperLandscape("archive_desk",true) : svg('<rect width="720" height="1280" fill="#7d4a3e"/><path d="M-20 169l754 96-15 592-735-76z" fill="#b28a62"/>'))
    + `<figure class="hero stock">${printImage(data.assets.primary,"","recorte")}</figure>`
    + (data.photoLabel ? `<div class="note-label" data-qc-text="origem da foto">${escape(data.photoLabel)}</div>` : "");
  art.insertAdjacentHTML("beforeend",grain());
}

window.__renderAt = function(t) {
  const p=clamp(t/data.duration), q=ease(p);
  const ink=document.querySelector("#ink");
  if (ink) {
    ink.style.strokeDasharray="1";
    ink.style.strokeDashoffset=String(1-motion(t,Math.min(.81,data.duration*.32),data.highlightAt));
    ink.style.opacity=t>=data.highlightAt ? 1 : 0;
  }
  for (const [selector,depth] of [["#scenery-back",.22],["#scenery-mid",.61],["#scenery-fog",.74],["#scenery-front",1.21]]) {
    const layer=document.querySelector(selector);
    if (layer) layer.setAttribute("transform",`translate(${(q-.5)*61*depth} ${Math.sin(t*1.2)*5*depth})`);
  }
  if (data.module==="action_cutout_puppet") puppetAt(t,p,data);
  if (data.module==="full_bleed_macro") {
    const x=720*data.focus.x,y=1280*data.focus.y,scale=2.2+.24*q;
    const targetX=360+(x-360)*.14, targetY=589+(y-589)*.19;
    document.querySelector("#macro").setAttribute("transform",`translate(${targetX} ${targetY}) scale(${scale}) translate(${-x} ${-y})`);
  }
  if (data.module==="impact_slam") {
    const since=t-data.impactAt;
    const pulse=since<0 ? .93+.06*clamp(1+since/.13) : 1+.055*Math.exp(-since*10)*Math.cos(since*26);
    document.querySelector("#impact-swipe").setAttribute("transform",`translate(0 ${-44+115*q})`);
    document.querySelector("#impact").style.transform=`scale(${pulse}) rotate(${-3+1.2*q}deg)`;
  }
  if (data.module==="dark_archive" || data.module==="photo_cutout") {
    const hero=document.querySelector(".hero");
    const movement=data.module==="dark_archive" ? Math.sin(p*Math.PI*1.7) : q;
    hero.style.transform=`translateX(${(q-.5)*5.5}vmin) rotate(${-5+q*8}deg) scale(${.83+.13*q+.08*movement})`;
    const lamp=document.querySelector("#archive-lamp");
    if (lamp) lamp.setAttribute("transform",`translate(${-123+q*246} ${Math.sin(t*2)*89})`);
    if (data.lanternFocus) {
      const focus=motion(t,Math.min(.76,data.duration*.24),data.cueAt-.28);
      hero.style.setProperty("--lamp-x",`${27+q*32+focus*9}%`);
      hero.style.setProperty("--lamp-y",`${49+Math.sin(t*1.14)*11-focus*8}%`);
    }
    const label=document.querySelector(".archive-label");
    if (label) label.style.transform=`scale(${1+.033*motion(t,.26,data.cueAt)*(1-motion(t,.38,data.cueAt+.22))})`;
  }
  if (data.module==="evidence_pan") {
    document.querySelector(".evidence-a").style.transform=`translateX(${(2-q*5)}vmin) rotate(${-7+q}deg)`;
    document.querySelector(".evidence-b").style.transform=`translateX(${(3-q*5)}vmin) rotate(${6-q}deg)`;
  }
  if (data.module==="newspaper_clipping") {
    const figure=document.querySelector(".proof img");
    if (figure) figure.style.transform=`scale(${.81+.16*q}) rotate(${-4+q*6}deg) translateX(${(q-.5)*5}vmin)`;
  }
  const block=data.captions.find(b=>t>=b.start && t<b.end);
  const caption=document.querySelector("#cap");
  caption.innerHTML=block ? `<span class="surface" data-qc-caption-surface>${block.words.map(w=>`<span${t>=w.start && t<w.end ? ' class="active"' : ''}>${escape(w.text)}</span>`).join(" ")}</span>` : "";
};
window.renderAt=(t,p,model)=>{if(data!==model) initialize(model); return window.__renderAt(t);};
