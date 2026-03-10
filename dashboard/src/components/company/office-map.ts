// Isometric grid constants
export const TILE_W = 64;
export const TILE_H = 32;

// Convert grid coords to isometric screen coords
export function toIso(col: number, row: number): { x: number; y: number } {
  return {
    x: (col - row) * (TILE_W / 2) + 520,
    y: (col + row) * (TILE_H / 2) + 60,
  };
}

// Room definitions
export interface Room {
  id: string;
  label: string;
  col: number;
  row: number;
  w: number;
  h: number;
}

export const ROOMS: Room[] = [
  { id: "ceo", label: "CEO", col: 6, row: 1, w: 4, h: 2 },
  { id: "finance", label: "Finance", col: 1, row: 4, w: 3, h: 3 },
  { id: "security", label: "Security", col: 4, row: 4, w: 3, h: 3 },
  { id: "engineering", label: "Engineering", col: 7, row: 4, w: 3, h: 3 },
  { id: "research", label: "Research", col: 10, row: 4, w: 3, h: 3 },
  { id: "operations", label: "Operations", col: 13, row: 4, w: 2, h: 3 },
];

// Worker positions on the isometric grid
export interface WorkerPos {
  workerId: string;
  name: string;
  col: number;
  row: number;
  room: string;
}

export const WORKER_POSITIONS: WorkerPos[] = [
  // CEO
  { workerId: "ceo", name: "JARVIS", col: 8, row: 2, room: "ceo" },
  // Finance
  { workerId: "finance.market_analyst", name: "Analyst", col: 1, row: 5, room: "finance" },
  { workerId: "finance.trader", name: "Trader", col: 2, row: 5, room: "finance" },
  { workerId: "finance.crypto_specialist", name: "Crypto", col: 3, row: 5, room: "finance" },
  // Security
  { workerId: "security.pen_tester", name: "PenTest", col: 4, row: 5, room: "security" },
  { workerId: "security.researcher", name: "Intel", col: 5, row: 5, room: "security" },
  // Engineering
  { workerId: "engineering.developer", name: "Dev", col: 7, row: 5, room: "engineering" },
  { workerId: "engineering.devops", name: "DevOps", col: 8, row: 5, room: "engineering" },
  // Research
  { workerId: "research.product_researcher", name: "PM", col: 10, row: 5, room: "research" },
  { workerId: "research.data_analyst", name: "Data", col: 11, row: 5, room: "research" },
  // Operations
  { workerId: "operations.office_manager", name: "Admin", col: 13, row: 5, room: "operations" },
];
