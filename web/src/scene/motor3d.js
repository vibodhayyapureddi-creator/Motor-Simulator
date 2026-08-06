// The 3D motor test bench.
//
// One MotorScene owns the renderer/camera/bench; each simulated bench
// ("A", "B") gets its own MotorRig - a detailed procedural motor built
// from primitives with PBR materials, plus a drop-in slot: if
// /assets/motor.glb exists it replaces the procedural model (its node
// named *shaft*/*rotor* is spun; meshes named housing/copper/shaft keep
// the glow hooks). Dual view shows both rigs side by side.
//
// Visual state encodings: housing glows with winding temperature, copper
// pulses with current, the attached load (propeller / wheel / pump /
// flywheel / brake) is coupled to the shaft, the BLDC commutation sector
// lights up around the stator, stall turns the shaft red, and overcurrent
// throws sparks.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { getPalette, onThemeChange } from "../theme.js";

const STEEL = { color: 0x9aa5b1, metalness: 0.85, roughness: 0.38 };
const DARK_STEEL = { color: 0x4a545f, metalness: 0.8, roughness: 0.5 };
const COPPER = { color: 0xb87333, metalness: 0.9, roughness: 0.35 };
const RUBBER = { color: 0x1a1d21, metalness: 0.0, roughness: 0.95 };

function mat(props) { return new THREE.MeshStandardMaterial(props); }

// ---------------------------------------------------------------- MotorRig

class MotorRig {
  constructor(parent, label) {
    this.group = new THREE.Group();
    this.group.position.y = 0.62;
    parent.add(this.group);

    this._buildProceduralMotor();
    this._buildSectorRing();
    this._buildSparks();
    this._buildLabel(label);

    this.loadKind = "none";
    this.loadGroup = null;
    this.staticLoadGroup = null;

    this.frame = null;         // latest telemetry for this bench
    this.spinAngle = 0;
    this._sparkTimer = 0;
  }

  // ------------------------------------------------------------ procedural

  _buildProceduralMotor() {
    const g = new THREE.Group();
    this.motorGroup = g;
    this.group.add(g);

    const add = (mesh, parent = g) => {
      mesh.castShadow = true; mesh.receiveShadow = true;
      parent.add(mesh); return mesh;
    };
    const cylX = (r, len, material, x = 0, segs = 40) => {
      const geo = new THREE.CylinderGeometry(r, r, len, segs);
      geo.rotateZ(Math.PI / 2);
      const m = new THREE.Mesh(geo, material);
      m.position.x = x;
      return m;
    };

    // housing + cooling fins (their material glows with temperature)
    this.housingMat = mat({ ...STEEL, emissive: 0xff3300, emissiveIntensity: 0 });
    add(cylX(0.44, 1.16, this.housingMat));
    const finGeo = new THREE.BoxGeometry(1.1, 0.16, 0.024);
    for (let i = 0; i < 14; i++) {
      const a = (i / 14) * Math.PI * 2;
      const fin = new THREE.Mesh(finGeo, this.housingMat);
      const r = 0.485;
      fin.position.set(0, Math.sin(a) * r, Math.cos(a) * r);
      fin.rotation.x = -a;
      add(fin);
    }

    // end bells + bearing boss + feet + terminal box
    const bellMat = mat(DARK_STEEL);
    add(cylX(0.465, 0.13, bellMat, -0.645));
    add(cylX(0.465, 0.13, bellMat, 0.645));
    add(cylX(0.13, 0.1, bellMat, 0.75));
    const footGeo = new THREE.BoxGeometry(0.34, 0.14, 0.7);
    for (const x of [-0.38, 0.38]) {
      const foot = new THREE.Mesh(footGeo, bellMat);
      foot.position.set(x, -0.55, 0);
      add(foot);
    }
    const tbox = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.2, 0.3), bellMat);
    tbox.position.set(-0.18, 0.53, 0);
    add(tbox);
    this.terminalBox = tbox;
    const cableMat = mat(RUBBER);
    for (const dz of [-0.06, 0.06]) {
      const cable = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 0.5, 10), cableMat);
      cable.position.set(-0.18, 0.78, dz);
      add(cable);
    }

    // copper winding rings, pulsing with current
    this.copperMat = mat({ ...COPPER, emissive: 0xff7722, emissiveIntensity: 0 });
    for (const x of [-0.585, 0.585]) {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.33, 0.05, 12, 40), this.copperMat);
      ring.rotation.y = Math.PI / 2;
      ring.position.x = x;
      add(ring);
    }

    // spinning shaft assembly
    const shaft = new THREE.Group();
    this.shaftGroup = shaft;
    g.add(shaft);
    this.shaftMat = mat({ color: 0xc9d2dc, metalness: 0.95, roughness: 0.25,
                          emissive: 0xff2200, emissiveIntensity: 0 });
    const shaftMesh = cylX(0.048, 0.75, this.shaftMat, 1.06, 24);
    add(shaftMesh, shaft);
    // key flat so rotation is visible even bare
    const keyMesh = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.02, 0.03), this.shaftMat);
    keyMesh.position.set(1.28, 0.052, 0);
    add(keyMesh, shaft);
    const hub = cylX(0.1, 0.09, mat(DARK_STEEL), 0.92, 24);
    add(hub, shaft);
  }

  _buildSectorRing() {
    // six commutation-sector arcs around the stator (BLDC insight view)
    this.sectorGroup = new THREE.Group();
    this.sectorGroup.visible = false;
    this.group.add(this.sectorGroup);
    this.sectorMats = [];
    for (let i = 0; i < 6; i++) {
      const m = mat({ color: 0xd6dae0, metalness: 0.2, roughness: 0.6,
                      emissive: 0x2563eb, emissiveIntensity: 0 });
      const arc = new THREE.Mesh(
        new THREE.TorusGeometry(0.62, 0.028, 8, 16, Math.PI / 3 - 0.09), m);
      arc.rotation.y = Math.PI / 2;               // torus plane -> YZ (axis X)
      arc.rotation.x = i * (Math.PI / 3) + 0.045;  // rotate about motor axis
      this.sectorGroup.add(arc);
      this.sectorMats.push(m);
    }
    // electrical-angle marker
    this.angleMarker = new THREE.Mesh(
      new THREE.SphereGeometry(0.045, 12, 12),
      mat({ color: 0x2563eb, emissive: 0x2563eb, emissiveIntensity: 0.7 }));
    this.sectorGroup.add(this.angleMarker);
  }

  _buildSparks() {
    const N = 90;
    this.sparkN = N;
    this.sparkPos = new Float32Array(N * 3);
    this.sparkVel = new Float32Array(N * 3);
    this.sparkLife = new Float32Array(N);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(this.sparkPos, 3));
    this.sparkMat = new THREE.PointsMaterial({
      color: 0xe8720c, size: 0.035, transparent: true, opacity: 0.95,
      depthWrite: false,
    });
    this.sparks = new THREE.Points(geo, this.sparkMat);
    this.sparks.visible = false;
    this.group.add(this.sparks);
  }

  _buildLabel(text) {
    // bench tag floating above the motor; shown only in dual view
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const g = c.getContext("2d");
    g.fillStyle = "#1f2430";
    g.beginPath(); g.arc(32, 32, 30, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#ffffff";
    g.font = "600 34px Segoe UI";
    g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(text, 32, 35);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: new THREE.CanvasTexture(c), transparent: true }));
    sprite.scale.set(0.3, 0.3, 1);
    sprite.position.set(0, 1.15, 0);
    sprite.visible = false;
    this.group.add(sprite);
    this.labelSprite = sprite;
  }

  // -------------------------------------------------------------- glb slot

  useGlb(glbScene) {
    let spinNode = null;
    glbScene.traverse(n => {
      if (!spinNode && /shaft|rotor/i.test(n.name)) spinNode = n;
      if (n.isMesh) { n.castShadow = n.receiveShadow = true; }
    });
    if (!spinNode) {
      console.warn("motor.glb has no node named *shaft*/*rotor*; keeping procedural model");
      return false;
    }
    this.motorGroup.visible = false;
    this.group.add(glbScene);

    // rehome any attached load onto the new shaft, then swap the spin node
    if (this.loadGroup) {
      this.shaftGroup.remove(this.loadGroup);
      spinNode.add(this.loadGroup);
    }
    this.shaftGroup = spinNode;

    // hook the glow encodings to the asset's named meshes when present
    glbScene.traverse(n => {
      if (!n.isMesh || !n.material) return;
      if (/housing|fin/i.test(n.name)) {
        n.material.emissive = new THREE.Color(0xff3300);
        this.housingMat = n.material;
      } else if (/copper|winding/i.test(n.name)) {
        n.material.emissive = new THREE.Color(0xff7722);
        this.copperMat = n.material;
      } else if (/shaft|rotor/i.test(n.name)) {
        n.material.emissive = new THREE.Color(0xff2200);
        this.shaftMat = n.material;
      }
    });
    return true;
  }

  // ----------------------------------------------------------------- loads

  setLoad(kind) {
    if (kind === this.loadKind) return;
    this.loadKind = kind;
    if (this.loadGroup) { this.shaftGroup.remove(this.loadGroup); this.loadGroup = null; }
    if (this.staticLoadGroup) { this.group.remove(this.staticLoadGroup); this.staticLoadGroup = null; }

    const spin = new THREE.Group();
    spin.position.x = 1.32;                    // coupling point on the shaft
    const fix = new THREE.Group();
    const addTo = (mesh, group) => {
      mesh.castShadow = true; mesh.receiveShadow = true; group.add(mesh); return mesh;
    };
    const diskX = (r, th, material, x = 0) => {
      const geo = new THREE.CylinderGeometry(r, r, th, 40);
      geo.rotateZ(Math.PI / 2);
      const m = new THREE.Mesh(geo, material);
      m.position.x = x;
      return m;
    };

    if (kind === "fan") {                       // propeller
      addTo(diskX(0.09, 0.09, mat(DARK_STEEL)), spin);
      const bladeMat = mat({ color: 0x2f3d4f, metalness: 0.3, roughness: 0.5 });
      for (let i = 0; i < 3; i++) {
        const blade = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.72, 0.13), bladeMat);
        blade.position.x = 0.01;
        blade.rotation.x = (i / 3) * Math.PI * 2;
        blade.rotation.y = 0.5;                 // blade pitch
        blade.translateY(0.36);
        addTo(blade, spin);
      }
    } else if (kind === "wheel") {
      const tire = new THREE.Mesh(new THREE.TorusGeometry(0.5, 0.13, 16, 40), mat(RUBBER));
      tire.rotation.y = Math.PI / 2;
      addTo(tire, spin);
      addTo(diskX(0.4, 0.06, mat(STEEL)), spin);
      const spokeGeo = new THREE.BoxGeometry(0.03, 0.76, 0.05);
      for (let i = 0; i < 5; i++) {
        const spoke = new THREE.Mesh(spokeGeo, mat(DARK_STEEL));
        spoke.rotation.x = (i / 5) * Math.PI * 2;
        addTo(spoke, spin);
      }
    } else if (kind === "pump") {
      // spinning impeller...
      addTo(diskX(0.22, 0.1, mat({ ...COPPER })), spin);
      const vane = new THREE.BoxGeometry(0.12, 0.4, 0.03);
      for (let i = 0; i < 4; i++) {
        const v = new THREE.Mesh(vane, mat(COPPER));
        v.rotation.x = (i / 4) * Math.PI * 2;
        v.position.x = 0.07;
        addTo(v, spin);
      }
      // ...inside a static volute casing with an outlet pipe
      const casing = new THREE.Mesh(new THREE.TorusGeometry(0.3, 0.14, 16, 40), mat(STEEL));
      casing.rotation.y = Math.PI / 2;
      casing.position.set(1.32, 0, 0);
      addTo(casing, fix);
      const pipe = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.6, 16), mat(STEEL));
      pipe.position.set(1.32, 0.43, 0);
      addTo(pipe, fix);
    } else if (kind === "flywheel") {
      addTo(diskX(0.58, 0.12, mat({ color: 0x39424e, metalness: 0.9, roughness: 0.3 })), spin);
      addTo(diskX(0.6, 0.05, mat(DARK_STEEL), 0.0), spin);   // rim band
      addTo(diskX(0.1, 0.16, mat(STEEL)), spin);
      // balance-hole ring so the spin reads visually
      for (let i = 0; i < 6; i++) {
        const holeMark = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.13, 12),
          mat({ color: 0x11151a, metalness: 0.4, roughness: 0.8 }));
        holeMark.geometry.rotateZ(Math.PI / 2);
        const a = (i / 6) * Math.PI * 2;
        holeMark.position.set(0.001, Math.sin(a) * 0.36, Math.cos(a) * 0.36);
        addTo(holeMark, spin);
      }
    } else if (kind === "constant" || kind === "viscous") {
      // brake drum with a caliper shoe on a bracket off the end bell
      addTo(diskX(0.3, 0.1, mat({ color: 0x5b6672, metalness: 0.85, roughness: 0.35 })), spin);
      for (let i = 0; i < 4; i++) {
        const slot = new THREE.Mesh(new THREE.BoxGeometry(0.11, 0.08, 0.02), mat(DARK_STEEL));
        const a = (i / 4) * Math.PI * 2;
        slot.position.set(0, Math.sin(a) * 0.2, Math.cos(a) * 0.2);
        slot.rotation.x = -a;
        addTo(slot, spin);
      }
      const shoe = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.12, 0.24), mat({ color: 0x8a2c2c, metalness: 0.4, roughness: 0.6 }));
      shoe.position.set(1.32, 0.36, 0);
      addTo(shoe, fix);
      const bracketMat = mat(DARK_STEEL);
      const post = new THREE.Mesh(new THREE.BoxGeometry(0.035, 0.51, 0.035), bracketMat);
      post.position.set(0.645, 0.105, 0);
      addTo(post, fix);
      const arm = new THREE.Mesh(new THREE.BoxGeometry(0.675, 0.035, 0.035), bracketMat);
      arm.position.set(0.9825, 0.36, 0);
      addTo(arm, fix);
    }

    if (spin.children.length) { this.shaftGroup.add(spin); this.loadGroup = spin; }
    if (fix.children.length) { this.group.add(fix); this.staticLoadGroup = fix; }
  }

  // ------------------------------------------------------------- per-frame

  update(frame) { this.frame = frame; }

  tick(dt) {
    const f = this.frame;
    if (f) {
      const ctl = f.ctl;
      const mtype = ctl.motor_type;
      this.setLoad(ctl.load.kind);
      this.sectorGroup.visible = mtype === "bldc" || mtype === "induction";

      if (mtype === "stepper") {
        // steppers show their TRUE mechanical angle (elec_angle carries it)
        // so single steps and detent oscillation are visible
        this.shaftGroup.rotation.x = -(f.elec_angle * Math.PI) / 180;
      } else {
        // shaft spin: compress thousands of RPM into a legible rate (plan §7),
        // slow-motion scales it down toward true speed
        const scaledRpm = f.rpm * ctl.time_scale * (ctl.paused ? 0 : 1);
        const revPerSec = Math.sign(scaledRpm) * 3.2 * (1 - Math.exp(-Math.abs(scaledRpm) / 1600));
        this.spinAngle -= revPerSec * Math.PI * 2 * dt;   // -x: CCW seen from shaft end
        this.shaftGroup.rotation.x = this.spinAngle;
      }

      // two-zone temperature glow: the housing shows the housing node,
      // the copper windings show the (hotter) winding node
      const span = Math.max(20, ctl.overheat_c - ctl.ambient_c);
      const heatH = Math.min(1, Math.max(0,
        ((f.housing_temp ?? f.temperature) - ctl.ambient_c) / span));
      this.housingMat.emissiveIntensity = heatH * heatH * 0.35;
      const heatW = Math.min(1, Math.max(0,
        (f.temperature - ctl.ambient_c) / span));

      // copper: current pulse, plus a steady red heat component
      const iNorm = Math.min(1, Math.abs(f.current) / Math.max(0.5, ctl.limit_a || 10));
      const pulse = iNorm * (0.5 + 0.15 * Math.sin(performance.now() / 45));
      this.copperMat.emissive.setHex(heatW * 0.5 > pulse ? 0xff2200 : 0xff7722);
      this.copperMat.emissiveIntensity = Math.max(pulse, heatW * heatW * 0.5);

      // stall: shaft glows red; regen braking: soft green
      if (f.flags.stall) {
        this.shaftMat.emissive.setHex(0xff2200);
        this.shaftMat.emissiveIntensity = 0.55 + 0.25 * Math.sin(performance.now() / 90);
      } else if (f.flags.regen) {
        this.shaftMat.emissive.setHex(0x18b755);
        this.shaftMat.emissiveIntensity = 0.4;
      } else {
        this.shaftMat.emissiveIntensity = 0;
      }

      // commutation / field view: discrete sectors for six-step BLDC, a
      // continuously rotating highlight for FOC and the induction field
      if (this.sectorGroup.visible) {
        const continuous = f.sector < 0;   // foc or induction
        for (let i = 0; i < 6; i++) {
          if (continuous) {
            const center = i * 60 + 30;
            let d = ((f.elec_angle - center) % 360 + 540) % 360 - 180;
            this.sectorMats[i].emissiveIntensity =
              0.04 + 0.9 * Math.pow(Math.max(0, Math.cos((d * Math.PI) / 180)), 6);
          } else {
            this.sectorMats[i].emissiveIntensity = i === f.sector ? 0.95 : 0.04;
          }
        }
        const a = (f.elec_angle * Math.PI) / 180;
        this.angleMarker.position.set(0, Math.sin(a) * 0.62, Math.cos(a) * 0.62);
      }

      // sparks while overcurrent
      this._sparkTimer -= dt;
      if (f.flags.overcurrent && this._sparkTimer <= 0) {
        this._burstSparks();
        this._sparkTimer = 0.14;
      }
    }
    this._updateSparks(dt);
  }

  _burstSparks() {
    const origin = new THREE.Vector3();
    this.terminalBox.getWorldPosition(origin);
    this.group.worldToLocal(origin);
    for (let i = 0; i < this.sparkN; i++) {
      if (this.sparkLife[i] > 0) continue;
      if (Math.random() > 0.5) continue;
      const j = i * 3;
      this.sparkPos[j] = origin.x; this.sparkPos[j + 1] = origin.y + 0.1; this.sparkPos[j + 2] = origin.z;
      this.sparkVel[j] = (Math.random() - 0.5) * 1.6;
      this.sparkVel[j + 1] = 0.8 + Math.random() * 1.6;
      this.sparkVel[j + 2] = (Math.random() - 0.5) * 1.6;
      this.sparkLife[i] = 0.5 + Math.random() * 0.5;
    }
  }

  _updateSparks(dt) {
    let alive = 0;
    for (let i = 0; i < this.sparkN; i++) {
      if (this.sparkLife[i] <= 0) continue;
      this.sparkLife[i] -= dt;
      const j = i * 3;
      if (this.sparkLife[i] <= 0) {
        this.sparkPos[j + 1] = -999;   // park dead particles out of sight
        continue;
      }
      alive++;
      this.sparkVel[j + 1] -= 4.5 * dt;
      this.sparkPos[j] += this.sparkVel[j] * dt;
      this.sparkPos[j + 1] += this.sparkVel[j + 1] * dt;
      this.sparkPos[j + 2] += this.sparkVel[j + 2] * dt;
    }
    this.sparks.visible = alive > 0;
    if (alive) this.sparks.geometry.attributes.position.needsUpdate = true;
  }
}

// --------------------------------------------------------------- the scene

export class MotorScene {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(getPalette().sceneBg);
    this.scene.fog = new THREE.Fog(getPalette().sceneBg, 9, 20);

    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.55;

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.05, 60);
    this.camera.position.set(2.9, 1.7, 3.4);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.target.set(0.25, 0.62, 0);
    this.controls.enableDamping = true;
    this.controls.maxDistance = 12;
    this.controls.minDistance = 0.8;

    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(3, 5, 2.5);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.camera.left = key.shadow.camera.bottom = -3;
    key.shadow.camera.right = key.shadow.camera.top = 3;
    this.scene.add(key);
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.55));

    // bench
    const ground = new THREE.Mesh(
      new THREE.CylinderGeometry(6, 6, 0.08, 48),
      mat({ color: getPalette().ground, metalness: 0.05, roughness: 0.95 }));
    ground.position.y = -0.04;
    ground.receiveShadow = true;
    this.scene.add(ground);
    this._ground = ground;
    this._grid = null;
    this._makeGrid();
    onThemeChange(pal => {
      this.scene.background.setHex(pal.sceneBg);
      this.scene.fog.color.setHex(pal.sceneBg);
      this._ground.material.color.setHex(pal.ground);
      this._makeGrid();
    });

    this.rigs = {
      A: new MotorRig(this.scene, "A"),
      B: new MotorRig(this.scene, "B"),
    };
    // (grid built via _makeGrid so a theme change can swap its colors)
    this.rigs.B.group.visible = false;
    this.dual = false;

    this._tryLoadGlb();
    this._buildGizmo();
    this._camTween = null;

    // affordance: the scene is draggable - say so once, then get out of
    // the way the moment the user actually orbits
    this._hint = document.createElement("div");
    this._hint.className = "orbit-hint";
    this._hint.textContent = "drag to orbit · scroll to zoom · click the cube for front / side / top";
    canvas.parentElement.appendChild(this._hint);
    this.controls.addEventListener("start", () => {
      this._hint.classList.add("hidden");
    });

    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(canvas.parentElement);
    this._resize();
  }

  // ------------------------------------------------------------- view cube

  _buildGizmo() {
    const maxAniso = this.renderer.capabilities.getMaxAnisotropy();
    const face = (label) => {
      // high-res + anisotropic so the tilted faces stay crisp at 100 px
      const c = document.createElement("canvas");
      c.width = c.height = 256;
      const g = c.getContext("2d");
      g.fillStyle = "#f4f5f7";
      g.fillRect(0, 0, 256, 256);
      g.strokeStyle = "#c9ced6";
      g.lineWidth = 10;
      g.strokeRect(5, 5, 246, 246);
      g.fillStyle = "#3b4252";
      g.font = "600 46px Segoe UI";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(label, 128, 132);
      const tex = new THREE.CanvasTexture(c);
      tex.anisotropy = maxAniso;
      tex.colorSpace = THREE.SRGBColorSpace;
      return new THREE.MeshBasicMaterial({ map: tex });
    };
    this._gizmoSize = 100;                // CSS px, bottom-right corner
    this._gizmoScene = new THREE.Scene();
    this._gizmoCam = new THREE.PerspectiveCamera(35, 1, 0.1, 10);
    this._gizmoCam.position.set(0, 0, 3.9);
    // the cube plus clickable corner/edge pieces, ViewCube-style: faces
    // snap to axis views, edges to 45-degree views, corners to isometric
    this._gizmoRoot = new THREE.Group();
    this._gizmoScene.add(this._gizmoRoot);
    // BoxGeometry material order: +x -x +y -y +z -z
    this._gizmoCube = new THREE.Mesh(
      new THREE.BoxGeometry(1.42, 1.42, 1.42),
      [face("FRONT"), face("BACK"), face("TOP"),
       face("BOT"), face("SIDE"), face("SIDE")]);
    this._gizmoRoot.add(this._gizmoCube);
    const outline = new THREE.LineSegments(
      new THREE.EdgesGeometry(this._gizmoCube.geometry),
      new THREE.LineBasicMaterial({ color: 0x8a919d }));
    this._gizmoCube.add(outline);

    // corner/edge pieces tile the cube's surface FLUSH (Rubik-style
    // segmentation): outer faces sit a hair above the cube skin so
    // nothing sticks out but there's no z-fighting either. The thin
    // outlines make each clickable tile readable.
    this._gizmoPieces = [];               // hover/click targets with .dir
    const pieceMat = () => new THREE.MeshBasicMaterial({ color: 0xe6e9ed });
    const h = 0.71;                       // cube half-size
    const cs = 0.30;                      // corner/edge tile size
    const eps = 0.006;                    // lift above the skin (no z-fight)
    const cc = h - cs / 2 + eps;          // flush center offset
    const addPiece = (geo, x, y, z, dir) => {
      const m = new THREE.Mesh(geo, pieceMat());
      m.position.set(x, y, z);
      m.userData.dir = dir.normalize();
      const line = new THREE.LineSegments(
        new THREE.EdgesGeometry(geo),
        new THREE.LineBasicMaterial({ color: 0xb3bac4 }));
      m.add(line);
      this._gizmoRoot.add(m);
      this._gizmoPieces.push(m);
    };
    // 8 corners -> isometric views
    const cornerGeo = new THREE.BoxGeometry(cs, cs, cs);
    for (const sx of [1, -1]) for (const sy of [1, -1]) for (const sz of [1, -1])
      addPiece(cornerGeo, sx * cc, sy * cc, sz * cc,
               new THREE.Vector3(sx, sy, sz));
    // 12 edges -> 45-degree views (bars spanning between the corners)
    const span = 2 * (h - cs);
    const edgeX = new THREE.BoxGeometry(span, cs, cs);
    const edgeY = new THREE.BoxGeometry(cs, span, cs);
    const edgeZ = new THREE.BoxGeometry(cs, cs, span);
    for (const s1 of [1, -1]) for (const s2 of [1, -1]) {
      addPiece(edgeX, 0, s1 * cc, s2 * cc, new THREE.Vector3(0, s1, s2));
      addPiece(edgeY, s1 * cc, 0, s2 * cc, new THREE.Vector3(s1, 0, s2));
      addPiece(edgeZ, s1 * cc, s2 * cc, 0, new THREE.Vector3(s1, s2, 0));
    }
    this._gizmoHover = null;

    // intercept pointer events over the cube BEFORE OrbitControls sees
    // them (capture phase on the parent beats target-phase listeners)
    const parent = this.canvas.parentElement;
    this._gizmoArmed = false;
    parent.addEventListener("pointerdown", (ev) => {
      if (this._inGizmo(ev)) { ev.stopPropagation(); this._gizmoArmed = true; }
    }, true);
    parent.addEventListener("pointerup", (ev) => {
      if (this._gizmoArmed && this._inGizmo(ev)) {
        ev.stopPropagation();
        this._gizmoClick(ev);
      }
      this._gizmoArmed = false;
    }, true);
    parent.addEventListener("pointermove", (ev) => {
      const inside = this._inGizmo(ev);
      this.canvas.style.cursor = inside ? "pointer" : "";
      this._gizmoHoverUpdate(inside ? ev : null);
    }, true);
    this._raycaster = new THREE.Raycaster();
  }

  _gizmoPick(ev) {
    const { rect, size, x0, y0 } = this._gizmoRect();
    const nx = ((ev.clientX - rect.left - x0) / size) * 2 - 1;
    const ny = -(((ev.clientY - rect.top - y0) / size) * 2 - 1);
    this._raycaster.setFromCamera(new THREE.Vector2(nx, ny), this._gizmoCam);
    return this._raycaster.intersectObjects(
      [this._gizmoCube, ...this._gizmoPieces], false)[0] || null;
  }

  _gizmoHoverUpdate(ev) {
    const hit = ev ? this._gizmoPick(ev) : null;
    const target = hit && hit.object !== this._gizmoCube ? hit.object : null;
    if (this._gizmoHover === target) return;
    if (this._gizmoHover) this._gizmoHover.material.color.setHex(0xe6e9ed);
    if (target) target.material.color.setHex(0x7db4f5);
    this._gizmoHover = target;
  }

  _gizmoRect() {
    const rect = this.canvas.getBoundingClientRect();
    const size = this._gizmoSize, pad = 10;
    return { rect, size,
             x0: rect.width - size - pad, y0: rect.height - size - pad };
  }

  _inGizmo(ev) {
    const { rect, size, x0, y0 } = this._gizmoRect();
    const px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    return px >= x0 && px <= x0 + size && py >= y0 && py <= y0 + size;
  }

  _gizmoClick(ev) {
    const hit = this._gizmoPick(ev);
    if (!hit) return;
    // the gizmo is world-aligned (it carries the inverse camera rotation),
    // so local directions ARE world directions to look from
    let dir;
    if (hit.object !== this._gizmoCube) {
      dir = hit.object.userData.dir.clone();      // corner or edge piece
    } else if (hit.face) {
      const n = hit.face.normal;
      dir = new THREE.Vector3(Math.round(n.x), Math.round(n.y), Math.round(n.z));
    } else {
      return;
    }
    if (Math.abs(dir.y) > 0.99 * dir.length())
      dir.x = 0.18 * dir.length();   // avoid gimbal-degenerate top/bottom
    dir.normalize();
    const dist = this.camera.position.distanceTo(this.controls.target);
    const to = this.controls.target.clone().add(dir.multiplyScalar(dist));
    this._camTween = { from: this.camera.position.clone(), to, t: 0 };
  }

  _makeGrid() {
    if (this._grid) this.scene.remove(this._grid);
    const pal = getPalette();
    this._grid = new THREE.GridHelper(12, 40, pal.grid1, pal.grid2);
    this._grid.position.y = 0.005;
    this.scene.add(this._grid);
  }

  setDual(on) {
    this.dual = on;
    this.rigs.A.group.position.z = on ? -1.0 : 0;
    this.rigs.B.group.position.z = on ? 1.0 : 0;
    this.rigs.B.group.visible = on;
    this.rigs.A.labelSprite.visible = on;
    this.rigs.B.labelSprite.visible = on;
  }

  // -------------------------------------------------------------- glb slot

  _tryLoadGlb() {
    fetch("/assets/motor.glb", { method: "HEAD" }).then(res => {
      if (!res.ok) return;
      new GLTFLoader().load("/assets/motor.glb", gltf => {
        // clone BEFORE rig A takes ownership of the original
        const copy = gltf.scene.clone(true);
        copy.traverse(n => {                    // clone() shares materials
          if (n.isMesh) n.material = n.material.clone();
        });
        const ok = this.rigs.A.useGlb(gltf.scene);
        if (ok) {
          this.rigs.B.useGlb(copy);
          console.info("Loaded motor model from /assets/motor.glb");
        }
      }, undefined, () => { /* keep procedural model */ });
    }).catch(() => { /* offline-safe: procedural model stays */ });
  }

  // ------------------------------------------------------------- per-frame

  update(frame) {
    const rig = this.rigs[frame.bench || "A"];
    if (rig) rig.update(frame);
  }

  tick(dt) {
    this.rigs.A.tick(dt);
    if (this.dual) this.rigs.B.tick(dt);

    // smooth camera snap after a view-cube click
    if (this._camTween) {
      const tw = this._camTween;
      tw.t = Math.min(1, tw.t + dt / 0.45);
      const e = 1 - Math.pow(1 - tw.t, 3);          // ease-out cubic
      this.camera.position.lerpVectors(tw.from, tw.to, e);
      if (tw.t >= 1) this._camTween = null;
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);

    // view-cube inset (bottom-right): world-aligned, so it turns as you
    // orbit - the visual cue that the whole scene is draggable
    const el = this.canvas.parentElement;
    const w = el.clientWidth, h = el.clientHeight;
    const size = this._gizmoSize, pad = 10;
    this._gizmoRoot.quaternion.copy(this.camera.quaternion).invert();
    this.renderer.autoClear = false;
    this.renderer.clearDepth();
    this.renderer.setScissorTest(true);
    this.renderer.setViewport(w - size - pad, pad, size, size);
    this.renderer.setScissor(w - size - pad, pad, size, size);
    this.renderer.render(this._gizmoScene, this._gizmoCam);
    this.renderer.setScissorTest(false);
    this.renderer.setViewport(0, 0, w, h);
    this.renderer.autoClear = true;
  }

  snapshot() {
    // synchronous render + capture (the drawing buffer is only reliable
    // immediately after a render call)
    this.renderer.render(this.scene, this.camera);
    return this.renderer.domElement.toDataURL("image/png");
  }

  _resize() {
    const el = this.canvas.parentElement;
    const w = el.clientWidth, h = el.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }
}
