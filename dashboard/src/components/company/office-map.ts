/* ── Office Layout - Warm RPG Maker Style ──
 * Game resolution: 640x360
 * Tile grid: 16x16 px
 * Layout: Main office (left), Kitchen (upper-right), CEO (lower-right), Meeting room
 */

export const GW = 640;
export const GH = 360;

export const CORRIDOR_Y = 148;
export const CORRIDOR_H = 40;

export interface RoomDef {
  id: string;
  label: string;
  x: number; y: number;
  w: number; h: number;
  doorX: number;
  accent: string;
  floor: string;
}

export const ROOMS: RoomDef[] = [
  // Main office (left, largest - ~60%)
  {
    id: "office",
    label: "Main Office",
    x: 16,
    y: 16,
    w: 368,
    h: 128,
    doorX: 200,
    accent: "#8A6035",
    floor: "#B8864E",
  },
  
  // Kitchen/Break Room (upper-right)
  {
    id: "kitchen",
    label: "Kitchen",
    x: 400,
    y: 16,
    w: 224,
    h: 128,
    doorX: 512,
    accent: "#A87840",
    floor: "#E8DCC8",
  },
  
  // CEO Office (lower-right)
  {
    id: "ceo",
    label: "CEO Office",
    x: 400,
    y: 196,
    w: 128,
    h: 156,
    doorX: 464,
    accent: "#C8A060",
    floor: "#3A5268",
  },
  
  // Meeting Room (accessible from corridor)
  {
    id: "meeting",
    label: "Meeting Room",
    x: 544,
    y: 196,
    w: 80,
    h: 156,
    doorX: 584,
    accent: "#6A7A7A",
    floor: "#808890",
  },
  
  // Departments in main office (virtual zones for worker assignment)
  {
    id: "finance",
    label: "Finance",
    x: 32,
    y: 32,
    w: 110,
    h: 100,
    doorX: 200,
    accent: "#2A3A5A",
    floor: "#B8864E",
  },
  
  {
    id: "security",
    label: "Security",
    x: 150,
    y: 32,
    w: 100,
    h: 100,
    doorX: 200,
    accent: "#A03030",
    floor: "#B8864E",
  },
  
  {
    id: "engineering",
    label: "Engineering",
    x: 258,
    y: 32,
    w: 110,
    h: 100,
    doorX: 200,
    accent: "#6A4A8A",
    floor: "#B8864E",
  },
  
  {
    id: "research",
    label: "Research",
    x: 32,
    y: 200,
    w: 110,
    h: 130,
    doorX: 200,
    accent: "#C87040",
    floor: "#B8864E",
  },
  
  {
    id: "operations",
    label: "Operations",
    x: 150,
    y: 200,
    w: 100,
    h: 130,
    doorX: 200,
    accent: "#6A7A7A",
    floor: "#B8864E",
  },
  
  {
    id: "sales",
    label: "Sales",
    x: 258,
    y: 200,
    w: 110,
    h: 130,
    doorX: 200,
    accent: "#4A8A4A",
    floor: "#B8864E",
  },
  
  {
    id: "marketing",
    label: "Marketing",
    x: 32,
    y: 250,
    w: 110,
    h: 80,
    doorX: 200,
    accent: "#C05A8A",
    floor: "#B8864E",
  },
];

export interface DeskPos {
  workerId: string;
  name: string;
  dept: string;
  deskX: number;
  deskY: number;
  charX: number;
  charY: number;
}

export const DESKS: DeskPos[] = [
  // CEO - centered in CEO room
  {
    workerId: "ceo",
    name: "JARVIS",
    dept: "ceo",
    deskX: 440,
    deskY: 280,
    charX: 464,
    charY: 240,
  },
  
  // Main office - 6 desks in 3 rows of 2
  // Row 1 (top)
  {
    workerId: "finance.market_analyst",
    name: "Market Analyst",
    dept: "finance",
    deskX: 48,
    deskY: 96,
    charX: 72,
    charY: 60,
  },
  {
    workerId: "finance.trader",
    name: "Trader",
    dept: "finance",
    deskX: 144,
    deskY: 96,
    charX: 168,
    charY: 60,
  },
  
  // Row 2 (middle)
  {
    workerId: "security.pen_tester",
    name: "Pen Tester",
    dept: "security",
    deskX: 240,
    deskY: 96,
    charX: 264,
    charY: 60,
  },
  {
    workerId: "engineering.developer",
    name: "Developer",
    dept: "engineering",
    deskX: 336,
    deskY: 96,
    charX: 360,
    charY: 60,
  },
  
  // Row 3 (bottom)
  {
    workerId: "research.product_researcher",
    name: "Product Researcher",
    dept: "research",
    deskX: 96,
    deskY: 270,
    charX: 120,
    charY: 234,
  },
  {
    workerId: "sales.account_exec",
    name: "Account Executive",
    dept: "sales",
    deskX: 288,
    deskY: 270,
    charX: 312,
    charY: 234,
  },
  
  // Additional workers
  {
    workerId: "finance.crypto_specialist",
    name: "Crypto Specialist",
    dept: "finance",
    deskX: 48,
    deskY: 180,
    charX: 72,
    charY: 144,
  },
  {
    workerId: "security.researcher",
    name: "Security Researcher",
    dept: "security",
    deskX: 192,
    deskY: 180,
    charX: 216,
    charY: 144,
  },
  {
    workerId: "engineering.devops",
    name: "DevOps",
    dept: "engineering",
    deskX: 336,
    deskY: 180,
    charX: 360,
    charY: 144,
  },
  {
    workerId: "research.data_analyst",
    name: "Data Analyst",
    dept: "research",
    deskX: 144,
    deskY: 270,
    charX: 168,
    charY: 234,
  },
  {
    workerId: "operations.office_manager",
    name: "Office Manager",
    dept: "operations",
    deskX: 192,
    deskY: 270,
    charX: 216,
    charY: 234,
  },
  {
    workerId: "sales.lead_gen",
    name: "Lead Generator",
    dept: "sales",
    deskX: 240,
    deskY: 180,
    charX: 264,
    charY: 144,
  },
  {
    workerId: "marketing.content_creator",
    name: "Content Creator",
    dept: "marketing",
    deskX: 96,
    deskY: 180,
    charX: 120,
    charY: 144,
  },
  {
    workerId: "marketing.social_manager",
    name: "Social Media Manager",
    dept: "marketing",
    deskX: 288,
    deskY: 180,
    charX: 312,
    charY: 144,
  },
];

/* ── Meeting Room Seats ── */
export interface MeetingSeat {
  x: number;
  y: number;
  facingLeft: boolean;
}

export const MEETING_TABLE_POS = { x: 562, y: 240 };

export const MEETING_SEATS: MeetingSeat[] = [
  { x: 554, y: 230, facingLeft: false },
  { x: 554, y: 250, facingLeft: false },
  { x: 554, y: 270, facingLeft: false },
  { x: 602, y: 230, facingLeft: true },
  { x: 602, y: 250, facingLeft: true },
  { x: 602, y: 270, facingLeft: true },
];

/* ── Furniture Positions ── */
export interface FurnitureDef {
  type: string;
  x: number;
  y: number;
}

export const FURNITURE: FurnitureDef[] = [
  // Main Office - windows on north wall (positioned on wall face, y = room.y - wallHeight + offset)
  { type: "window", x: 60, y: -10 },
  { type: "window", x: 160, y: -10 },
  { type: "window", x: 260, y: -10 },
  
  // Main Office - 2 bookshelves on north wall
  { type: "bookshelf", x: 40, y: 20 },
  { type: "bookshelf", x: 280, y: 20 },
  
  // Main Office - 5-6 potted plants scattered
  { type: "plant", x: 140, y: 40 },
  { type: "plant", x: 240, y: 40 },
  { type: "plant", x: 60, y: 120 },
  { type: "plant", x: 340, y: 120 },
  { type: "plant", x: 180, y: 210 },
  { type: "plant", x: 320, y: 240 },
  
  // Main Office - wastebaskets
  { type: "trash", x: 32, y: 130 },
  { type: "trash", x: 368, y: 130 },
  
  // Kitchen - refrigerator against right wall
  { type: "fridge", x: 600, y: 32 },
  
  // Kitchen - long counter along north wall
  { type: "counter", x: 416, y: 20 },
  { type: "counter", x: 480, y: 20 },
  
  // Kitchen - coffee machine on counter
  { type: "coffee", x: 448, y: 6 },
  
  // Kitchen - water cooler
  { type: "water_cooler", x: 560, y: 100 },
  
  // Kitchen - wall clock
  { type: "clock", x: 500, y: 20 },
  
  // CEO Office - executive desk with laptop/monitor
  { type: "desk", x: 440, y: 280 },
  
  // CEO Office - couch/sofa
  { type: "sofa", x: 420, y: 220 },
  
  // CEO Office - 2 bookshelves with books and plants
  { type: "bookshelf", x: 536, y: 200 },
  
  // CEO Office - framed landscape painting on wall
  { type: "painting", x: 460, y: 200 },
  
  // CEO Office - plants
  { type: "plant", x: 548, y: 310 },
  { type: "plant", x: 410, y: 250 },
  
  // Meeting Room - meeting table in center
  { type: "meeting_table", x: 562, y: 240 },
  
  // Meeting Room - whiteboard on north wall
  { type: "whiteboard", x: 556, y: 200 },
  
  // Meeting Room - chairs (rendered separately with MEETING_SEATS)
  { type: "chair", x: 554, y: 236 },
  { type: "chair", x: 554, y: 256 },
  { type: "chair", x: 554, y: 276 },
  { type: "chair", x: 596, y: 236 },
  { type: "chair", x: 596, y: 256 },
  { type: "chair", x: 596, y: 276 },
  
  // Filing cabinets in various departments
  { type: "filing_cabinet", x: 128, y: 48 },
  { type: "filing_cabinet", x: 260, y: 220 },
  
  // Corridor decorations - vending machine, plants, bench
  { type: "water_cooler", x: 200, y: 150 },
  { type: "plant", x: 100, y: 148 },
  { type: "plant", x: 300, y: 148 },
  { type: "plant", x: 500, y: 148 },
  { type: "trash", x: 150, y: 166 },
  
  // More props in main office for life
  { type: "filing_cabinet", x: 360, y: 48 },
  { type: "plant", x: 20, y: 30 },
  { type: "plant", x: 370, y: 30 },
  
  // Kitchen extras
  { type: "plant", x: 580, y: 30 },
  { type: "trash", x: 420, y: 120 },
];

/* ── Coffee Position (in kitchen) ── */
export const COFFEE_POS = { x: 448, y: 6 };

/* ── Plant Positions (comprehensive list for animation) ── */
export const PLANT_POSITIONS = [
  { x: 140, y: 40 },
  { x: 240, y: 40 },
  { x: 60, y: 120 },
  { x: 340, y: 120 },
  { x: 180, y: 210 },
  { x: 320, y: 240 },
  { x: 548, y: 310 },
  { x: 410, y: 250 },
];

/* ── Water Cooler Position ── */
export const WATER_COOLER_POS = { x: 560, y: 100 };

/* ── Whiteboard Positions ── */
export const BOARD_POSITIONS = [
  { x: 556, y: 200, room: "meeting" },
];

export const WINDOW_POS = { x: 450, y: 200 };

/* ── Desk Lamps (one per desk - not used in this style) ── */
export const DESK_LAMPS = DESKS.map(d => ({
  x: d.deskX + 4,
  y: d.deskY - 8,
}));
