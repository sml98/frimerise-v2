import * as THREE from "./vendor/three-r184/three.module.min.js";

// The explicit seek/render pattern follows HyperFrames' Apache-2.0
// registry/components/three-orbiting-cards at revision 34c7e64.
const canvas = document.querySelector("#plate");
const stage = document.querySelector("#stage");
const texture = document.querySelector("#texture");
const caption = document.querySelector("#caption");
const fov = 38;
const rayHalfHeight = Math.tan(fov * Math.PI / 360);
let composition;

function geometryForDepth(depth, width, height, aspect) {
  const positions = [];
  const uvs = [];
  const triangles = [];
  for (let row = 0; row < height; row++) {
    const y = row / (height - 1);
    for (let column = 0; column < width; column++) {
      const x = column / (width - 1);
      const distance = 5.5 - 1.6 * depth[row * width + column] / 65535;
      const ray = distance * rayHalfHeight;
      positions.push((x * 2 - 1) * ray * aspect, (1 - y * 2) * ray, -distance);
      uvs.push(x, 1 - y);
    }
  }
  function face(a, b, c) {
    if (Math.max(depth[a], depth[b], depth[c]) - Math.min(depth[a], depth[b], depth[c]) < 9100) {
      triangles.push(a, b, c);
    }
  }
  for (let row = 0; row < height - 1; row++) {
    for (let column = 0; column < width - 1; column++) {
      const left = row * width + column;
      face(left, left + width, left + 1);
      face(left + width, left + width + 1, left + 1);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(triangles);
  return geometry;
}

async function initialize(payload) {
  const loader = new THREE.TextureLoader();
  const [art, back, response] = await Promise.all([
    loader.loadAsync("/_media/artwork.png"),
    loader.loadAsync("/_media/bg_inpainted.png"),
    fetch("/_media/depth-grid.bin"),
  ]);
  if (!response.ok) throw new Error("Mapa de profundidade indisponível.");
  art.colorSpace = THREE.SRGBColorSpace;
  back.colorSpace = THREE.SRGBColorSpace;
  const [gridWidth, gridHeight] = payload.gridSize;
  const raw = new DataView(await response.arrayBuffer());
  if (raw.byteLength !== gridWidth * gridHeight * 2) throw new Error("Mapa de profundidade truncado.");
  const depth = Array.from({length: gridWidth * gridHeight}, (_, index) => raw.getUint16(index * 2, true));
  const aspect = art.image.width / art.image.height;
  const portrait = innerHeight > innerWidth;
  const screenWidth = Math.round(Math.min(innerWidth * (portrait ? .86 : .72), innerHeight * .44 * aspect));
  const screenHeight = Math.round(screenWidth / aspect);
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true, preserveDrawingBuffer: true});
  renderer.setPixelRatio(1);
  renderer.setSize(screenWidth, screenHeight);
  renderer.setClearColor(0x1b1615);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(fov, aspect, .1, 20);
  const backgroundSize = 2 * 6.2 * rayHalfHeight;
  const background = new THREE.Mesh(new THREE.PlaneGeometry(backgroundSize * aspect, backgroundSize),
                                   new THREE.MeshBasicMaterial({map: back}));
  background.position.z = -6.2;
  scene.add(background);
  const relief = new THREE.Mesh(geometryForDepth(depth, gridWidth, gridHeight, aspect),
                               new THREE.MeshBasicMaterial({map: art}));
  scene.add(relief);
  document.querySelector("#title").textContent = payload.headline;
  document.querySelector("#credit").textContent = payload.credit;
  return {renderer, scene, camera};
}

function drawCaption(seconds, blocks) {
  const block = blocks.find(({start, end}) => seconds >= start && seconds < end);
  caption.replaceChildren();
  if (!block) return;
  const surface = document.createElement("span");
  surface.className = "surface";
  surface.dataset.qcCaptionSurface = "";
  for (const [index, word] of block.words.entries()) {
    if (index) surface.append(" ");
    const active = seconds >= word.start && seconds < word.end;
    const token = document.createElement(active ? "strong" : "span");
    token.textContent = word.text;
    surface.append(token);
  }
  caption.append(surface);
}

window.__renderAt = async (seconds, payload) => {
  const {renderer, scene, camera} = await (composition ??= initialize(payload));
  const p = Math.max(0, Math.min(1, seconds / payload.duration));
  const eased = p * p * (3 - 2 * p);
  camera.position.set(.49 * (eased - .46), .073 * Math.sin(p * Math.PI), -.065 - .49 * eased);
  camera.lookAt(.048 - .12 * eased, -.075 * eased, -5);
  renderer.render(scene, camera);
  renderer.getContext().finish();
  stage.style.setProperty("--light", `${38 + 24 * eased}%`);
  const frame = Math.round(seconds * payload.fps);
  texture.style.backgroundPosition = `${(frame * 37) % 156}px ${(frame * 67) % 156}px`;
  drawCaption(seconds, payload.caption);
};
