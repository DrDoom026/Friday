import { Canvas } from "@react-three/fiber";
import { EntityEngine, PerformanceTier } from "../types";
import { TIER_CONFIG } from "../hooks/usePerformanceTier";
import { EngineContext, PALETTE } from "./context";
import { EntityController } from "./EntityController";
import { VoidCore } from "./VoidCore";
import { PlasmaRing } from "./PlasmaRing";
import { FilamentDisc, Halo } from "./Filaments";
import { ParticleLayer, Starfield } from "./Particles";
import { PerfMonitor } from "./PerfMonitor";

interface Props {
  engine: EntityEngine;
  tier: PerformanceTier;
  reducedMotion: boolean;
  onDowngrade: () => void;
  onWake: () => void;
}

export const FirdayScene = ({ engine, tier, reducedMotion, onDowngrade, onWake }: Props) => {
  const cfg = TIER_CONFIG[tier];
  const n = (base: number) => Math.max(24, Math.round(base * cfg.particleScale));

  return (
    <div className="firday-scene" data-testid="cosmic-eye-3d-canvas" onClick={onWake}>
      <Canvas
        dpr={[1, cfg.dpr]}
        gl={{ antialias: cfg.antialias, alpha: false, powerPreference: "high-performance", stencil: false }}
        camera={{ fov: 38, position: [0, 0, 6.6], near: 0.1, far: 60 }}
        onCreated={({ gl, scene }) => {
          gl.setClearColor(PALETTE.void, 1);
          scene.background = PALETTE.void;
        }}
      >
        <EngineContext.Provider value={engine}>
          <PerfMonitor onDowngrade={onDowngrade} enabled={tier !== "low"} />
          <Starfield count={n(700)} />
          <ParticleLayer count={n(700)} rMin={2.2} rMax={7} zMin={-4.5} zMax={-1.6} size={1.1} speed={0.05} intensity={0.3} />
          <EntityController reducedMotion={reducedMotion}>
            <Halo size={9} z={-1.4} intensity={0.32} />
            <FilamentDisc inner={1.5} outer={3.4} density={3.2} speed={0.22} intensity={0.85} sharp={0.4} z={-0.25} spin={-0.4} />
            {cfg.outerShell && (
              <FilamentDisc inner={1.35} outer={2.6} density={5.5} speed={0.35} intensity={0.35} sharp={0.5} z={0.12} spin={0.6} />
            )}
            <PlasmaRing outerShell={cfg.outerShell} />
            <FilamentDisc inner={0.99} outer={1.34} density={11} speed={0.55} intensity={0.9} sharp={0.3} z={0} spin={0.9} levelGain={1.2} />
            <VoidCore />
            <ParticleLayer count={n(500)} rMin={1.05} rMax={2.7} zMin={-0.45} zMax={0.45} size={1.5} speed={0.18} intensity={0.6} />
            {cfg.foreground && (
              <ParticleLayer count={n(80)} rMin={1.4} rMax={3.6} zMin={1.2} zMax={2.6} size={3.0} speed={0.08} intensity={0.28} />
            )}
          </EntityController>
        </EngineContext.Provider>
      </Canvas>
      <div className="firday-vignette" />
    </div>
  );
};
