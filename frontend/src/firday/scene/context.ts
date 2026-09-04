import { createContext, useContext } from "react";
import * as THREE from "three";
import { EntityEngine } from "../types";

export const EngineContext = createContext<EntityEngine>(null!);
export const useEngine = () => useContext(EngineContext);

export const PALETTE = {
  warm: new THREE.Color("#FF5C93"),
  warm2: new THREE.Color("#FF9E79"),
  cool: new THREE.Color("#6C5CE7"),
  cool2: new THREE.Color("#00CEC9"),
  rimCool: new THREE.Color("#4A3A9A"),
  rimWarm: new THREE.Color("#B04A7A"),
  void: new THREE.Color("#03020A"),
};

export const additive = (vertexShader: string, fragmentShader: string, uniforms: Record<string, THREE.IUniform>) =>
  new THREE.ShaderMaterial({
    vertexShader,
    fragmentShader,
    uniforms,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    side: THREE.DoubleSide,
  });
