import { useMemo } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { additive, PALETTE, useEngine } from "./context";
import { PARTICLE_FRAG, PARTICLE_VERT, STAR_FRAG, STAR_VERT } from "./shaders";

interface LayerProps {
  count: number;
  rMin: number;
  rMax: number;
  zMin: number;
  zMax: number;
  size: number;
  speed: number;
  intensity: number;
  warmColor?: THREE.Color;
  coolColor?: THREE.Color;
}

const rand = (a: number, b: number) => a + Math.random() * (b - a);

export const ParticleLayer = ({ count, rMin, rMax, zMin, zMax, size, speed, intensity, warmColor = PALETTE.warm2, coolColor = PALETTE.cool2 }: LayerProps) => {
  const engine = useEngine();
  const { gl } = useThree();

  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(count * 3);
    const seed = new Float32Array(count);
    const sz = new Float32Array(count);
    const radius = new Float32Array(count);
    const phase = new Float32Array(count);
    const z = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      seed[i] = Math.random();
      sz[i] = size * rand(0.4, 1.4);
      radius[i] = Math.sqrt(rand(rMin * rMin, rMax * rMax));
      phase[i] = rand(0, Math.PI * 2);
      z[i] = rand(zMin, zMax);
    }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
    g.setAttribute("aSize", new THREE.BufferAttribute(sz, 1));
    g.setAttribute("aRadius", new THREE.BufferAttribute(radius, 1));
    g.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
    g.setAttribute("aZ", new THREE.BufferAttribute(z, 1));
    return g;
  }, [count, rMin, rMax, zMin, zMax, size]);

  const material = useMemo(
    () =>
      additive(PARTICLE_VERT, PARTICLE_FRAG, {
        uTime: { value: 0 },
        uLevel: { value: 0 },
        uBias: { value: 0 },
        uSpeed: { value: speed },
        uPixelRatio: { value: gl.getPixelRatio() },
        uScale: { value: 1 },
        uIntensity: { value: intensity },
        uWarm: { value: warmColor },
        uCool: { value: coolColor },
      }),
    [speed, intensity, gl, warmColor, coolColor],
  );

  useFrame(() => {
    const u = material.uniforms;
    u.uTime.value = engine.time;
    u.uLevel.value = engine.activity;
    u.uBias.value = engine.bias;
    u.uPixelRatio.value = gl.getPixelRatio();
    u.uIntensity.value = intensity * (0.65 + engine.activity * 0.3);
  });

  return <points geometry={geometry} material={material} frustumCulled={false} />;
};

export const Starfield = ({ count }: { count: number }) => {
  const engine = useEngine();
  const { gl } = useThree();
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(count * 3);
    const seed = new Float32Array(count);
    const sz = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      const r = rand(9, 22);
      const theta = rand(0, Math.PI * 2);
      const phi = Math.acos(rand(-1, 1));
      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta) * 0.7;
      pos[i * 3 + 2] = -Math.abs(r * Math.cos(phi)) - 4;
      seed[i] = Math.random();
      sz[i] = rand(0.6, 1.8);
    }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
    g.setAttribute("aSize", new THREE.BufferAttribute(sz, 1));
    return g;
  }, [count]);
  const material = useMemo(
    () =>
      additive(STAR_VERT, STAR_FRAG, {
        uTime: { value: 0 },
        uPixelRatio: { value: gl.getPixelRatio() },
      }),
    [gl],
  );
  useFrame(() => {
    material.uniforms.uTime.value = engine.time;
    material.uniforms.uPixelRatio.value = gl.getPixelRatio();
  });
  return <points geometry={geometry} material={material} frustumCulled={false} />;
};
