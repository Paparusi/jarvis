import { Graphics, Container, Text, TextStyle } from "pixi.js";

// Department colors
const DEPT_COLORS: Record<string, number> = {
  ceo: 0xffd700,
  finance: 0x1e90ff,
  security: 0xff4444,
  engineering: 0x9b59b6,
  research: 0xff8c00,
  operations: 0x888888,
};

export function createCharacter(department: string, name: string): Container {
  const container = new Container();
  const color = DEPT_COLORS[department] || 0x666666;

  const body = new Graphics();

  // Shadow
  body.ellipse(0, 2, 8, 4);
  body.fill({ color: 0x000000, alpha: 0.3 });

  // Body
  body.roundRect(-6, -18, 12, 14, 2);
  body.fill(color);

  // Head
  body.circle(0, -24, 6);
  body.fill(0xffcc99);

  // Eyes
  body.circle(-2, -25, 1);
  body.fill(0x333333);
  body.circle(2, -25, 1);
  body.fill(0x333333);

  container.addChild(body);

  // Name label below
  const label = new Text({
    text: name,
    style: new TextStyle({
      fontSize: 9,
      fill: 0xaaaaaa,
      fontFamily: "monospace",
    }),
  });
  label.anchor.set(0.5, 0);
  label.y = 6;
  container.addChild(label);

  return container;
}

export function createDesk(): Container {
  const container = new Container();
  const desk = new Graphics();

  // Desk top (isometric diamond)
  desk.moveTo(0, 0);
  desk.lineTo(16, -8);
  desk.lineTo(32, 0);
  desk.lineTo(16, 8);
  desk.closePath();
  desk.fill(0x8b6914);

  // Desk front
  desk.moveTo(0, 0);
  desk.lineTo(16, 8);
  desk.lineTo(16, 14);
  desk.lineTo(0, 6);
  desk.closePath();
  desk.fill(0x6b4f10);

  // Monitor
  desk.rect(10, -16, 12, 10);
  desk.fill(0x222244);
  desk.rect(11, -15, 10, 8);
  desk.fill(0x334488);

  container.addChild(desk);
  return container;
}

export function createStatusIndicator(status: string): Container {
  const container = new Container();
  const g = new Graphics();

  const colors: Record<string, number> = {
    working: 0x00ccff,
    done: 0x00ff88,
    fail: 0xff4444,
  };

  const color = colors[status] || 0x666666;

  // Pulsing circle above character head
  g.circle(0, -36, 5);
  g.fill({ color, alpha: 0.8 });
  g.circle(0, -36, 3);
  g.fill({ color: 0xffffff, alpha: 0.4 });

  container.addChild(g);

  // Icon text
  const icons: Record<string, string> = {
    working: "\u26A1",
    done: "\u2713",
    fail: "\u2717",
  };
  if (icons[status]) {
    const text = new Text({
      text: icons[status],
      style: new TextStyle({ fontSize: 8, fill: 0xffffff }),
    });
    text.anchor.set(0.5, 0.5);
    text.y = -36;
    container.addChild(text);
  }

  return container;
}

export function createSpeechBubble(message: string): Container {
  const container = new Container();
  const truncated = message.length > 35 ? message.slice(0, 35) + "\u2026" : message;

  const bg = new Graphics();

  // Bubble body
  bg.roundRect(-65, -52, 130, 22, 8);
  bg.fill({ color: 0xffffff, alpha: 0.92 });

  // Tail
  bg.moveTo(-3, -30);
  bg.lineTo(3, -30);
  bg.lineTo(0, -24);
  bg.closePath();
  bg.fill({ color: 0xffffff, alpha: 0.92 });

  container.addChild(bg);

  const text = new Text({
    text: truncated,
    style: new TextStyle({
      fontSize: 9,
      fill: 0x111111,
      fontFamily: "monospace",
    }),
  });
  text.anchor.set(0.5, 0.5);
  text.y = -41;
  container.addChild(text);

  return container;
}

export function createRoomLabel(label: string): Text {
  return new Text({
    text: label,
    style: new TextStyle({
      fontSize: 11,
      fill: 0x445566,
      fontFamily: "monospace",
      fontWeight: "bold",
    }),
  });
}
