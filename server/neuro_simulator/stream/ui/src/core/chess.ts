/** Chess 元素组件：用 chessground 显示后端广播的在线棋局。
 *
 * 状态权威仍然在 stream（Lichess Bot API + esca 本地镜像），这里只是渲染器。
 * - 首次拿到状态（含中途连接 snapshot）瞬时画好，不补移动动画；
 * - 后续每次 element_state 全量快照 diff 交给 chessground 动画；
 * - 画面是 viewOnly：直播画面不承担操作，下棋入口仍是 Neuro 的 `game_chess_move`。
 */

import { Chessground } from "@lichess-org/chessground";
import "@lichess-org/chessground/assets/chessground.base.css";
import "@lichess-org/chessground/assets/chessground.cburnett.css";

import type { ElementView } from "./components";

type Ground = ReturnType<typeof Chessground>;

interface ChessUiState {
  game: string;
  status: "idle" | "playing" | "ended" | string;
  fen?: string;
  turn?: string;
  orientation?: string;
  my_color?: string;
  in_check?: boolean;
  last_move?: { from: string; to: string } | null;
  result?: string;
  winner?: string | null;
  error?: string | null;
  clock?: { white?: string; black?: string };
  players?: { white?: string; black?: string };
}

export class ChessBoardView implements ElementView {
  private panel: HTMLElement;
  private boardArea: HTMLElement;
  private board: HTMLElement;
  private statusText: HTMLElement;
  private ground: Ground | null = null;
  private prev: ChessUiState | null = null;
  private observer: ResizeObserver | null = null;

  constructor(el: HTMLElement) {
    el.classList.add("chess");

    this.panel = document.createElement("div");
    this.panel.className = "chess-panel";

    this.boardArea = document.createElement("div");
    this.boardArea.className = "chess-board-area";

    this.board = document.createElement("div");
    this.board.className = "chess-ground";

    this.statusText = document.createElement("div");
    this.statusText.className = "chess-status";

    this.boardArea.append(this.board);
    this.panel.append(this.boardArea, this.statusText);
    el.append(this.panel);

    if (typeof ResizeObserver !== "undefined") {
      this.observer = new ResizeObserver(() => this.fitBoard());
      this.observer.observe(this.boardArea);
    }
    this.fitBoard();
  }

  update(payload: unknown): void {
    const s = payload as ChessUiState | null;
    if (!s || s.game !== "chess" || s.status === "idle" || !s.fen) {
      this.prev = null;
      this.ground?.destroy();
      this.ground = null;
      this.statusText.textContent = "";
      return;
    }

    const orientation = (s.orientation || s.my_color || "white") as "white" | "black";
    const turnColor = (s.turn || orientation) as "white" | "black";
    const firstRender = this.ground === null;
    const instant = this.prev === null;
    const config = {
      fen: s.fen.split(" ")[0],
      orientation,
      turnColor,
      viewOnly: true,
      coordinates: false,
      draggable: { enabled: false },
      selectable: { enabled: false },
      movable: { free: false },
      highlight: { lastMove: true, check: true },
      animation: { enabled: !instant, duration: 250 },
      check: Boolean(s.in_check) ? turnColor : false,
      lastMove:
        s.last_move && s.status === "playing" ? [s.last_move.from, s.last_move.to] : undefined,
    } as unknown as Parameters<Ground["set"]>[0];

    if (!this.ground) {
      this.ground = Chessground(this.board, config);
    } else if (firstRender) {
      this.ground.set({ ...config, animation: { enabled: false } });
    } else {
      this.ground.set(config);
    }

    this.statusText.textContent = this.statusLine(s);
    this.prev = s;
  }

  dispose(): void {
    this.observer?.disconnect();
    this.observer = null;
    this.ground?.destroy();
    this.ground = null;
    this.prev = null;
  }

  private fitBoard(): void {
    const rect = this.boardArea.getBoundingClientRect();
    const size = Math.max(1, Math.floor(Math.min(rect.width, rect.height)));
    const px = `${size}px`;
    const changed = this.board.style.width !== px || this.board.style.height !== px;
    this.board.style.width = px;
    this.board.style.height = px;
    if (changed) this.ground?.redrawAll();
  }

  private statusLine(s: ChessUiState): string {
    if (s.error) return s.error;
    if (s.status !== "playing") {
      const winner = s.winner || "draw";
      return `Game over: ${winner}${s.result ? ` (${s.result})` : ""}`;
    }
    const white = s.players?.white || "White";
    const black = s.players?.black || "Black";
    const clock = s.clock || {};
    return `${white} ${clock.white || "--:--"} · ${black} ${clock.black || "--:--"}`;
  }
}
