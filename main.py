import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

import customtkinter as ctk
from tkinter import messagebox

import credstore
import psn
from bridge_util import normalize_npsso

APP_NAME = "PS5-to-Steam-Bridge"

logger = logging.getLogger("Bridge")
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG = "#0b0c10"
CARD = "#14161c"
INSET = "#1b1e26"
LINE = "#2c303a"
TEXT = "#f2f3f6"
MUTED = "#8d93a5"
ACCENT = "#6b8cff"
ACCENT_HOVER = "#8aa3ff"
OK = "#3dcf8e"
WARN = "#e6b84d"
BAD = "#ff6a78"
DOT = {"ok": OK, "wait": WARN, "err": BAD, "off": "#5c6270"}
FONT = "Segoe UI"


def resource_path(relative_path):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def runtime_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def app_data_dir():
    if os.name == "nt":
        path = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
    else:
        path = os.path.join(
            os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"),
            APP_NAME,
        )
        legacy = os.path.join(os.path.expanduser("~"), APP_NAME)
        if os.path.isdir(legacy) and not os.path.isdir(path):
            shutil.move(legacy, path)
            return path
    os.makedirs(path, exist_ok=True)
    return path


def migrate_legacy_data(dest_dir):
    src_dir = runtime_dir()
    src_cfg = os.path.join(src_dir, "config.json")
    dst_cfg = os.path.join(dest_dir, "config.json")
    if os.path.exists(src_cfg) and not os.path.exists(dst_cfg):
        shutil.copy2(src_cfg, dst_cfg)
    src_session = os.path.join(src_dir, "steam_session")
    dst_session = os.path.join(dest_dir, "steam_session")
    if os.path.isdir(src_session) and not os.path.isdir(dst_session):
        shutil.copytree(src_session, dst_session)


def setup_logging(log_dir):
    log_path = os.path.join(log_dir, "bridge.log")
    handlers = [logging.FileHandler(log_path, encoding="utf-8")]
    if sys.stderr:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )


class SteamWorker:
    def __init__(self, session_dir):
        self.cmd_q = queue.Queue()
        self.event_q = queue.Queue()
        self.session_dir = session_dir
        os.makedirs(self.session_dir, exist_ok=True)
        self.backend_path = resource_path(os.path.join("steam-backend", "python_bridge_backend.js"))
        self.backend_cwd = os.path.dirname(self.backend_path)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.running = False
        self.login_lock = threading.Lock()
        self.node_lock = threading.Lock()
        self.node_proc = None
        self.node_ready = False
        self.node_logged_in = False
        self.creds = {"user": "", "password": "", "refresh_token": ""}
        self._login_event = None
        self._login_result = None
        self._suppress_fail = False
        self.last_synced_game = None
        self._restart_after = 0

    def start(self):
        if self.running:
            return
        self.running = True
        self._start_node()
        self.thread.start()

    def shutdown(self):
        self.running = False
        try:
            self._send_node("set_status", {"game_name": ""})
            time.sleep(0.15)
            self._send_node("shutdown")
        except Exception:
            pass
        self._stop_node(timeout=3)

    def _resolve_node(self):
        for name in ("node.exe", "node"):
            bundled = resource_path(os.path.join("node", name))
            if os.path.exists(bundled):
                return bundled
        return shutil.which("node")

    def _start_node(self):
        with self.node_lock:
            return self._start_node_unlocked()

    def _start_node_unlocked(self):
        if self.node_proc and self.node_proc.poll() is None:
            return True
        if not os.path.exists(self.backend_path):
            logger.error("Steam backend script missing: %s", self.backend_path)
            self.event_q.put(("login_fail", "Steam backend missing from install"))
            return False
        node_exe = self._resolve_node()
        if not node_exe:
            logger.error("Node.js executable not found")
            self.event_q.put(("login_fail", "Node.js not found"))
            return False

        env = os.environ.copy()
        env["STEAM_SESSION_DIR"] = self.session_dir
        popen_kwargs = {}
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            popen_kwargs["startupinfo"] = startupinfo
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self.node_ready = False
            self.node_logged_in = False
            self.node_proc = subprocess.Popen(
                [node_exe, self.backend_path],
                cwd=self.backend_cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                **popen_kwargs,
            )
        except Exception as exc:
            logger.error("Failed to start Steam backend: %s", exc)
            self.node_proc = None
            self.event_q.put(("login_fail", str(exc)))
            return False

        threading.Thread(target=self._node_stdout_loop, args=(self.node_proc,), daemon=True).start()
        threading.Thread(target=self._node_stderr_loop, args=(self.node_proc,), daemon=True).start()
        return True

    def _stop_node(self, timeout=2):
        with self.node_lock:
            proc = self.node_proc
            self.node_proc = None
            self.node_ready = False
            self.node_logged_in = False
        if not proc:
            return
        if proc.poll() is None:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass

    def _send_node(self, cmd, data=None):
        if not (self.node_proc and self.node_proc.poll() is None and self.node_proc.stdin):
            return False
        try:
            self.node_proc.stdin.write(json.dumps({"cmd": cmd, "data": data or {}}, ensure_ascii=False) + "\n")
            self.node_proc.stdin.flush()
            return True
        except Exception as exc:
            logger.error("Steam backend command failed (%s): %s", cmd, exc)
            return False

    def _node_stdout_loop(self, proc):
        try:
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.info("Steam backend: %s", line)
                    continue
                self._handle_node_event(msg.get("event"), msg.get("data"))
        except Exception as exc:
            logger.error("Steam backend reader error: %s", exc)

    def _node_stderr_loop(self, proc):
        try:
            for raw in proc.stderr:
                line = raw.strip()
                if line:
                    logger.info("Steam backend: %s", line)
        except Exception:
            pass

    def _finish_login(self, status, info):
        if self._login_event and not self._login_event.is_set():
            self._login_result = (status, info)
            self._login_event.set()

    def _handle_node_event(self, event, data):
        if event == "backend_ready":
            self.node_ready = True
            logger.info("Steam backend ready")
        elif event == "login_success":
            self.node_logged_in = True
            self._finish_login("ok", None)
            self.event_q.put(("login_success", None))
        elif event == "login_fail":
            self.node_logged_in = False
            self._finish_login("fail", str(data))
            if not self._suppress_fail:
                self.event_q.put(("login_fail", str(data)))
        elif event == "new_login_key":
            self.creds["refresh_token"] = data
            self.event_q.put(("new_login_key", data))
        elif event in ("need_2fa", "need_2fa_retry", "disconnected"):
            if event == "disconnected":
                self.node_logged_in = False
            self.event_q.put((event, data))
        elif event:
            logger.info("Steam backend event %s: %s", event, data)

    def _node_login(self, user, password, refresh_token):
        if not self._start_node():
            return False, "Steam backend unavailable"
        deadline = time.time() + 8
        while not self.node_ready and time.time() < deadline:
            if self.node_proc and self.node_proc.poll() is not None:
                return False, "Steam backend exited"
            time.sleep(0.05)
        if not self.node_ready:
            return False, "Steam backend not ready"

        self._login_event = threading.Event()
        self._login_result = None
        self._suppress_fail = True
        ok = self._send_node("login", {
            "user": user,
            "password": password,
            "refresh_token": refresh_token or "",
        })
        if not ok:
            self._suppress_fail = False
            return False, "Could not talk to Steam backend"
        try:
            if not self._login_event.wait(timeout=120):
                return False, "Steam login timed out"
            status, info = self._login_result
            return status == "ok", info
        finally:
            self._login_event = None
            self._login_result = None
            self._suppress_fail = False

    def _run(self):
        while self.running:
            try:
                cmd, data = self.cmd_q.get(timeout=0.4)
            except queue.Empty:
                self._maybe_restart_node()
                continue
            try:
                if cmd == "login":
                    self.creds.update({
                        "user": str((data or {}).get("user") or ""),
                        "password": str((data or {}).get("password") or ""),
                        "refresh_token": str((data or {}).get("steam_refresh_token") or ""),
                    })
                    if self.login_lock.acquire(blocking=False):
                        threading.Thread(target=self._do_login, daemon=True).start()
                elif cmd == "submit_2fa":
                    self._send_node("submit_2fa", {"code": (data or {}).get("code", "")})
                elif cmd == "logout":
                    self.node_logged_in = False
                    self.creds["refresh_token"] = ""
                    self._send_node("logout")
                elif cmd == "set_status":
                    if isinstance(data, dict):
                        self._do_set_status(data.get("game_name", ""), bool(data.get("force", False)))
                    else:
                        self._do_set_status(data)
                elif cmd == "shutdown":
                    self.running = False
            except Exception as exc:
                logger.error("Steam worker error: %s", exc)

    def _maybe_restart_node(self):
        if not self.running:
            return
        if self.node_proc and self.node_proc.poll() is None:
            return
        now = time.time()
        if now < self._restart_after:
            return
        self._restart_after = now + 3
        if self.node_proc is None and not self.creds.get("refresh_token") and not self.creds.get("password"):
            return
        logger.warning("Steam backend not running, restarting")
        if not self._start_node():
            return
        if self.creds.get("refresh_token") or (self.creds.get("user") and self.creds.get("password")):
            if self.login_lock.acquire(blocking=False):
                threading.Thread(target=self._do_login, daemon=True).start()

    def _do_login(self):
        try:
            user = self.creds.get("user", "").strip()
            password = self.creds.get("password", "").strip()
            refresh_token = self.creds.get("refresh_token", "").strip()
            if refresh_token:
                logger.info("Steam: trying saved session")
                ok, err = self._node_login(user, password, refresh_token)
                if ok:
                    logger.info("Steam: logged in with saved session")
                    return
                logger.warning("Steam: saved session failed: %s", err)
            if user and password:
                logger.info("Steam: trying username/password")
                ok, err = self._node_login(user, password, "")
                if ok:
                    logger.info("Steam: logged in with password")
                    return
                logger.warning("Steam: password login failed: %s", err)
            self.event_q.put(("login_fail", "Steam login failed"))
        except Exception as exc:
            logger.error("Steam login error: %s", exc)
            self.event_q.put(("login_fail", str(exc)))
        finally:
            try:
                self.login_lock.release()
            except RuntimeError:
                pass

    def _do_set_status(self, game_name, force=False):
        name = (game_name or "").strip()
        if not force and self.last_synced_game == name:
            return
        if not name:
            logger.info("Steam: clearing status")
        else:
            logger.info("Steam: status -> PS5: %s", name)
        self._send_node("set_status", {"game_name": name})
        self.last_synced_game = name


class GuiLogHandler(logging.Handler):
    def __init__(self, app):
        super().__init__()
        self.app = app

    def emit(self, record):
        try:
            self.app.after(0, self.app.append_log, self.format(record))
        except Exception:
            pass


class BridgeApp(ctk.CTk):
    def __init__(self, data_dir):
        super().__init__()
        self.data_dir = data_dir
        self.steam_initialized = False
        self.psn_initialized = False
        self.psn_verifying = False
        self.is_bridge_running = False
        self.is_closing = False
        self.psn_access_token = None
        self.last_presence = None
        self.steam_user = ""
        self.steam_pass = ""
        self.steam_refresh_token = ""

        self.steam_worker = SteamWorker(os.path.join(data_dir, "steam_session"))
        self.steam_worker.start()

        self.title("PS5 to Steam")
        self.geometry("440x680")
        self.minsize(400, 620)
        self.configure(fg_color=BG)
        self.setup_ui()
        self._attach_gui_log()
        self.load_config()
        self.poll_worker_events()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.steam_refresh_token or (self.steam_user and self.steam_pass):
            self.set_steam_state("wait", "Signing in…")
            self.steam_worker.cmd_q.put(("login", {
                "user": self.steam_user,
                "password": self.steam_pass,
                "steam_refresh_token": self.steam_refresh_token,
            }))
        if not self.steam_refresh_token and not self.steam_pass and not self._npsso():
            self.show_settings()
        self.after(4000, self.update_status)

    def setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.home = ctk.CTkFrame(self, fg_color=BG)
        self.settings = ctk.CTkFrame(self, fg_color=BG)
        self._build_home()
        self._build_settings()
        self.show_home()

    def _font(self, size, weight="normal"):
        return ctk.CTkFont(family=FONT, size=size, weight=weight)

    def _chip(self, parent, name):
        chip = ctk.CTkFrame(parent, fg_color=INSET, corner_radius=20, height=36)
        chip.grid_columnconfigure(1, weight=1)
        dot = ctk.CTkLabel(chip, text="●", font=self._font(11), text_color=DOT["off"], width=18)
        dot.grid(row=0, column=0, padx=(12, 0), pady=8)
        label = ctk.CTkLabel(chip, text=name, font=self._font(13), text_color=TEXT, anchor="w")
        label.grid(row=0, column=1, padx=(4, 14), pady=8, sticky="w")
        return chip, dot, label

    def _card(self, parent):
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=18, border_width=1, border_color=LINE)

    def _ghost_btn(self, parent, text, command, **kwargs):
        return ctk.CTkButton(
            parent, text=text, command=command,
            fg_color=INSET, hover_color=LINE, text_color=TEXT,
            height=36, corner_radius=10, font=self._font(13), **kwargs,
        )

    def _build_home(self):
        page = self.home
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(page, fg_color="transparent")
        top.grid(row=0, column=0, padx=28, pady=(26, 8), sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="PS5 → Steam", font=self._font(22, "bold"), text_color=TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            top, text="Settings", command=self.show_settings, width=88, height=32,
            fg_color="transparent", hover_color=INSET, border_width=1, border_color=LINE,
            text_color=MUTED, font=self._font(13), corner_radius=8,
        ).grid(row=0, column=1, sticky="e")

        chips = ctk.CTkFrame(page, fg_color="transparent")
        chips.grid(row=1, column=0, padx=28, pady=(8, 4), sticky="ew")
        chips.grid_columnconfigure((0, 1), weight=1)
        self.steam_chip, self.steam_dot, self.steam_chip_text = self._chip(chips, "Steam")
        self.steam_chip.grid(row=0, column=0, padx=(0, 6), sticky="ew")
        self.psn_chip, self.psn_dot, self.psn_chip_text = self._chip(chips, "PlayStation")
        self.psn_chip.grid(row=0, column=1, padx=(6, 0), sticky="ew")

        hero = self._card(page)
        hero.grid(row=2, column=0, padx=28, pady=16, sticky="nsew")
        hero.grid_columnconfigure(0, weight=1)
        hero.grid_rowconfigure(2, weight=1)
        self.presence_kicker = ctk.CTkLabel(hero, text="NOW PLAYING", font=self._font(11), text_color=MUTED, anchor="w")
        self.presence_kicker.grid(row=0, column=0, padx=22, pady=(22, 0), sticky="w")
        self.presence_title = ctk.CTkLabel(
            hero, text="Nothing yet", font=self._font(26, "bold"),
            text_color=TEXT, anchor="w", wraplength=300, justify="left",
        )
        self.presence_title.grid(row=1, column=0, padx=22, pady=(6, 0), sticky="w")
        self.presence_hint = ctk.CTkLabel(
            hero, text="Connect both accounts, then start.",
            font=self._font(13), text_color=MUTED, anchor="w", wraplength=340, justify="left",
        )
        self.presence_hint.grid(row=2, column=0, padx=22, pady=(10, 22), sticky="nw")

        bottom = ctk.CTkFrame(page, fg_color="transparent")
        bottom.grid(row=3, column=0, padx=28, pady=(0, 24), sticky="ew")
        bottom.grid_columnconfigure(0, weight=1)
        self.start_button = ctk.CTkButton(
            bottom, text="Start", command=self.toggle_bridge, state="disabled",
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0b0c10",
            height=50, corner_radius=14, font=self._font(16, "bold"),
        )
        self.start_button.grid(row=0, column=0, sticky="ew")
        self.activity_label = ctk.CTkLabel(bottom, text="", font=self._font(11), text_color=MUTED, anchor="w")
        self.activity_label.grid(row=1, column=0, pady=(10, 0), sticky="ew")

    def _build_settings(self):
        page = self.settings
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(3, weight=1)

        top = ctk.CTkFrame(page, fg_color="transparent")
        top.grid(row=0, column=0, padx=28, pady=(26, 4), sticky="ew")
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(
            top, text="← Home", command=self.show_home, width=80, height=32,
            fg_color="transparent", hover_color=INSET, text_color=MUTED,
            font=self._font(13), corner_radius=8,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(top, text="Settings", font=self._font(22, "bold"), text_color=TEXT).grid(row=0, column=1, sticky="w", padx=8)

        steam = self._card(page)
        steam.grid(row=1, column=0, padx=28, pady=(16, 8), sticky="ew")
        steam.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(steam, text="Steam", font=self._font(16, "bold"), text_color=TEXT, anchor="w").grid(row=0, column=0, padx=20, pady=(18, 2), sticky="w")
        self.steam_detail = ctk.CTkLabel(steam, text="Account name, not the profile name.", font=self._font(12), text_color=MUTED, anchor="w")
        self.steam_detail.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="w")
        self.steam_user_entry = ctk.CTkEntry(
            steam, placeholder_text="Account name", height=40, corner_radius=10,
            fg_color=INSET, border_color=LINE, text_color=TEXT,
        )
        self.steam_user_entry.grid(row=2, column=0, padx=20, pady=4, sticky="ew")
        self.steam_pass_entry = ctk.CTkEntry(
            steam, placeholder_text="Password (first login only)", show="*", height=40,
            corner_radius=10, fg_color=INSET, border_color=LINE, text_color=TEXT,
        )
        self.steam_pass_entry.grid(row=3, column=0, padx=20, pady=4, sticky="ew")
        steam_btns = ctk.CTkFrame(steam, fg_color="transparent")
        steam_btns.grid(row=4, column=0, padx=20, pady=(10, 18), sticky="ew")
        steam_btns.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            steam_btns, text="Sign in", command=self.do_steam_login,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0b0c10",
            height=38, corner_radius=10, font=self._font(13, "bold"),
        ).grid(row=0, column=0, sticky="ew")
        self._ghost_btn(steam_btns, "Clear saved session", self.reset_steam_session).grid(row=1, column=0, pady=(8, 0), sticky="ew")

        psn = self._card(page)
        psn.grid(row=2, column=0, padx=28, pady=8, sticky="ew")
        psn.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(psn, text="PlayStation", font=self._font(16, "bold"), text_color=TEXT, anchor="w").grid(row=0, column=0, columnspan=2, padx=20, pady=(18, 2), sticky="w")
        self.psn_detail = ctk.CTkLabel(
            psn, text="Log in on the web, open the token page, paste it here.",
            font=self._font(12), text_color=MUTED, anchor="w", wraplength=340, justify="left",
        )
        self.psn_detail.grid(row=1, column=0, columnspan=2, padx=20, pady=(0, 10), sticky="w")
        self._ghost_btn(psn, "playstation.com", lambda: webbrowser.open("https://www.playstation.com/")).grid(row=2, column=0, padx=(20, 6), pady=4, sticky="ew")
        self._ghost_btn(psn, "Token page", lambda: webbrowser.open("https://ca.account.sony.com/api/v1/ssocookie")).grid(row=2, column=1, padx=(6, 20), pady=4, sticky="ew")
        self.npsso_token_entry = ctk.CTkEntry(
            psn, placeholder_text="npsso or full JSON", show="*", height=40,
            corner_radius=10, fg_color=INSET, border_color=LINE, text_color=TEXT,
        )
        self.npsso_token_entry.grid(row=3, column=0, columnspan=2, padx=20, pady=8, sticky="ew")
        self.verify_button = ctk.CTkButton(
            psn, text="Verify", command=self.verify_psn,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0b0c10",
            height=38, corner_radius=10, font=self._font(13, "bold"),
        )
        self.verify_button.grid(row=4, column=0, columnspan=2, padx=20, pady=(4, 18), sticky="ew")

        self.log_box = ctk.CTkTextbox(
            page, height=90, font=self._font(11), fg_color=CARD,
            border_color=LINE, border_width=1, text_color=MUTED, corner_radius=12,
        )
        self.log_box.grid(row=3, column=0, padx=28, pady=(8, 24), sticky="nsew")
        self.log_box.configure(state="disabled")

    def show_home(self):
        self.settings.grid_remove()
        self.home.grid(row=0, column=0, sticky="nsew")
        self._refresh_home()

    def show_settings(self):
        self.home.grid_remove()
        self.settings.grid(row=0, column=0, sticky="nsew")
        if self.steam_user and not self.steam_user_entry.get():
            self.steam_user_entry.insert(0, self.steam_user)

    def _paint_chip(self, dot, label, name, kind):
        suffix = {"ok": "", "wait": "…", "err": "", "off": ""}[kind]
        dot.configure(text_color=DOT[kind])
        label.configure(text=name + suffix, text_color=TEXT if kind != "off" else MUTED)

    def set_steam_state(self, kind, message=None):
        self._paint_chip(self.steam_dot, self.steam_chip_text, "Steam", kind)
        if message:
            self.steam_detail.configure(text=message)
        self._refresh_home()

    def set_psn_state(self, kind, message=None):
        self._paint_chip(self.psn_dot, self.psn_chip_text, "PlayStation", kind)
        if message:
            self.psn_detail.configure(text=message)
        self._refresh_home()

    def _refresh_home(self):
        if self.is_bridge_running:
            self.start_button.configure(
                text="Stop", state="normal",
                fg_color="#3a1520", hover_color="#4a1b28", text_color=BAD, border_width=1, border_color=BAD,
            )
            if self.last_presence:
                self.presence_kicker.configure(text="NOW PLAYING")
                self.presence_title.configure(text=self.last_presence)
                self.presence_hint.configure(text="Shown on Steam as  PS5: " + self.last_presence)
            else:
                self.presence_kicker.configure(text="BRIDGE ON")
                self.presence_title.configure(text="Waiting for PS5")
                self.presence_hint.configure(text="Idle or offline on PlayStation.")
            return
        self.start_button.configure(border_width=0, text_color="#0b0c10", hover_color=ACCENT_HOVER)
        ready = self.steam_initialized and self.psn_initialized
        self.start_button.configure(
            text="Start",
            state="normal" if ready else "disabled",
            fg_color=ACCENT if ready else INSET,
            text_color="#0b0c10" if ready else MUTED,
        )
        self.presence_kicker.configure(text="NOW PLAYING")
        self.presence_title.configure(text="Nothing yet")
        if ready:
            self.presence_hint.configure(text="Ready. Start to mirror your PS5.")
        elif not self.steam_initialized and not self.psn_initialized:
            self.presence_hint.configure(text="Connect Steam and PlayStation in Settings.")
        elif not self.steam_initialized:
            self.presence_hint.configure(text="Sign in to Steam in Settings.")
        else:
            self.presence_hint.configure(text="Add your PlayStation token in Settings.")

    def _attach_gui_log(self):
        handler = GuiLogHandler(self)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)

    def append_log(self, line):
        try:
            short = line.split("] ", 1)[-1]
            self.activity_label.configure(text=short[:80])
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line + "\n")
            if int(float(self.log_box.index("end"))) > 200:
                self.log_box.delete("1.0", "80.0")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        except Exception:
            pass

    def _npsso(self):
        return normalize_npsso(self.npsso_token_entry.get())

    def load_config(self):
        credstore.migrate_plaintext_config(self.data_dir)
        public = credstore.load_public(self.data_dir)
        secrets = credstore.load_secrets(self.data_dir)
        self.steam_user = public.get("steam_user", "")
        self.steam_refresh_token = secrets.get("steam_refresh_token", "")
        self.steam_pass = "" if self.steam_refresh_token else secrets.get("steam_pass", "")
        npsso = secrets.get("npsso", "")
        if npsso:
            self.npsso_token_entry.insert(0, npsso)
        if self.steam_user:
            self.steam_user_entry.delete(0, "end")
            self.steam_user_entry.insert(0, self.steam_user)

    def _internal_save(self):
        try:
            credstore.save_public(self.data_dir, {"steam_user": self.steam_user})
            secrets = {
                "npsso": self._npsso(),
                "steam_refresh_token": self.steam_refresh_token,
            }
            if not self.steam_refresh_token and self.steam_pass:
                secrets["steam_pass"] = self.steam_pass
            credstore.save_secrets(self.data_dir, secrets)
        except OSError as exc:
            logger.error("Could not save config: %s", exc)

    def poll_worker_events(self):
        try:
            while True:
                event, data = self.steam_worker.event_q.get_nowait()
                if event == "login_success":
                    self.steam_initialized = True
                    self.set_steam_state("ok", "Signed in. Session is saved on this PC.")
                    if self.is_bridge_running and self.last_presence:
                        self.steam_worker.cmd_q.put(("set_status", self.last_presence))
                    self.check_enable_start()
                elif event == "new_login_key":
                    if data != self.steam_refresh_token:
                        self.steam_refresh_token = data
                        self.steam_pass = ""
                        self._internal_save()
                        logger.info("Saved Steam session token")
                elif event in ("need_2fa", "need_2fa_retry"):
                    kind = data if data in ("email", "app") else "app"
                    prompt = "Code from your Steam app" if kind == "app" else "Code from your Steam Guard email"
                    if event == "need_2fa_retry":
                        prompt = "Wrong code. " + prompt
                    code = self.ask_steam_guard(prompt)
                    if code:
                        self.steam_worker.cmd_q.put(("submit_2fa", {"code": code}))
                elif event == "login_fail":
                    logger.error("Steam login failed: %s", data)
                    if "Invalid" in str(data):
                        self.steam_refresh_token = ""
                    self.steam_initialized = False
                    self.set_steam_state("err", str(data))
                    self.check_enable_start()
                elif event == "disconnected":
                    self.steam_initialized = False
                    self.set_steam_state("wait", "Reconnecting…")
                    self.check_enable_start()
        except queue.Empty:
            pass
        if not self.is_closing:
            self.after(100, self.poll_worker_events)

    def ask_steam_guard(self, message):
        result = {"code": None}
        dialog = ctk.CTkToplevel(self)
        dialog.title("Steam Guard")
        dialog.geometry("360x220")
        dialog.configure(fg_color=BG)
        dialog.transient(self)
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(dialog, text="Steam Guard", font=self._font(18, "bold"), text_color=TEXT).grid(row=0, column=0, padx=24, pady=(24, 6))
        ctk.CTkLabel(dialog, text=message, font=self._font(13), text_color=MUTED, wraplength=300).grid(row=1, column=0, padx=24, pady=(0, 12))
        entry = ctk.CTkEntry(dialog, width=240, height=40, fg_color=INSET, border_color=LINE)
        entry.grid(row=2, column=0, pady=4)
        entry.focus()

        def submit(_event=None):
            result["code"] = entry.get().strip()
            dialog.destroy()

        entry.bind("<Return>", submit)
        ctk.CTkButton(
            dialog, text="Continue", command=submit, width=240, height=38,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0b0c10",
            font=self._font(13, "bold"), corner_radius=10,
        ).grid(row=3, column=0, pady=(12, 24))
        dialog.wait_window()
        return result["code"]

    def verify_psn(self):
        if self.psn_verifying:
            return
        npsso = self._npsso()
        if not npsso:
            return
        self.psn_verifying = True
        self.set_psn_state("wait", "Checking token…")
        threading.Thread(target=self._threaded_verify_psn, args=(npsso,), daemon=True).start()

    def _threaded_verify_psn(self, npsso):
        try:
            token = psn.authenticate(npsso)
            if token:
                self.psn_access_token = token
                self.psn_initialized = True
                self.after(0, lambda: self.set_psn_state("ok", "Connected. Token stays on this PC."))
                self.after(0, self._internal_save)
            else:
                self.psn_initialized = False
                self.after(0, lambda: self.set_psn_state("err", "Token rejected. Log in again and copy a fresh npsso."))
        except Exception as exc:
            logger.error("PSN error: %s", exc)
            self.psn_initialized = False
            self.after(0, lambda: self.set_psn_state("err", "Network error. Try again."))
        finally:
            self.psn_verifying = False
            self.after(0, self.check_enable_start)

    def check_enable_start(self):
        self._refresh_home()

    def set_presence(self, title):
        self.last_presence = title
        self._refresh_home()

    def bridge_loop(self):
        logger.info("Bridge started")
        while self.is_bridge_running:
            try:
                if self.psn_access_token:
                    status, title = psn.current_title(self.psn_access_token)
                    if status == 401:
                        self.psn_access_token = psn.authenticate(self._npsso())
                        if not self.psn_access_token:
                            self.psn_initialized = False
                            self.after(0, lambda: self.set_psn_state("err", "Token expired. Paste a fresh npsso."))
                            self.after(0, self._stop_bridge_ui)
                            break
                        continue
                    if status != 200:
                        time.sleep(10)
                        continue
                    if not self.is_bridge_running:
                        break
                    self.after(0, self.set_presence, title)
                    self.steam_worker.cmd_q.put(("set_status", title))
                time.sleep(30)
            except Exception as exc:
                logger.error("Bridge loop error: %s", exc)
                time.sleep(10)
        self.steam_worker.cmd_q.put(("set_status", {"game_name": "", "force": True}))
        self.after(0, self.set_presence, None)
        logger.info("Bridge stopped")

    def update_status(self):
        if not self.psn_initialized and not self.psn_verifying and self._npsso():
            self.verify_psn()
        self.after(30000, self.update_status)

    def reset_steam_session(self):
        if not messagebox.askyesno("Reset Steam", "Log out and clear the saved Steam session on this PC?"):
            return
        self.steam_refresh_token = ""
        self.steam_worker.cmd_q.put(("logout", None))
        session_dir = os.path.join(self.data_dir, "steam_session")
        try:
            if os.path.exists(session_dir):
                shutil.rmtree(session_dir)
            os.makedirs(session_dir, exist_ok=True)
        except Exception as exc:
            logger.error("Could not clear Steam session: %s", exc)
        self._internal_save()
        self.steam_initialized = False
        self.set_steam_state("off", "Session cleared. Sign in again.")
        self.check_enable_start()

    def do_steam_login(self):
        self.steam_user = self.steam_user_entry.get().strip()
        password = self.steam_pass_entry.get().strip()
        if password:
            self.steam_pass = password
            self.steam_refresh_token = ""
        self._internal_save()
        self.set_steam_state("wait", "Signing in…")
        self.steam_worker.cmd_q.put(("login", {
            "user": self.steam_user,
            "password": self.steam_pass,
            "steam_refresh_token": self.steam_refresh_token,
        }))

    def toggle_bridge(self):
        if not self.is_bridge_running:
            self.is_bridge_running = True
            self._refresh_home()
            threading.Thread(target=self.bridge_loop, daemon=True).start()
        else:
            self._stop_bridge_ui()
            self.steam_worker.cmd_q.put(("set_status", {"game_name": "", "force": True}))

    def _stop_bridge_ui(self):
        self.is_bridge_running = False
        self.last_presence = None
        self._refresh_home()

    def on_close(self):
        if self.is_closing:
            return
        self.is_closing = True
        self.is_bridge_running = False
        try:
            self.steam_worker.shutdown()
        except Exception as exc:
            logger.error("Close error: %s", exc)
        self.destroy()


def main():
    data_dir = app_data_dir()
    migrate_legacy_data(data_dir)
    credstore.migrate_plaintext_config(data_dir)
    setup_logging(data_dir)
    app = BridgeApp(data_dir)
    app.mainloop()


if __name__ == "__main__":
    main()
