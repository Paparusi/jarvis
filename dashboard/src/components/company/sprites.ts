/* ── Pixel Art Sprite System - Warm RPG Maker Style ──
 * Characters: 16x24 game pixels (RPG Maker / GBA style)
 * Furniture: Various sizes, warm muted palette
 * Style: Stardew Valley / Pokémon / Game Dev Tycoon
 */

const _ = -1;
const S = 0;  // skin
const D = 1;  // skin shadow
const H = 2;  // hair
const E = 3;  // eye
const W = 4;  // eye white
const M = 5;  // mouth
const C = 6;  // clothes
const K = 7;  // clothes dark
const P = 8;  // pants
const F = 9;  // shoes
const G = 10; // glasses
const X = 11; // highlight on clothes
const T = 12; // tie (for CEO)

export const RAW_W = 16;
export const RAW_H = 24;
export const CHAR_W = 18; // 16 + 2 outline
export const CHAR_H = 26; // 24 + 2 outline

/* ── Base character sprite (16x24) ── */
const BASE: number[][] = [
  [_, _, _, _, _, H, H, H, H, H, H, _, _, _, _, _],
  [_, _, _, _, H, H, H, H, H, H, H, H, _, _, _, _],
  [_, _, _, H, H, H, H, H, H, H, H, H, H, _, _, _],
  [_, _, _, H, H, H, H, H, H, H, H, H, H, _, _, _],
  [_, _, _, H, S, S, S, S, S, S, S, S, H, _, _, _],
  [_, _, _, S, W, E, S, S, S, S, W, E, S, _, _, _],
  [_, _, _, S, S, S, S, D, D, S, S, S, S, _, _, _],
  [_, _, _, _, S, S, M, M, M, M, S, S, _, _, _, _],
  [_, _, _, _, _, D, S, S, S, S, D, _, _, _, _, _],
  [_, _, _, _, C, C, X, X, X, X, C, C, _, _, _, _],
  [_, _, _, C, C, C, X, X, X, X, C, C, C, _, _, _],
  [_, _, S, C, C, C, C, C, C, C, C, C, C, S, _, _],
  [_, _, S, C, C, C, C, C, C, C, C, C, C, S, _, _],
  [_, _, _, C, C, C, C, C, C, C, C, C, C, _, _, _],
  [_, _, _, C, C, C, C, C, C, C, C, C, C, _, _, _],
  [_, _, _, _, K, K, K, C, C, K, K, K, _, _, _, _],
  [_, _, _, _, _, P, P, P, P, P, P, _, _, _, _, _],
  [_, _, _, _, _, P, P, _, _, P, P, _, _, _, _, _],
  [_, _, _, _, _, P, P, _, _, P, P, _, _, _, _, _],
  [_, _, _, _, _, P, P, _, _, P, P, _, _, _, _, _],
  [_, _, _, _, _, P, _, _, _, _, P, _, _, _, _, _],
  [_, _, _, _, _, P, _, _, _, _, P, _, _, _, _, _],
  [_, _, _, _, F, F, F, _, _, F, F, F, _, _, _, _],
  [_, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _],
];

// Walk frames
const WALK1_LEGS: number[][] = [
  [_, _, _, _, _, _, P, P, _, _, _, _, _, _, _, _],
  [_, _, _, _, _, P, P, _, _, _, P, P, _, _, _, _],
  [_, _, _, _, _, P, _, _, _, _, P, _, _, _, _, _],
  [_, _, _, _, _, P, _, _, _, _, P, P, _, _, _, _],
  [_, _, _, _, F, F, _, _, _, F, F, F, _, _, _, _],
];

const WALK2_LEGS: number[][] = [
  [_, _, _, _, _, P, P, _, _, P, P, _, _, _, _, _],
  [_, _, _, _, _, P, P, _, _, _, P, _, _, _, _, _],
  [_, _, _, _, _, P, _, _, _, _, P, _, _, _, _, _],
  [_, _, _, _, _, _, _, _, _, _, P, P, _, _, _, _],
  [_, _, _, _, _, _, _, _, _, F, F, F, _, _, _, _],
];

const SIT_ROWS: number[][] = [
  [_, _, _, _, K, K, K, C, C, K, K, K, _, _, _, _],
  [_, _, _, _, _, P, P, P, P, P, P, _, _, _, _, _],
  [_, _, _, _, _, P, P, _, _, P, P, _, _, _, _, _],
  [_, _, _, _, F, F, _, _, _, _, F, F, _, _, _, _],
  [_, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _],
];

// Work arms (typing)
const WORK_ARMS: number[][] = [
  [_, _, S, C, C, C, C, C, C, C, C, C, C, S, _, _],
  [_, S, S, C, C, C, C, C, C, C, C, C, C, S, S, _],
];

const WORK_ARMS_BOB: number[][] = [
  [_, _, S, C, C, C, C, C, C, C, C, C, C, S, _, _],
  [_, S, C, C, C, C, C, C, C, C, C, C, C, C, S, _],
];

/* ── Hair styles ── */
const HAIR: Record<string, number[][]> = {
  normal: [
    [_, _, _, _, _, H, H, H, H, H, H, _, _, _, _, _],
    [_, _, _, _, H, H, H, H, H, H, H, H, _, _, _, _],
    [_, _, _, H, H, H, H, H, H, H, H, H, H, _, _, _],
    [_, _, _, H, H, H, H, H, H, H, H, H, H, _, _, _],
  ],
  spiky: [
    [_, _, _, H, _, H, H, H, _, H, H, _, H, _, _, _],
    [_, _, _, _, H, H, H, H, H, H, H, H, _, _, _, _],
    [_, _, _, H, H, H, H, H, H, H, H, H, H, _, _, _],
    [_, _, _, H, H, H, H, H, H, H, H, H, H, _, _, _],
  ],
  neat: [
    [_, _, _, _, H, H, H, H, H, H, H, H, _, _, _, _],
    [_, _, _, H, H, H, H, H, H, H, H, H, H, _, _, _],
    [_, _, _, H, H, H, H, H, H, H, H, H, H, _, _, _],
    [_, _, _, H, H, H, H, H, H, H, H, H, H, _, _, _],
  ],
  long: [
    [_, _, _, _, _, H, H, H, H, H, H, _, _, _, _, _],
    [_, _, _, _, H, H, H, H, H, H, H, H, _, _, _, _],
    [_, H, H, H, H, H, H, H, H, H, H, H, H, H, H, _],
    [_, H, H, H, S, S, S, S, S, S, S, S, H, H, H, _],
  ],
  buzz: [
    [_, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _],
    [_, _, _, _, _, H, H, H, H, H, H, _, _, _, _, _],
    [_, _, _, _, H, H, H, H, H, H, H, H, _, _, _, _],
    [_, _, _, H, H, H, H, H, H, H, H, H, H, _, _, _],
  ],
  mohawk: [
    [_, _, _, _, _, _, H, H, H, H, _, _, _, _, _, _],
    [_, _, _, _, _, H, H, H, H, H, H, _, _, _, _, _],
    [_, _, _, _, H, H, H, H, H, H, H, H, _, _, _, _],
    [_, _, _, H, H, H, H, H, H, H, H, H, H, _, _, _],
  ],
};

interface Look { hair: string; hairColor: string; skinTone: string; glasses?: boolean; hasTie?: boolean }
const LOOKS: Record<string, Look> = {
  "ceo":                         { hair: "neat",   hairColor: "#1A1A28", skinTone: "#E8B888", hasTie: true },
  "finance.market_analyst":      { hair: "normal", hairColor: "#8B4513", skinTone: "#D4A06A" },
  "finance.trader":              { hair: "spiky",  hairColor: "#1A1A28", skinTone: "#F0C8A0" },
  "finance.crypto_specialist":   { hair: "mohawk", hairColor: "#D4A020", skinTone: "#C89060" },
  "security.pen_tester":         { hair: "buzz",   hairColor: "#1A1A28", skinTone: "#8B6040" },
  "security.researcher":         { hair: "long",   hairColor: "#8B2020", skinTone: "#E8B888" },
  "engineering.developer":       { hair: "spiky",  hairColor: "#3A2A1A", skinTone: "#D4A06A", glasses: true },
  "engineering.devops":          { hair: "buzz",   hairColor: "#C06030", skinTone: "#F0C8A0" },
  "research.product_researcher": { hair: "long",   hairColor: "#D8B870", skinTone: "#C89060" },
  "research.data_analyst":       { hair: "neat",   hairColor: "#1A1A28", skinTone: "#8B6040", glasses: true },
  "operations.office_manager":   { hair: "normal", hairColor: "#A06030", skinTone: "#E8B888" },
  "sales.account_exec":          { hair: "neat",   hairColor: "#5A3A20", skinTone: "#F0C8A0" },
  "sales.lead_gen":              { hair: "spiky",  hairColor: "#D8B870", skinTone: "#D4A06A" },
  "marketing.content_creator":   { hair: "long",   hairColor: "#C03050", skinTone: "#E8B888" },
  "marketing.social_manager":    { hair: "normal", hairColor: "#2A1A30", skinTone: "#D4A06A", glasses: true },
};

/* ── Department outfits (muted, warm colors) ── */
const OUTFITS: Record<string, { main: string; dark: string; highlight: string }> = {
  ceo:         { main: "#3A3A4A", dark: "#2A2A38", highlight: "#5A5A6A" },  // dark suit
  finance:     { main: "#2A4A7A", dark: "#1A3A6A", highlight: "#4A6A9A" },  // navy blue
  security:    { main: "#7A2A2A", dark: "#5A1A1A", highlight: "#9A4A4A" },  // dark red
  engineering: { main: "#5A4A7A", dark: "#4A3A6A", highlight: "#7A6A9A" },  // purple
  research:    { main: "#B06830", dark: "#904820", highlight: "#D08850" },  // orange-brown
  operations:  { main: "#5A6A5A", dark: "#4A5A4A", highlight: "#7A8A7A" },  // olive
  sales:       { main: "#3A7A5A", dark: "#2A6A4A", highlight: "#5A9A7A" },  // teal green
  marketing:   { main: "#9A3A6A", dark: "#7A2A5A", highlight: "#BA5A8A" },  // magenta
};

const EYE_CLR    = "#2A2020";
const WHITE_CLR  = "#F0F0E8";
const MOUTH_CLR  = "#D08860";
const PANTS_CLR  = "#4A4A60";
const SHOES_CLR  = "#2A2A30";
const GLASS_CLR  = "#8ABCE0";
const TIE_CLR    = "#8A3030";

function resolveColor(px: number, wId: string, dept: string): string {
  const look = LOOKS[wId] || { hair: "normal", hairColor: "#6A4A30", skinTone: "#E8B888" };
  const out = OUTFITS[dept] || OUTFITS.operations;
  switch (px) {
    case S: return look.skinTone;
    case D: return "#C9956B";
    case H: return look.hairColor;
    case E: return EYE_CLR;
    case W: return WHITE_CLR;
    case M: return MOUTH_CLR;
    case C: return out.main;
    case K: return out.dark;
    case X: return out.highlight;
    case P: return PANTS_CLR;
    case F: return SHOES_CLR;
    case G: return GLASS_CLR;
    case T: return TIE_CLR;
    default: return "";
  }
}

function buildFrame(
  wId: string,
  mod?: {
    legs?: number[][];
    legsStart?: number;
    arms?: number[][];
    armsStart?: number;
    headBob?: boolean;
  }
): number[][] {
  const look = LOOKS[wId] || { hair: "normal", hairColor: "#6A4A30", skinTone: "#E8B888" };
  const hairRows = HAIR[look.hair] || HAIR.normal;
  const frame = BASE.map(r => [...r]);
  
  for (let i = 0; i < hairRows.length; i++) frame[i] = [...hairRows[i]];
  
  if (look.glasses) {
    frame[5] = [...frame[5]];
    frame[5][3] = G; frame[5][6] = G; frame[5][9] = G; frame[5][12] = G;
  }
  
  if (look.hasTie) {
    frame[9] = [...frame[9]];
    frame[10] = [...frame[10]];
    frame[11] = [...frame[11]];
    frame[9][7] = T; frame[9][8] = T;
    frame[10][7] = T; frame[10][8] = T;
    frame[11][7] = T; frame[11][8] = T;
  }
  
  if (mod?.headBob) {
    const temp = [...frame];
    for (let i = 8; i >= 0; i--) {
      frame[i + 1] = [...temp[i]];
    }
    frame[0] = Array(RAW_W).fill(-1);
  }
  
  if (mod?.legs) {
    const s = mod.legsStart ?? 17;
    for (let i = 0; i < mod.legs.length; i++) frame[s + i] = [...mod.legs[i]];
  }
  
  if (mod?.arms) {
    const s = mod.armsStart ?? 11;
    for (let i = 0; i < mod.arms.length; i++) frame[s + i] = [...mod.arms[i]];
  }
  
  return frame;
}

function renderChar(pixels: number[][], wId: string, dept: string): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = RAW_W + 2;
  c.height = RAW_H + 2;
  const ctx = c.getContext("2d")!;

  // Pass 1: dark outline for visibility
  const OUTLINE = "#1A1A28";
  for (let y = 0; y < RAW_H; y++) {
    for (let x = 0; x < RAW_W; x++) {
      if (pixels[y][x] === -1) continue;
      const ox = x + 1, oy = y + 1;
      ctx.fillStyle = OUTLINE;
      if (x === 0 || pixels[y][x - 1] === -1) ctx.fillRect(ox - 1, oy, 1, 1);
      if (x === RAW_W - 1 || pixels[y][x + 1] === -1) ctx.fillRect(ox + 1, oy, 1, 1);
      if (y === 0 || pixels[y - 1]?.[x] === -1) ctx.fillRect(ox, oy - 1, 1, 1);
      if (y === RAW_H - 1 || pixels[y + 1]?.[x] === -1) ctx.fillRect(ox, oy + 1, 1, 1);
    }
  }

  // Pass 2: colored sprite
  for (let y = 0; y < RAW_H; y++) {
    for (let x = 0; x < RAW_W; x++) {
      if (pixels[y][x] === -1) continue;
      ctx.fillStyle = resolveColor(pixels[y][x], wId, dept);
      ctx.fillRect(x + 1, y + 1, 1, 1);
    }
  }

  return c;
}

function flipCanvas(src: HTMLCanvasElement): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = src.width;
  c.height = src.height;
  const ctx = c.getContext("2d")!;
  ctx.translate(c.width, 0);
  ctx.scale(-1, 1);
  ctx.drawImage(src, 0, 0);
  return c;
}

export interface CharSprites {
  stand: HTMLCanvasElement;
  walk1: HTMLCanvasElement;
  walk2: HTMLCanvasElement;
  sit: HTMLCanvasElement;
  work1: HTMLCanvasElement;
  work2: HTMLCanvasElement;
  standL: HTMLCanvasElement;
  walk1L: HTMLCanvasElement;
  walk2L: HTMLCanvasElement;
}

export function preRenderChar(wId: string, dept: string): CharSprites {
  const stand = renderChar(buildFrame(wId), wId, dept);
  const walk1 = renderChar(buildFrame(wId, { legs: WALK1_LEGS, legsStart: 19 }), wId, dept);
  const walk2 = renderChar(buildFrame(wId, { legs: WALK2_LEGS, legsStart: 19 }), wId, dept);
  const sit   = renderChar(buildFrame(wId, { legs: SIT_ROWS, legsStart: 19 }), wId, dept);
  const work1 = renderChar(buildFrame(wId, { legs: SIT_ROWS, legsStart: 19, arms: WORK_ARMS }), wId, dept);
  const work2 = renderChar(buildFrame(wId, { legs: SIT_ROWS, legsStart: 19, arms: WORK_ARMS_BOB, headBob: true }), wId, dept);
  
  return {
    stand, walk1, walk2, sit, work1, work2,
    standL: flipCanvas(stand),
    walk1L: flipCanvas(walk1),
    walk2L: flipCanvas(walk2),
  };
}

/* ── DESK (48x32) with monitor, keyboard ── */
export const DESK_W = 48;
export const DESK_H = 32;

export function preRenderDesk(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = DESK_W;
  c.height = DESK_H;
  const ctx = c.getContext("2d")!;
  
  // Desk surface (wood grain effect)
  ctx.fillStyle = "#7A5030";
  ctx.fillRect(0, 18, 48, 14);
  ctx.fillStyle = "#8A6040"; // lighter top edge
  ctx.fillRect(1, 18, 46, 2);
  ctx.fillStyle = "#6A4020"; // darker bottom edge  
  ctx.fillRect(0, 30, 48, 2);
  // Subtle wood grain lines
  ctx.fillStyle = "#704828";
  ctx.fillRect(4, 22, 40, 1);
  ctx.fillRect(8, 26, 32, 1);
  
  // Desk legs
  ctx.fillStyle = "#5C3A1E";
  ctx.fillRect(2, 30, 3, 2);
  ctx.fillRect(43, 30, 3, 2);
  
  // Monitor stand (thin pole + base)
  ctx.fillStyle = "#3A3A4A";
  ctx.fillRect(22, 16, 4, 3);
  ctx.fillStyle = "#4A4A5A";
  ctx.fillRect(20, 17, 8, 1);
  
  // Monitor (with bezel and screen reflection)
  ctx.fillStyle = "#1A1A22"; // outer bezel
  ctx.fillRect(12, 0, 24, 17);
  ctx.fillStyle = "#2A2A32"; // inner bezel
  ctx.fillRect(13, 1, 22, 15);
  ctx.fillStyle = "#6AA8D8"; // screen base color
  ctx.fillRect(14, 2, 20, 13);
  // Screen content (text lines)
  ctx.fillStyle = "#90C8E8";
  ctx.fillRect(16, 4, 12, 1);
  ctx.fillRect(16, 7, 8, 1);
  ctx.fillRect(16, 10, 14, 1);
  // Screen highlight (reflection)
  ctx.fillStyle = "#A0D8F0";
  ctx.fillRect(14, 2, 8, 2);
  
  // Keyboard
  ctx.fillStyle = "#4A4A58";
  ctx.fillRect(6, 22, 14, 5);
  ctx.fillStyle = "#5A5A68"; // key highlights
  ctx.fillRect(7, 23, 12, 1);
  ctx.fillRect(7, 25, 12, 1);
  
  // Mouse
  ctx.fillStyle = "#4A4A58";
  ctx.fillRect(36, 23, 4, 5);
  ctx.fillStyle = "#5A5A68";
  ctx.fillRect(37, 24, 2, 1);
  
  // Coffee mug
  ctx.fillStyle = "#D8D8D0"; // mug body
  ctx.fillRect(40, 19, 4, 5);
  ctx.fillStyle = "#8A5A30"; // coffee
  ctx.fillRect(41, 19, 2, 1);
  ctx.fillStyle = "#C0C0B8"; // handle
  ctx.fillRect(44, 21, 1, 2);
  
  // Small plant/pen holder
  ctx.fillStyle = "#C04040"; // red cup
  ctx.fillRect(2, 20, 3, 4);
  ctx.fillStyle = "#40A040"; // pens
  ctx.fillRect(3, 18, 1, 3);
  
  return c;
}

/* ── OFFICE CHAIR (16x16) ── */
export const CHAIR_W = 16;
export const CHAIR_H = 16;

export function preRenderChair(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = CHAIR_W;
  c.height = CHAIR_H;
  const ctx = c.getContext("2d")!;
  
  const CL = "#C8A878"; // chair light
  const CD = "#A08860"; // chair dark
  
  // Back
  ctx.fillStyle = CL;
  ctx.fillRect(5, 0, 6, 8);
  ctx.fillStyle = CD;
  ctx.fillRect(5, 0, 2, 8);
  
  // Seat
  ctx.fillStyle = CL;
  ctx.fillRect(4, 8, 8, 4);
  ctx.fillStyle = CD;
  ctx.fillRect(4, 8, 8, 1);
  
  // Legs
  ctx.fillStyle = CD;
  ctx.fillRect(7, 12, 2, 4);
  
  return c;
}

/* ── BOOKSHELF (80x20) horizontal with colored book spines ── */
export const SHELF_W = 80;
export const SHELF_H = 20;

export function preRenderBookshelf(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = SHELF_W;
  c.height = SHELF_H;
  const ctx = c.getContext("2d")!;
  
  const WO = "#8A6035"; // wood
  const WD = "#6A4A25"; // wood dark
  const BG = "#A87840"; // backing
  
  const bookColors = ["#A03030", "#4A7A4A", "#4A4A8A", "#C87040", "#8A4A8A", "#6A6A4A"];
  
  // Frame
  ctx.fillStyle = WD;
  ctx.fillRect(0, 0, SHELF_W, SHELF_H);
  ctx.fillStyle = BG;
  ctx.fillRect(2, 2, SHELF_W - 4, SHELF_H - 4);
  
  // Shelves
  ctx.fillStyle = WO;
  ctx.fillRect(0, 0, SHELF_W, 2);
  ctx.fillRect(0, SHELF_H - 2, SHELF_W, 2);
  
  // Books (top shelf)
  for (let i = 0; i < 15; i++) {
    const x = 4 + i * 5;
    ctx.fillStyle = bookColors[i % bookColors.length];
    ctx.fillRect(x, 3, 4, 6);
    ctx.fillStyle = WD;
    ctx.fillRect(x, 3, 1, 6);
  }
  
  // Books (bottom shelf)
  for (let i = 0; i < 15; i++) {
    const x = 4 + i * 5;
    ctx.fillStyle = bookColors[(i + 2) % bookColors.length];
    ctx.fillRect(x, 11, 4, 6);
    ctx.fillStyle = WD;
    ctx.fillRect(x, 11, 1, 6);
  }
  
  return c;
}

/* ── POTTED PLANT (12x20) ── */
export const PLANT_W = 12;
export const PLANT_H = 20;

export function preRenderPlant(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = PLANT_W;
  c.height = PLANT_H;
  const ctx = c.getContext("2d")!;
  
  const L1 = "#5A9A3A"; // leaf light
  const L2 = "#4A8A2A"; // leaf mid
  const L3 = "#3A7A1A"; // leaf dark
  const PT = "#D0C0A0"; // pot (cream/white like reference)
  const PD = "#B0A080"; // pot dark
  
  // Leaves (fuller, rounder bush shape)
  ctx.fillStyle = L3;
  ctx.fillRect(4, 0, 4, 2);
  ctx.fillStyle = L2;
  ctx.fillRect(2, 1, 8, 3);
  ctx.fillStyle = L1;
  ctx.fillRect(1, 3, 10, 4);
  ctx.fillStyle = L2;
  ctx.fillRect(2, 6, 8, 3);
  ctx.fillStyle = L3;
  ctx.fillRect(3, 8, 6, 2);
  // Leaf highlights
  ctx.fillStyle = "#6AAA4A";
  ctx.fillRect(3, 2, 2, 1);
  ctx.fillRect(6, 4, 2, 1);
  ctx.fillRect(4, 7, 2, 1);
  
  // Stem  
  ctx.fillStyle = "#5A4020";
  ctx.fillRect(5, 10, 2, 3);
  
  // Pot (cream/white like reference image)
  ctx.fillStyle = PT;
  ctx.fillRect(2, 13, 8, 6);
  ctx.fillStyle = PD;
  ctx.fillRect(2, 13, 8, 1); // rim
  ctx.fillStyle = "#C0B090";
  ctx.fillRect(3, 14, 6, 4); // lighter center
  // Pot base
  ctx.fillStyle = PD;
  ctx.fillRect(3, 18, 6, 1);
  
  // Small shadow under pot
  ctx.fillStyle = "rgba(0,0,0,0.15)";
  ctx.fillRect(2, 19, 8, 1);
  
  return c;
}

/* ── REFRIGERATOR (16x28) ── */
export const FRIDGE_W = 16;
export const FRIDGE_H = 28;

export function preRenderFridge(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = FRIDGE_W;
  c.height = FRIDGE_H;
  const ctx = c.getContext("2d")!;
  
  const FR = "#B8B8C0"; // fridge light
  const FD = "#989898"; // fridge dark
  const HN = "#686878"; // handle
  
  // Body
  ctx.fillStyle = FR;
  ctx.fillRect(0, 0, 16, 28);
  ctx.fillStyle = FD;
  ctx.fillRect(0, 0, 2, 28);
  ctx.fillRect(0, 0, 16, 2);
  
  // Door line
  ctx.fillStyle = FD;
  ctx.fillRect(0, 12, 16, 2);
  
  // Handles
  ctx.fillStyle = HN;
  ctx.fillRect(13, 5, 2, 4);
  ctx.fillStyle = HN;
  ctx.fillRect(13, 17, 2, 4);
  
  return c;
}

/* ── COUNTER/CABINET (64x16) ── */
export const COUNTER_W = 64;
export const COUNTER_H = 16;

export function preRenderCounter(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = COUNTER_W;
  c.height = COUNTER_H;
  const ctx = c.getContext("2d")!;
  
  const WO = "#8A6035";
  const WD = "#6A4A25";
  const HN = "#4A4A5A";
  
  // Countertop
  ctx.fillStyle = WO;
  ctx.fillRect(0, 0, 64, 4);
  ctx.fillStyle = WD;
  ctx.fillRect(0, 0, 64, 1);
  
  // Cabinet doors
  for (let i = 0; i < 4; i++) {
    const x = i * 16;
    ctx.fillStyle = WD;
    ctx.fillRect(x, 4, 16, 12);
    ctx.fillStyle = WO;
    ctx.fillRect(x + 1, 5, 14, 10);
    // Handle
    ctx.fillStyle = HN;
    ctx.fillRect(x + 12, 9, 2, 2);
  }
  
  return c;
}

/* ── COFFEE MACHINE (12x14) ── */
export const COFFEE_W = 12;
export const COFFEE_H = 14;

export function preRenderCoffee(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = COFFEE_W;
  c.height = COFFEE_H;
  const ctx = c.getContext("2d")!;
  
  const CM = "#4A4A5A";
  const CD = "#3A3A4A";
  const GL = "#8ABCE0";
  
  // Machine body
  ctx.fillStyle = CM;
  ctx.fillRect(0, 0, 12, 14);
  ctx.fillStyle = CD;
  ctx.fillRect(0, 0, 2, 14);
  
  // Glass pot
  ctx.fillStyle = GL;
  ctx.fillRect(3, 10, 6, 3);
  
  // Buttons
  ctx.fillStyle = "#A03030";
  ctx.fillRect(8, 2, 2, 2);
  ctx.fillStyle = "#4A8A4A";
  ctx.fillRect(8, 5, 2, 2);
  
  return c;
}

/* ── COUCH/SOFA (32x20) ── */
export const SOFA_W = 32;
export const SOFA_H = 20;

export function preRenderSofa(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = SOFA_W;
  c.height = SOFA_H;
  const ctx = c.getContext("2d")!;
  
  const SF = "#9A6878"; // sofa mauve
  const SD = "#7A4858"; // sofa dark
  
  // Backrest
  ctx.fillStyle = SF;
  ctx.fillRect(2, 0, 28, 10);
  ctx.fillStyle = SD;
  ctx.fillRect(2, 0, 28, 1); // top edge
  // Armrests (sides)
  ctx.fillStyle = SD;
  ctx.fillRect(0, 0, 3, 16);
  ctx.fillRect(29, 0, 3, 16);
  ctx.fillStyle = "#AA7888"; // armrest highlight
  ctx.fillRect(0, 1, 2, 14);
  ctx.fillRect(30, 1, 2, 14);
  
  // Seat cushions (2 segments)
  ctx.fillStyle = SF;
  ctx.fillRect(3, 9, 26, 7);
  ctx.fillStyle = "#AA7888"; // cushion highlight
  ctx.fillRect(4, 10, 11, 5);
  ctx.fillRect(17, 10, 11, 5);
  // Cushion divider
  ctx.fillStyle = SD;
  ctx.fillRect(15, 10, 2, 5);
  
  // Front edge
  ctx.fillStyle = SD;
  ctx.fillRect(2, 15, 28, 1);
  
  // Legs
  ctx.fillStyle = "#4A3020";
  ctx.fillRect(4, 16, 2, 4);
  ctx.fillRect(26, 16, 2, 4);
  
  return c;
}

/* ── MEETING TABLE (40x24) ── */
export const MTABLE_W = 40;
export const MTABLE_H = 24;

export function preRenderMeetingTable(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = MTABLE_W;
  c.height = MTABLE_H;
  const ctx = c.getContext("2d")!;
  
  const TL = "#7A5030";
  const TD = "#5C3A1E";
  
  // Table top
  ctx.fillStyle = TL;
  ctx.fillRect(0, 0, 40, 20);
  ctx.fillStyle = TD;
  ctx.fillRect(0, 0, 40, 2);
  ctx.fillRect(0, 18, 40, 2);
  ctx.fillRect(0, 0, 2, 20);
  ctx.fillRect(38, 0, 2, 20);
  
  // Legs
  ctx.fillStyle = TD;
  ctx.fillRect(5, 20, 3, 4);
  ctx.fillRect(32, 20, 3, 4);
  
  return c;
}

/* ── WASTEBASKET (8x10) ── */
export const TRASH_W = 8;
export const TRASH_H = 10;

export function preRenderTrash(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = TRASH_W;
  c.height = TRASH_H;
  const ctx = c.getContext("2d")!;
  
  const TR = "#4A4A5A";
  const TD = "#3A3A4A";
  
  ctx.fillStyle = TR;
  ctx.fillRect(1, 0, 6, 10);
  ctx.fillStyle = TD;
  ctx.fillRect(1, 0, 6, 1);
  ctx.fillRect(1, 0, 1, 10);
  
  return c;
}

/* ── FILING CABINET (12x20) ── */
export const CABINET_W = 12;
export const CABINET_H = 20;

export function preRenderCabinet(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = CABINET_W;
  c.height = CABINET_H;
  const ctx = c.getContext("2d")!;
  
  const CB = "#8A8A98";
  const CD = "#6A6A78";
  const HN = "#4A4A5A";
  
  // Body
  ctx.fillStyle = CB;
  ctx.fillRect(0, 0, 12, 20);
  ctx.fillStyle = CD;
  ctx.fillRect(0, 0, 2, 20);
  
  // Drawers
  for (let i = 0; i < 4; i++) {
    const y = i * 5;
    ctx.fillStyle = CD;
    ctx.fillRect(0, y, 12, 1);
    ctx.fillStyle = HN;
    ctx.fillRect(8, y + 2, 2, 1);
  }
  
  return c;
}

/* ── WATER COOLER (12x24) ── */
export const COOLER_W = 12;
export const COOLER_H = 24;

export function preRenderCooler(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = COOLER_W;
  c.height = COOLER_H;
  const ctx = c.getContext("2d")!;
  
  const BT = "#6AACDD"; // bottle
  const WA = "#A0D0FF"; // water
  const BS = "#B8B8C0"; // base
  
  // Bottle
  ctx.fillStyle = BT;
  ctx.fillRect(2, 0, 8, 12);
  ctx.fillStyle = WA;
  ctx.fillRect(3, 2, 6, 8);
  
  // Base
  ctx.fillStyle = BS;
  ctx.fillRect(0, 12, 12, 12);
  ctx.fillStyle = "#989898";
  ctx.fillRect(0, 12, 2, 12);
  
  // Taps
  ctx.fillStyle = "#A03030";
  ctx.fillRect(2, 16, 3, 2);
  ctx.fillStyle = "#4A7ACA";
  ctx.fillRect(7, 16, 3, 2);
  
  return c;
}

/* ── WALL CLOCK (8x8) ── */
export const CLOCK_W = 8;
export const CLOCK_H = 8;

export function preRenderClock(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = CLOCK_W;
  c.height = CLOCK_H;
  const ctx = c.getContext("2d")!;
  
  ctx.fillStyle = "#2A2A35";
  ctx.fillRect(0, 0, 8, 8);
  ctx.fillStyle = "#E8E8E0";
  ctx.fillRect(1, 1, 6, 6);
  ctx.fillStyle = "#2A2A35";
  ctx.fillRect(3, 2, 1, 2);
  ctx.fillRect(4, 3, 2, 1);
  
  return c;
}

/* ── FRAMED PAINTING (24x16) ── */
export const PAINTING_W = 24;
export const PAINTING_H = 16;

export function preRenderPainting(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = PAINTING_W;
  c.height = PAINTING_H;
  const ctx = c.getContext("2d")!;
  
  const FR = "#4A3A2A"; // frame
  const SK = "#6AACDD"; // sky
  const GR = "#5A8A3A"; // grass
  const MN = "#8A7A6A"; // mountain
  
  // Frame
  ctx.fillStyle = FR;
  ctx.fillRect(0, 0, 24, 16);
  
  // Sky
  ctx.fillStyle = SK;
  ctx.fillRect(2, 2, 20, 6);
  
  // Mountain
  ctx.fillStyle = MN;
  ctx.fillRect(6, 5, 12, 5);
  ctx.fillRect(8, 4, 8, 1);
  ctx.fillRect(10, 3, 4, 1);
  
  // Grass
  ctx.fillStyle = GR;
  ctx.fillRect(2, 8, 20, 6);
  
  return c;
}

/* ── WHITEBOARD (32x16) ── */
export const BOARD_W = 32;
export const BOARD_H = 16;

export function preRenderBoard(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = BOARD_W;
  c.height = BOARD_H;
  const ctx = c.getContext("2d")!;
  
  const FR = "#4A4A5A"; // frame
  const BG = "#F0F0E8"; // board
  const MK = "#4A7ACA"; // marker
  
  // Frame
  ctx.fillStyle = FR;
  ctx.fillRect(0, 0, 32, 16);
  
  // Board surface
  ctx.fillStyle = BG;
  ctx.fillRect(2, 2, 28, 12);
  
  // Some scribbles
  ctx.fillStyle = MK;
  ctx.fillRect(4, 4, 8, 1);
  ctx.fillRect(4, 7, 12, 1);
  ctx.fillRect(18, 5, 8, 1);
  
  return c;
}

/* ── WINDOW (32x20) with sky and curtains ── */
export const WIN_W = 32;
export const WIN_H = 20;

export function preRenderWindowFrame(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = WIN_W;
  c.height = WIN_H;
  const ctx = c.getContext("2d")!;
  
  // Window frame (dark brown wood)
  ctx.fillStyle = "#5C3A1E";
  ctx.fillRect(0, 0, 32, 20);
  
  // Sky through window (2 panes)
  ctx.fillStyle = "#6AACDD"; // daytime sky
  ctx.fillRect(2, 2, 13, 16);
  ctx.fillRect(17, 2, 13, 16);
  
  // Clouds
  ctx.fillStyle = "#A8D8F0";
  ctx.fillRect(4, 4, 6, 2);
  ctx.fillRect(20, 6, 5, 2);
  
  // Window divider
  ctx.fillStyle = "#5C3A1E";
  ctx.fillRect(15, 0, 2, 20);
  ctx.fillRect(0, 9, 32, 2); // horizontal bar
  
  // Frame highlight
  ctx.fillStyle = "#7A5030";
  ctx.fillRect(1, 1, 30, 1);
  
  // Windowsill
  ctx.fillStyle = "#7A5030";
  ctx.fillRect(0, 18, 32, 2);
  ctx.fillStyle = "#8A6040";
  ctx.fillRect(1, 18, 30, 1);
  
  return c;
}

/* ── Bubble drawing ── */
export function drawBubble(
  ctx: CanvasRenderingContext2D,
  x: number, y: number,
  text: string,
  type: "info" | "done" | "fail" = "info",
) {
  const t = text.length > 26 ? text.slice(0, 26) + ".." : text;
  ctx.save();
  ctx.font = "bold 6px monospace";
  const tw = ctx.measureText(t).width;
  const pw = tw + 10;
  const ph = 12;
  const bx = x - pw / 2;
  const by = y - ph;

  const colors = { info: "#FFFFFFE8", done: "#5A8A3AF0", fail: "#A03030F0" };
  ctx.fillStyle = colors[type] || colors.info;
  ctx.beginPath();
  ctx.roundRect(bx, by, pw, ph, 3);
  ctx.fill();

  ctx.fillStyle = type === "info" ? "#2A2020" : "#ffffff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(t, x, by + ph / 2 + 1);
  ctx.restore();
}
