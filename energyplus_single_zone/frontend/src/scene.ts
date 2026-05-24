/**
 * Three.js zone visualization — a simple 3D building cube with one
 * configurable window.
 *
 * The scene is intentionally minimal:
 *
 *   - One cube representing the building zone
 *   - One window face whose area scales with the ``window_area``
 *     parameter
 *   - The window's transparency reflects the U-value (lower U =
 *     better insulation = more opaque tint)
 *   - The wall color shifts with the climate zone (warmer for 4A,
 *     cooler for 6A) as a qualitative cue
 *   - An ambient + directional light pair so the cube reads as 3D
 *   - Auto-rotating camera so the learner sees the window
 *
 * The 3D viz is a pedagogical aid, not a substitute for the
 * numeric results. The cards in the form panel are the actual
 * answer the learner needs.
 *
 * **Graceful degradation:** if WebGL isn't available (test envs,
 * old browsers), :func:`createScene` returns ``null`` and the
 * caller renders the form panel without the 3D viz. The contract
 * with the form panel is purely "I can also update myself when
 * parameters change" — if I'm absent, the form still works.
 */

import * as THREE from "three";

import { CLIMATE_DATA } from "./constants";

/**
 * Handle returned by :func:`createScene`. The form panel pushes
 * parameter changes via :meth:`update`; the scene also owns its
 * animation loop until :meth:`dispose` is called.
 */
export interface SceneHandle {
  /** Push the latest parameter values into the scene. */
  update(parameters: {
    glazing_u_value: number;
    window_area: number;
    climate_zone: string;
  }): void;

  /** Tear down the animation loop + Three.js resources. */
  dispose(): void;
}

const SCENE_WIDTH_PX = 360;
const SCENE_HEIGHT_PX = 280;

/**
 * Build the Three.js scene inside ``container``.
 *
 * Returns ``null`` if WebGL is unavailable (the caller should
 * render the rest of the UI without it).
 */
export function createScene(container: HTMLElement): SceneHandle | null {
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch (err) {
    // Most commonly this happens in happy-dom (no WebGL context)
    // or on iOS Lockdown Mode. Either way, return null and let the
    // caller render the form panel alone.
    console.warn(
      "energyplus_single_zone: WebGL unavailable, skipping 3D viz",
      err,
    );
    return null;
  }

  renderer.setSize(SCENE_WIDTH_PX, SCENE_HEIGHT_PX);
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setClearColor(0xf3f4f6); // light gray background
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();

  const camera = new THREE.PerspectiveCamera(
    35,
    SCENE_WIDTH_PX / SCENE_HEIGHT_PX,
    0.1,
    100,
  );
  camera.position.set(6, 4, 7);
  camera.lookAt(0, 1.2, 0);

  // Lighting — directional for shading + ambient for fill.
  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const sun = new THREE.DirectionalLight(0xffffff, 0.85);
  sun.position.set(5, 8, 4);
  scene.add(sun);

  // The zone — a 3 × 2.5 × 3 building (W × H × D).
  const zoneWidth = 3;
  const zoneHeight = 2.5;
  const zoneDepth = 3;

  const wallMaterial = new THREE.MeshStandardMaterial({
    color: 0xcccccc,
    roughness: 0.7,
  });
  const zoneGeometry = new THREE.BoxGeometry(zoneWidth, zoneHeight, zoneDepth);
  const zoneMesh = new THREE.Mesh(zoneGeometry, wallMaterial);
  zoneMesh.position.y = zoneHeight / 2;
  scene.add(zoneMesh);

  // Edge highlight so the cube doesn't read as a flat surface.
  const edges = new THREE.EdgesGeometry(zoneGeometry);
  const edgesMesh = new THREE.LineSegments(
    edges,
    new THREE.LineBasicMaterial({ color: 0x4b5563 }),
  );
  edgesMesh.position.copy(zoneMesh.position);
  scene.add(edgesMesh);

  // Ground plane so the cube doesn't float.
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(10, 10),
    new THREE.MeshStandardMaterial({ color: 0xe5e7eb, roughness: 0.95 }),
  );
  ground.rotation.x = -Math.PI / 2;
  scene.add(ground);

  // The window — a plane on the front face. Updated per-call to
  // reflect parameter changes. Initialize with placeholders; the
  // first update() call sizes + colors it correctly.
  const windowMaterial = new THREE.MeshStandardMaterial({
    color: 0x60a5fa,
    transparent: true,
    opacity: 0.55,
    roughness: 0.1,
    metalness: 0.05,
  });
  const windowGeometry = new THREE.PlaneGeometry(1, 1);
  const windowMesh = new THREE.Mesh(windowGeometry, windowMaterial);
  // Front face of the cube is at z = zoneDepth/2. Push the window
  // slightly forward so it doesn't z-fight with the wall.
  windowMesh.position.z = zoneDepth / 2 + 0.001;
  windowMesh.position.y = zoneHeight / 2;
  scene.add(windowMesh);

  let rafHandle = 0;
  let disposed = false;

  /** Animation loop — gentle auto-rotation. */
  function tick(): void {
    if (disposed) return;
    zoneMesh.rotation.y += 0.003;
    edgesMesh.rotation.y = zoneMesh.rotation.y;
    windowMesh.position.x = Math.sin(zoneMesh.rotation.y) * (zoneDepth / 2 + 0.001);
    windowMesh.position.z = Math.cos(zoneMesh.rotation.y) * (zoneDepth / 2 + 0.001);
    windowMesh.rotation.y = zoneMesh.rotation.y;
    renderer.render(scene, camera);
    rafHandle = requestAnimationFrame(tick);
  }
  tick();

  return {
    update({ glazing_u_value, window_area, climate_zone }): void {
      // Scale the window plane to match window_area. Clamp so the
      // window can't exceed the wall.
      // The wall area on the front face is zoneWidth * zoneHeight
      // = 7.5 m². Use sqrt-of-area for the side length so a 5 m²
      // window comes out as roughly 2.24 × 2.24 m, then clamp.
      const side = Math.sqrt(Math.max(0.5, window_area));
      const clampedW = Math.min(side, zoneWidth - 0.2);
      const clampedH = Math.min(side, zoneHeight - 0.2);
      windowMesh.scale.set(clampedW, clampedH, 1);

      // U-value → glass opacity. Range 0.5-6.0 maps to opacity 0.85
      // (well-insulated, more opaque) down to 0.25 (single-pane,
      // mostly clear).
      const uValueClamped = Math.min(Math.max(glazing_u_value, 0.5), 6.0);
      const opacityRange = 0.85 - 0.25;
      const normalized = (uValueClamped - 0.5) / (6.0 - 0.5);
      windowMaterial.opacity = 0.85 - normalized * opacityRange;

      // Climate zone → wall tint. Cooler tone for colder zones.
      const climateColor =
        CLIMATE_DATA[climate_zone]?.wallTintHex ?? 0xcccccc;
      wallMaterial.color.setHex(climateColor);
    },

    dispose(): void {
      disposed = true;
      cancelAnimationFrame(rafHandle);
      renderer.dispose();
      windowGeometry.dispose();
      windowMaterial.dispose();
      zoneGeometry.dispose();
      wallMaterial.dispose();
      edges.dispose();
      ground.geometry.dispose();
      (ground.material as THREE.Material).dispose();
      renderer.domElement.remove();
    },
  };
}
