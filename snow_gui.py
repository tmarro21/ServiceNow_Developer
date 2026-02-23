#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ServiceNow AI Agent — GUI Interface

A dark-themed tkinter chat window with:
  • Multi-line prompt area (Ctrl+Enter to send)
  • File & screenshot attachments (images displayed inline, text/code included as context)
  • Live streaming of agent text and tool calls
  • Conversation history preserved across turns

Run with:
    python snow_gui.py
"""

import os
import base64
import mimetypes
import threading
import json
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from typing import List, Dict, Optional

import anthropic
from dotenv import load_dotenv

from snow_client import ServiceNowClient
from snow_agent import SYSTEM_PROMPT
from tools import TOOL_DEFINITIONS, execute_tool

load_dotenv()

# ── Optional PIL for image thumbnails ────────────────────────────────────────
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ── File type sets ────────────────────────────────────────────────────────────
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
TEXT_EXTS = {
    '.txt', '.py', '.js', '.ts', '.jsx', '.tsx', '.json', '.xml',
    '.yaml', '.yml', '.csv', '.md', '.html', '.css', '.sql', '.sh',
    '.bat', '.log', '.env', '.ini', '.cfg', '.toml', '.java', '.cs',
    '.cpp', '.c', '.h', '.go', '.rs', '.rb', '.php',
}

# ── Catppuccin Mocha palette ──────────────────────────────────────────────────
BG       = '#1e1e2e'
MANTLE   = '#181825'
CRUST    = '#11111b'
SURFACE0 = '#313244'
SURFACE1 = '#45475a'
OVERLAY0 = '#6c7086'
TEXT     = '#cdd6f4'
SUBTEXT  = '#a6adc8'
BLUE     = '#89b4fa'
GREEN    = '#a6e3a1'
YELLOW   = '#f9e2af'
RED      = '#f38ba8'
MAUVE    = '#cba6f7'
TEAL     = '#94e2d5'


# ─────────────────────────────────────────────────────────────────────────────
# Attachment
# ─────────────────────────────────────────────────────────────────────────────

class Attachment:
    """A file (image or text/code) that will be sent to Claude."""

    def __init__(self, path: str):
        self.path = path
        self.name = Path(path).name
        self.ext  = Path(path).suffix.lower()
        self.is_image = self.ext in IMAGE_EXTS

    def to_content_block(self) -> dict:
        """Return an Anthropic API content block for this attachment."""
        if self.is_image:
            with open(self.path, 'rb') as f:
                data = base64.standard_b64encode(f.read()).decode('utf-8')
            mime = mimetypes.guess_type(self.path)[0] or 'image/png'
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            }
        else:
            try:
                with open(self.path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                if len(content) > 120_000:
                    content = content[:120_000] + "\n…[truncated at 120 KB]"
            except Exception as exc:
                content = f"[Could not read file: {exc}]"
            return {
                "type": "text",
                "text": f'<file name="{self.name}">\n{content}\n</file>',
            }


# ─────────────────────────────────────────────────────────────────────────────
# Background agent worker
# ─────────────────────────────────────────────────────────────────────────────

class AgentWorker(threading.Thread):
    """Runs one conversation turn (with tool-use loop) in a daemon thread."""

    def __init__(
        self,
        snow_client: ServiceNowClient,
        content,           # str or list[dict] (multimodal)
        history: list,
        on_text,
        on_tool,
        on_done,
        on_error,
    ):
        super().__init__(daemon=True)
        self.snow_client = snow_client
        self.content     = content
        self.history     = history
        self.on_text     = on_text
        self.on_tool     = on_tool
        self.on_done     = on_done
        self.on_error    = on_error

    def run(self):
        try:
            api = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            self.history.append({"role": "user", "content": self.content})

            for _ in range(50):
                resp = api.messages.create(
                    model="claude-opus-4-6",
                    max_tokens=8192,
                    system=SYSTEM_PROMPT,
                    tools=TOOL_DEFINITIONS,
                    messages=self.history,
                )
                self.history.append({"role": "assistant", "content": resp.content})

                for block in resp.content:
                    if hasattr(block, 'text') and block.text:
                        self.on_text(block.text)

                if resp.stop_reason == "end_turn":
                    break

                if resp.stop_reason == "tool_use":
                    results = []
                    for block in resp.content:
                        if block.type != "tool_use":
                            continue
                        self.on_tool(block.name, block.input)
                        result = execute_tool(block.name, block.input, self.snow_client)
                        results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                    self.history.append({"role": "user", "content": results})
                else:
                    break

            self.on_done()
        except Exception as exc:
            import traceback
            self.on_error(f"{exc}\n{traceback.format_exc()}")


# ─────────────────────────────────────────────────────────────────────────────
# Main GUI application
# ─────────────────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ServiceNow AI Agent")
        self.root.geometry("1060x740")
        self.root.configure(bg=BG)
        self.root.minsize(720, 520)

        self.snow_client: Optional[ServiceNowClient] = None
        self.history:     List[Dict] = []
        self.attachments: List[Attachment] = []
        self.busy  = False
        self._imgs: List = []  # keep PIL PhotoImage refs alive

        self._build()
        threading.Thread(target=self._connect, daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build(self):
        # ── Status bar ────────────────────────────────────────────────────────
        bar = tk.Frame(self.root, bg=MANTLE, height=36)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        tk.Label(
            bar, text="  ServiceNow AI Agent",
            bg=MANTLE, fg=TEXT, font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            bar, text="Clear chat",
            bg=SURFACE0, fg=SUBTEXT, relief='flat',
            padx=10, pady=2, font=("Segoe UI", 9), cursor='hand2',
            command=self._clear,
        ).pack(side=tk.RIGHT, padx=10, pady=5)

        self._dot = tk.Label(bar, text="●", bg=MANTLE, fg=OVERLAY0, font=("Segoe UI", 14))
        self._dot.pack(side=tk.RIGHT, padx=2)

        self._status_lbl = tk.Label(
            bar, text="Connecting…", bg=MANTLE, fg=OVERLAY0, font=("Segoe UI", 9),
        )
        self._status_lbl.pack(side=tk.RIGHT, padx=4)

        # ── Chat display ──────────────────────────────────────────────────────
        chat_frame = tk.Frame(self.root, bg=MANTLE)
        chat_frame.pack(fill=tk.BOTH, expand=True)

        self.chat = tk.Text(
            chat_frame,
            bg=MANTLE, fg=TEXT, relief='flat', bd=0,
            font=("Segoe UI", 10), wrap=tk.WORD,
            state=tk.DISABLED, cursor='arrow',
            padx=18, pady=14, spacing3=3,
            selectbackground=SURFACE1,
        )
        vsb = tk.Scrollbar(
            chat_frame, orient=tk.VERTICAL, command=self.chat.yview,
            bg=SURFACE0, troughcolor=MANTLE, activebackground=SURFACE1, width=10,
        )
        self.chat.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat.pack(fill=tk.BOTH, expand=True)

        # Text tags
        self.chat.tag_configure('you_lbl',  foreground=GREEN,   font=("Segoe UI", 10, "bold"))
        self.chat.tag_configure('you_txt',  foreground=TEXT,    font=("Segoe UI", 10))
        self.chat.tag_configure('ai_lbl',   foreground=BLUE,    font=("Segoe UI", 10, "bold"))
        self.chat.tag_configure('ai_txt',   foreground=TEXT,    font=("Segoe UI", 10))
        self.chat.tag_configure('tool_ln',  foreground=YELLOW,  font=("Segoe UI", 9))
        self.chat.tag_configure('system',   foreground=MAUVE,   font=("Segoe UI", 9, "italic"))
        self.chat.tag_configure('err',      foreground=RED,     font=("Segoe UI", 9))
        self.chat.tag_configure('file_lbl', foreground=TEAL,    font=("Segoe UI", 9))
        self.chat.tag_configure('sep',      foreground=SURFACE1)

        # ── Bottom panel (attach strip + input) ───────────────────────────────
        self._bot = tk.Frame(self.root, bg=SURFACE0)
        self._bot.pack(fill=tk.X, side=tk.BOTTOM)
        self._bot.grid_columnconfigure(0, weight=1)

        # Attachment strip (row 0 — hidden until files are added)
        self._strip = tk.Frame(self._bot, bg=CRUST)
        self._strip.grid(row=0, column=0, sticky='ew')
        self._strip.grid_remove()  # hidden initially

        self._chips = tk.Frame(self._strip, bg=CRUST)
        self._chips.pack(fill=tk.X, padx=8, pady=6)

        # Input section (row 1)
        inp_sec = tk.Frame(self._bot, bg=SURFACE0)
        inp_sec.grid(row=1, column=0, sticky='ew')

        self.input_box = tk.Text(
            inp_sec,
            bg=SURFACE0, fg=OVERLAY0, relief='flat', bd=0,
            font=("Segoe UI", 10), wrap=tk.WORD,
            insertbackground=TEXT, padx=14, pady=10, height=4,
            selectbackground=SURFACE1,
        )
        self.input_box.pack(fill=tk.BOTH, expand=True, padx=6, pady=(8, 0))
        self.input_box.bind("<Control-Return>",    lambda e: (self._send(), "break")[1])
        self.input_box.bind("<Control-KP_Enter>",  lambda e: (self._send(), "break")[1])
        self.input_box.bind("<FocusIn>",  self._ph_clear)
        self.input_box.bind("<FocusOut>", self._ph_restore)
        self.input_box.focus_set()

        # Placeholder
        self._ph_active = True
        self._ph_text   = "Describe what you want to configure in ServiceNow…  (Ctrl+Enter to send)"
        self.input_box.insert('1.0', self._ph_text)

        # Button row
        btn_row = tk.Frame(inp_sec, bg=SURFACE0)
        btn_row.pack(fill=tk.X, padx=8, pady=6)

        self._attach_btn = tk.Button(
            btn_row, text="📎  Attach file",
            bg=SURFACE1, fg=TEXT, relief='flat',
            padx=12, pady=5, font=("Segoe UI", 9), cursor='hand2',
            command=self._browse,
        )
        self._attach_btn.pack(side=tk.LEFT)

        tk.Label(
            btn_row, text="Ctrl+Enter to send",
            bg=SURFACE0, fg=OVERLAY0, font=("Segoe UI", 8),
        ).pack(side=tk.LEFT, padx=12)

        self._send_btn = tk.Button(
            btn_row, text="Send  ▶",
            bg=BLUE, fg=CRUST, relief='flat',
            padx=16, pady=5, font=("Segoe UI", 9, "bold"), cursor='hand2',
            command=self._send,
        )
        self._send_btn.pack(side=tk.RIGHT)

    # ─────────────────────────────────────────────────────────────────────────
    # Placeholder helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _ph_clear(self, _=None):
        if self._ph_active:
            self.input_box.delete('1.0', tk.END)
            self.input_box.configure(fg=TEXT)
            self._ph_active = False

    def _ph_restore(self, _=None):
        if not self.input_box.get('1.0', tk.END).strip():
            self.input_box.insert('1.0', self._ph_text)
            self.input_box.configure(fg=OVERLAY0)
            self._ph_active = True

    # ─────────────────────────────────────────────────────────────────────────
    # ServiceNow connection
    # ─────────────────────────────────────────────────────────────────────────

    def _connect(self):
        missing = [v for v in ("SNOW_INSTANCE", "SNOW_USERNAME", "SNOW_PASSWORD", "ANTHROPIC_API_KEY")
                   if not os.environ.get(v)]
        if missing:
            self.root.after(0, lambda: self._set_status(f"Missing env vars: {', '.join(missing)}", 'error'))
            return

        instance = os.environ["SNOW_INSTANCE"]
        username = os.environ["SNOW_USERNAME"]
        try:
            c = ServiceNowClient(instance, username, os.environ["SNOW_PASSWORD"])
            t = c.test_connection()
            if not t.get("success"):
                raise RuntimeError(t.get("error", "Unknown error"))
            self.snow_client = c
            data = t.get("data", [])
            name = (data[0].get("name") or username) if isinstance(data, list) and data else username
            self.root.after(0, lambda: self._set_status(f"{name}  @{instance}", 'ok'))
            self.root.after(0, lambda: self._sys(
                f"Connected to {instance}.service-now.com as {name}. Ready to configure."
            ))
        except Exception as exc:
            self.root.after(0, lambda: self._set_status(f"Connection failed: {exc}", 'error'))
            self.root.after(0, lambda: self._err(f"Connection failed: {exc}"))

    def _set_status(self, text: str, kind: str = ''):
        self._status_lbl.configure(text=text)
        dot = {'ok': GREEN, 'error': RED, 'busy': YELLOW}.get(kind, OVERLAY0)
        self._dot.configure(fg=dot)
        lbl = {'ok': SUBTEXT, 'error': RED}.get(kind, OVERLAY0)
        self._status_lbl.configure(fg=lbl)

    # ─────────────────────────────────────────────────────────────────────────
    # Chat rendering helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _w(self, text: str, tag: str):
        self.chat.configure(state=tk.NORMAL)
        self.chat.insert(tk.END, text, tag)
        self.chat.configure(state=tk.DISABLED)
        self.chat.see(tk.END)

    def _sys(self, t: str):
        self._w(f"\n  {t}\n", 'system')

    def _err(self, t: str):
        self._w(f"\n  ⚠ {t}\n", 'err')

    def _sep(self):
        self._w("\n" + "─" * 80 + "\n", 'sep')

    def _user_header(self, text: str):
        self._w("\nYou\n", 'you_lbl')
        if text:
            self._w(f"{text}\n", 'you_txt')

    def _agent_header(self):
        self._w("\nAgent\n", 'ai_lbl')

    def _agent_text(self, text: str):
        self._w(text, 'ai_txt')

    def _tool_line(self, name: str, inp: dict):
        s = json.dumps(inp, default=str)
        short = s[:260] + "…" if len(s) > 260 else s
        self._w(f"\n  ↳ {name}({short})\n", 'tool_ln')

    def _inline_image(self, path: str):
        if not HAS_PIL:
            self._w(f"  🖼 {Path(path).name}\n", 'file_lbl')
            return
        try:
            img = Image.open(path)
            img.thumbnail((340, 220), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._imgs.append(photo)
            self.chat.configure(state=tk.NORMAL)
            self.chat.insert(tk.END, "\n")
            self.chat.image_create(tk.END, image=photo, padx=18, pady=4)
            self.chat.insert(tk.END, "\n")
            self.chat.configure(state=tk.DISABLED)
            self.chat.see(tk.END)
        except Exception:
            self._w(f"  🖼 {Path(path).name}\n", 'file_lbl')

    # ─────────────────────────────────────────────────────────────────────────
    # Attachment management
    # ─────────────────────────────────────────────────────────────────────────

    def _browse(self):
        exts_img  = " ".join(f"*{e}" for e in sorted(IMAGE_EXTS))
        exts_text = " ".join(f"*{e}" for e in sorted(TEXT_EXTS))
        paths = filedialog.askopenfilenames(
            title="Attach files or screenshots",
            filetypes=[
                ("Images & code", f"{exts_img} {exts_text}"),
                ("Images / screenshots", exts_img),
                ("Code / text files",    exts_text),
                ("All files", "*.*"),
            ],
        )
        for p in paths:
            self.attachments.append(Attachment(p))
        self._rebuild_chips()

    def _rebuild_chips(self):
        for w in self._chips.winfo_children():
            w.destroy()

        if not self.attachments:
            self._strip.grid_remove()
            return

        self._strip.grid()

        for i, att in enumerate(self.attachments):
            icon = "🖼" if att.is_image else "📄"
            chip = tk.Frame(self._chips, bg=SURFACE0, padx=4, pady=2)
            chip.pack(side=tk.LEFT, padx=3)
            tk.Label(
                chip, text=f"{icon} {att.name}",
                bg=SURFACE0, fg=TEXT, font=("Segoe UI", 8),
            ).pack(side=tk.LEFT)
            tk.Button(
                chip, text=" ×", bg=SURFACE0, fg=OVERLAY0,
                relief='flat', font=("Segoe UI", 9), cursor='hand2',
                command=lambda i=i: self._rm_attachment(i),
            ).pack(side=tk.LEFT)

    def _rm_attachment(self, idx: int):
        if 0 <= idx < len(self.attachments):
            self.attachments.pop(idx)
        self._rebuild_chips()

    # ─────────────────────────────────────────────────────────────────────────
    # Send / agent loop
    # ─────────────────────────────────────────────────────────────────────────

    def _send(self):
        if self.busy:
            return
        if not self.snow_client:
            self._err("Not connected yet — please wait.")
            return

        text = "" if self._ph_active else self.input_box.get('1.0', tk.END).strip()
        if not text and not self.attachments:
            return

        # Build multimodal content for Claude
        if self.attachments:
            blocks = [a.to_content_block() for a in self.attachments]
            if text:
                blocks.append({"type": "text", "text": text})
            content = blocks
        else:
            content = text

        # Render user turn in chat
        self._user_header(text)
        for att in self.attachments:
            if att.is_image:
                self._inline_image(att.path)
            else:
                self._w(f"  📄 {att.name}\n", 'file_lbl')

        # Clear input & attachments
        self.input_box.delete('1.0', tk.END)
        self._ph_active = False
        self.attachments.clear()
        self._rebuild_chips()

        # Kick off agent worker
        self._set_busy(True)
        self._agent_header()

        AgentWorker(
            snow_client=self.snow_client,
            content=content,
            history=self.history,
            on_text=lambda t: self.root.after(0, lambda t=t: self._agent_text(t)),
            on_tool=lambda n, i: self.root.after(0, lambda n=n, i=i: self._tool_line(n, i)),
            on_done=lambda: self.root.after(0, self._agent_done),
            on_error=lambda e: self.root.after(0, lambda e=e: self._agent_error(e)),
        ).start()

    def _agent_done(self):
        self._sep()
        self._set_busy(False)

    def _agent_error(self, msg: str):
        self._err(f"Agent error: {msg}")
        self._sep()
        self._set_busy(False)

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self._send_btn.configure(state=state)
        self._attach_btn.configure(state=state)
        self.input_box.configure(state=state)
        if busy:
            self._send_btn.configure(text="Working…", bg=SURFACE1, fg=OVERLAY0)
        else:
            self._send_btn.configure(text="Send  ▶", bg=BLUE, fg=CRUST)

    # ─────────────────────────────────────────────────────────────────────────
    # Misc
    # ─────────────────────────────────────────────────────────────────────────

    def _clear(self):
        self.history.clear()
        self.chat.configure(state=tk.NORMAL)
        self.chat.delete('1.0', tk.END)
        self.chat.configure(state=tk.DISABLED)
        self._sys("Conversation cleared.")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    load_dotenv()
    root = tk.Tk()

    # Sharper rendering on high-DPI Windows displays
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
