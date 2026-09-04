import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { additive, PALETTE, useEngine } from "./context";
import { RING_FRAG, RING_VERT } from "./shaders";

interface RingProps {
  radius: number;
  tube: number;
  intensity: number;
  asym: number;
  tilt?: [number, number, number];
  spin?: number;
  segments?: [number, number];
}

const Ring = ({ radius, tube, intensity, asym, tilt = [0, 0, 0], spin = 1, segments = [40, 160] }: RingProps) => {
  const engine = useEngine();
  const mesh = useRef<THREE.Mesh>(null);
  const material = useMemo(
    () =>
      additive(RING_VERT, RING_FRAG, {
        uTime: { value: 0 },
        uLevel: { value: 0 },
        uBias: { value: 0 },
        uAsym: { value: asym },
        uIntensity: { value: intensity },
        uWarm: { value: PALETTE.warm },
        uWarm2: { value: PALETTE.warm2 },
        uCool: { value: PALETTE.cool },
        uCool2: { value: PALETTE.cool2 },
      }),
    [asym, intensity],
  );
  const geometry = useMemo(() => new THREE.TorusGeometry(radius, tube, segments[0], segments[1]), [radius, tube, segments]);

  useFrame(() => {
    const u = material.uniforms;
    u.uTime.value = engine.time;
    u.uLevel.value = engine.activity;
    u.uBias.value = engine.bias;
    u.uIntensity.value = intensity * (0.65 + engine.activity * 0.35);
    if (mesh.current) mesh.current.rotation.z = tilt[2] + engine.rot * spin;
  });

  return (
    <mesh ref={mesh} geometry={geometry} material={material} rotation={tilt} frustumCulled={false} />
  );
};

export const PlasmaRing = ({ outerShell }: { outerShell: boolean }) => (
  <>
    <Ring radius={1.32} tube={0.3} intensity={0.95} asym={1} spin={0.5} />
    <Ring radius={1.42} tube={0.16} intensity={0.55} asym={0.6} tilt={[0.18, -0.12, 1.2]} spin={-0.35} segments={[24, 120]} />
    {outerShell && (
      <Ring radius={1.75} tube={0.42} intensity={0.22} asym={1.4} tilt={[-0.1, 0.08, 2.4]} spin={0.2} segments={[20, 100]} />
    )}
  </>
);
