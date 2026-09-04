export const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0);
  const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy));
  vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);
  vec3 l=1.0-g;
  vec3 i1=min(g.xyz,l.zxy);
  vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;
  vec3 x2=x0-i2+C.yyy;
  vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857;
  vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z);
  vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy;
  vec4 y=y_*ns.x+ns.yyyy;
  vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);
  vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0;
  vec4 s1=floor(b1)*2.0+1.0;
  vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;
  vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);
  vec3 p1=vec3(a0.zw,h.y);
  vec3 p2=vec3(a1.xy,h.z);
  vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);
  m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
`;

// Shared: warm/cool split by angle around the ring, nudged by uBias (-1 cool .. +1 warm)
const SPLIT = /* glsl */ `
float splitFor(float ang, float bias, float wobble){
  float s = 0.5 + 0.5*cos(ang - 0.65 + wobble);
  return clamp(s + bias*0.32, 0.0, 1.0);
}
`;

export const RING_VERT = /* glsl */ `
uniform float uTime;
uniform float uLevel;
uniform float uAsym;
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vPos;
varying float vNoise;
varying float vAngle;
${NOISE}
void main(){
  vec3 p = position;
  float ang = atan(p.y, p.x);
  float asym = 0.55 + 0.45*sin(ang - 0.9);
  float n = snoise(vec3(p.xy*1.15, p.z*1.4 + uTime*0.35));
  float n2 = snoise(vec3(p.xy*3.2 + 7.0, uTime*0.7));
  float turb = 0.5 + uLevel*0.9;
  float disp = (n*0.24 + n2*0.07) * (0.6 + asym*uAsym) * turb;
  p += normal * disp;
  p.xy += normalize(p.xy) * (0.10 * asym * uAsym + uLevel * 0.14);
  vNoise = n;
  vAngle = ang;
  vPos = p;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  vNormal = normalize(normalMatrix * normal);
  vView = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}
`;

export const RING_FRAG = /* glsl */ `
uniform float uTime;
uniform float uLevel;
uniform float uBias;
uniform float uIntensity;
uniform vec3 uWarm;
uniform vec3 uWarm2;
uniform vec3 uCool;
uniform vec3 uCool2;
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vPos;
varying float vNoise;
varying float vAngle;
${NOISE}
${SPLIT}
void main(){
  float ndv = abs(dot(normalize(vNormal), normalize(vView)));
  float fres = pow(1.0 - ndv, 1.5);
  float body = pow(ndv, 2.0) * 0.35;
  float split = splitFor(vAngle, uBias, vNoise*0.9);
  vec3 warm = mix(uWarm, uWarm2, 0.5 + 0.5*sin(vNoise*3.0 + uTime*0.6));
  vec3 cool = mix(uCool, uCool2, 0.5 + 0.5*cos(vNoise*2.4 - uTime*0.45));
  vec3 col = mix(cool, warm, split);
  float flow = snoise(vec3(vPos.xy*2.6, uTime*0.9)) * 0.5 + 0.5;
  float a = (fres + body) * (0.3 + 0.7*flow) * uIntensity;
  gl_FragColor = vec4(col * a * 1.15, a);
}
`;

export const VOID_VERT = /* glsl */ `
varying vec3 vNormal;
varying vec3 vView;
void main(){
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vNormal = normalize(normalMatrix * normal);
  vView = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}
`;

export const VOID_FRAG = /* glsl */ `
uniform float uTime;
uniform float uLevel;
uniform float uBias;
uniform vec3 uRimCool;
uniform vec3 uRimWarm;
varying vec3 vNormal;
varying vec3 vView;
void main(){
  float f = 1.0 - abs(dot(normalize(vNormal), normalize(vView)));
  float rim = pow(f, 5.0);
  float inner = pow(f, 1.6) * 0.06;
  float pulse = 0.8 + 0.2*sin(uTime*1.4);
  vec3 rimCol = mix(uRimCool, uRimWarm, clamp(0.5 + uBias*0.5, 0.0, 1.0));
  vec3 col = rimCol * (rim * (0.45 + uLevel*0.9) * pulse + inner);
  gl_FragColor = vec4(col, 1.0);
}
`;

export const FILAMENT_VERT = /* glsl */ `
varying vec2 vUv;
void main(){
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

export const FILAMENT_FRAG = /* glsl */ `
uniform float uTime;
uniform float uLevel;
uniform float uBias;
uniform float uInner;
uniform float uOuter;
uniform float uDensity;
uniform float uSpeed;
uniform float uIntensity;
uniform float uSharp;
uniform vec3 uWarm;
uniform vec3 uCool;
varying vec2 vUv;
${NOISE}
${SPLIT}
void main(){
  vec2 p = (vUv - 0.5) * 2.0 * uOuter;
  float r = length(p);
  if (r < uInner || r > uOuter) discard;
  float ang = atan(p.y, p.x);
  float t = (r - uInner) / (uOuter - uInner);
  float swirl = ang + t * 0.9 * sin(uTime * 0.08 + 1.3);
  vec3 q = vec3(cos(swirl) * uDensity, sin(swirl) * uDensity, t * 2.4 - uTime * uSpeed);
  float n = snoise(q) * 0.65 + snoise(q * 2.3 + 11.0) * 0.35;
  float fil = smoothstep(uSharp, uSharp + 0.32, n);
  float fade = pow(1.0 - t, 2.2) * smoothstep(0.0, 0.12, t);
  float asym = 0.7 + 0.3*sin(ang - 0.9);
  float split = splitFor(ang, uBias, n * 0.6);
  vec3 col = mix(uCool, uWarm, split);
  float a = fil * fade * asym * uIntensity;
  gl_FragColor = vec4(col * a, a);
}
`;

export const HALO_FRAG = /* glsl */ `
uniform float uTime;
uniform float uLevel;
uniform float uBias;
uniform float uIntensity;
uniform vec3 uWarm;
uniform vec3 uCool;
varying vec2 vUv;
${NOISE}
${SPLIT}
void main(){
  vec2 p = (vUv - 0.5) * 2.0;
  float r = length(p);
  float ang = atan(p.y, p.x);
  float cloud = snoise(vec3(p * 1.8, uTime * 0.12)) * 0.5 + 0.5;
  float g = exp(-r * r * 4.2) * (0.55 + 0.45 * cloud);
  float split = splitFor(ang, uBias, cloud * 0.8);
  vec3 col = mix(uCool, uWarm, split);
  float a = g * uIntensity;
  gl_FragColor = vec4(col * a, a);
}
`;

export const PARTICLE_VERT = /* glsl */ `
attribute float aSeed;
attribute float aSize;
attribute float aRadius;
attribute float aPhase;
attribute float aZ;
uniform float uTime;
uniform float uLevel;
uniform float uSpeed;
uniform float uPixelRatio;
uniform float uScale;
varying float vAlpha;
varying float vWarm;
${NOISE}
void main(){
  float ang = aPhase + uTime * uSpeed * (0.5 + aSeed) / max(aRadius, 0.4);
  float spread = 1.0 + uLevel * (0.12 + aSeed * 0.3);
  vec3 p = vec3(cos(ang) * aRadius * spread, sin(ang) * aRadius * spread * 0.94, aZ * spread);
  float n = snoise(vec3(p.xy * 0.7, uTime * 0.2 + aSeed * 9.0));
  p.xy += vec2(n, snoise(vec3(p.yx * 0.7, aSeed * 5.0 - uTime * 0.2))) * 0.18 * (0.4 + uLevel);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_PointSize = aSize * uPixelRatio * uScale * (240.0 / -mv.z) * (0.85 + uLevel * 0.45);
  vAlpha = 0.3 + 0.7 * (0.5 + 0.5 * sin(uTime * (1.2 + aSeed * 2.5) + aSeed * 40.0));
  vWarm = 0.5 + 0.5 * cos(ang - 0.65);
  gl_Position = projectionMatrix * mv;
}
`;

export const PARTICLE_FRAG = /* glsl */ `
uniform float uIntensity;
uniform float uBias;
uniform vec3 uWarm;
uniform vec3 uCool;
varying float vAlpha;
varying float vWarm;
void main(){
  float d = length(gl_PointCoord - 0.5) * 2.0;
  if (d > 1.0) discard;
  float soft = pow(1.0 - d, 2.2);
  float w = clamp(vWarm + uBias * 0.3, 0.0, 1.0);
  vec3 col = mix(uCool, uWarm, w);
  float a = soft * vAlpha * uIntensity;
  gl_FragColor = vec4(col * a, a);
}
`;

export const STAR_VERT = /* glsl */ `
attribute float aSeed;
attribute float aSize;
uniform float uTime;
uniform float uPixelRatio;
varying float vAlpha;
void main(){
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = aSize * uPixelRatio * (160.0 / -mv.z);
  vAlpha = 0.35 + 0.65 * (0.5 + 0.5 * sin(uTime * (0.6 + aSeed * 1.5) + aSeed * 60.0));
  gl_Position = projectionMatrix * mv;
}
`;

export const STAR_FRAG = /* glsl */ `
varying float vAlpha;
void main(){
  float d = length(gl_PointCoord - 0.5) * 2.0;
  if (d > 1.0) discard;
  float a = pow(1.0 - d, 2.0) * vAlpha * 0.55;
  gl_FragColor = vec4(vec3(0.85, 0.82, 0.95) * a, a);
}
`;
