import { ReactNode, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { useEngine } from "./context";

const ENTITY_DIAMETER = 3.9;
const damp = (dt: number, k: number) => 1 - Math.exp(-dt * k);

interface Props {
  reducedMotion: boolean;
  children: ReactNode;
}

// Smooths activityLevel, advances entity time/rotation and composes camera + group transforms
export const EntityController = ({ reducedMotion, children }: Props) => {
  const engine = useEngine();
  const group = useRef<THREE.Group>(null);
  const { viewport, camera } = useThree();

  useFrame((st, rawDt) => {
    const dt = Math.min(rawDt, 0.05);
    const e = engine;
    const k = e.target > e.level ? 2.6 : 1.1;
    e.level += (e.target - e.level) * damp(dt, k);
    e.pulse *= Math.exp(-dt * 3.2);
    e.bias += (e.biasTarget - e.bias) * damp(dt, 1.4);
    e.activity = Math.min(1, e.level + e.pulse);

    const motion = reducedMotion ? 0.18 : 1;
    e.time += dt * (0.35 + e.activity * 1.1) * motion;
    e.rot += dt * (0.04 + e.activity * 0.14) * motion;

    const g = group.current;
    if (!g) return;
    const portrait = viewport.width < viewport.height;
    const fit = Math.min(
      (0.56 * viewport.height) / ENTITY_DIAMETER,
      ((portrait ? 0.82 : 0.7) * viewport.width) / ENTITY_DIAMETER,
    );
    const breath = 1 + Math.sin(e.time * 1.5) * 0.012 * (1 + e.activity);
    const swell = 0.9 + e.activity * 0.2;
    g.scale.setScalar(fit * breath * swell);
    g.position.y = viewport.height * (portrait ? 0.09 : 0.075);

    const drift = reducedMotion ? 0 : 1;
    const px = reducedMotion ? 0 : st.pointer.x;
    const py = reducedMotion ? 0 : st.pointer.y;
    const tx = px * 0.32 + Math.sin(e.time * 0.23) * 0.12 * drift;
    const ty = py * 0.2 + Math.cos(e.time * 0.17) * 0.08 * drift;
    camera.position.x += (tx - camera.position.x) * damp(dt, 1.4);
    camera.position.y += (ty - camera.position.y) * damp(dt, 1.4);
    camera.lookAt(0, g.position.y * 0.75, 0);
  });

  return <group ref={group}>{children}</group>;
};
