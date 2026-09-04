import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { additive, PALETTE, useEngine } from "./context";
import { FILAMENT_FRAG, FILAMENT_VERT, HALO_FRAG } from "./shaders";

interface FilamentProps {
  inner: number;
  outer: number;
  density: number;
  speed: number;
  intensity: number;
  sharp: number;
  z: number;
  spin: number;
  levelGain?: number;
}

export const FilamentDisc = ({ inner, outer, density, speed, intensity, sharp, z, spin, levelGain = 0.8 }: FilamentProps) => {
  const engine = useEngine();
  const mesh = useRef<THREE.Mesh>(null);
  const material = useMemo(
    () =>
      additive(FILAMENT_VERT, FILAMENT_FRAG, {
        uTime: { value: 0 },
        uLevel: { value: 0 },
        uBias: { value: 0 },
        uInner: { value: inner },
        uOuter: { value: outer },
        uDensity: { value: density },
        uSpeed: { value: speed },
        uIntensity: { value: intensity },
        uSharp: { value: sharp },
        uWarm: { value: PALETTE.warm },
        uCool: { value: PALETTE.cool2 },
      }),
    [inner, outer, density, speed, intensity, sharp],
  );
  const geometry = useMemo(() => new THREE.PlaneGeometry(outer * 2, outer * 2), [outer]);

  useFrame(() => {
    const u = material.uniforms;
    u.uTime.value = engine.time;
    u.uLevel.value = engine.activity;
    u.uBias.value = engine.bias;
    u.uIntensity.value = intensity * (0.5 + engine.activity * levelGain * 0.5);
    if (mesh.current) mesh.current.rotation.z = engine.rot * spin;
  });

  return <mesh ref={mesh} geometry={geometry} material={material} position={[0, 0, z]} frustumCulled={false} />;
};

export const Halo = ({ size, z, intensity }: { size: number; z: number; intensity: number }) => {
  const engine = useEngine();
  const material = useMemo(
    () =>
      additive(FILAMENT_VERT, HALO_FRAG, {
        uTime: { value: 0 },
        uLevel: { value: 0 },
        uBias: { value: 0 },
        uIntensity: { value: intensity },
        uWarm: { value: PALETTE.warm },
        uCool: { value: PALETTE.cool },
      }),
    [intensity],
  );
  const geometry = useMemo(() => new THREE.PlaneGeometry(size, size), [size]);

  useFrame(() => {
    const u = material.uniforms;
    u.uTime.value = engine.time;
    u.uLevel.value = engine.activity;
    u.uBias.value = engine.bias;
    u.uIntensity.value = intensity * (0.6 + engine.activity * 0.25);
  });

  return <mesh geometry={geometry} material={material} position={[0, 0, z]} frustumCulled={false} />;
};
