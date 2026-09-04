import { useMemo } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { PALETTE, useEngine } from "./context";
import { VOID_FRAG, VOID_VERT } from "./shaders";

export const VoidCore = () => {
  const engine = useEngine();
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: VOID_VERT,
        fragmentShader: VOID_FRAG,
        uniforms: {
          uTime: { value: 0 },
          uLevel: { value: 0 },
          uBias: { value: 0 },
          uRimCool: { value: PALETTE.rimCool },
          uRimWarm: { value: PALETTE.rimWarm },
        },
      }),
    [],
  );
  const geometry = useMemo(() => new THREE.SphereGeometry(1, 56, 40), []);

  useFrame(() => {
    material.uniforms.uTime.value = engine.time;
    material.uniforms.uLevel.value = engine.activity;
    material.uniforms.uBias.value = engine.bias;
  });

  return <mesh geometry={geometry} material={material} frustumCulled={false} />;
};
