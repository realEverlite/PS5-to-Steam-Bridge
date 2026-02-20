import os
import sys
import traceback
import json
import webbrowser
import threading
import time
import queue
import logging
import zlib
import subprocess
import asyncio
import requests
import gevent
import customtkinter as ctk
from tkinter import TclError, simpledialog, messagebox
from steam.client import SteamClient
from steam.enums import EPersonaState
from steam.core.msg import MsgProto
from steam.enums.emsg import EMsg
from steam.core.cm import CMClient
from eventemitter import EventEmitter

def _patch_cmclient_eventemitter_init():
    """
    Compatibility patch for newer eventemitter package releases.
    ValvePython's CMClient assumes EventEmitter internals exist before self.on(...)
    calls, but doesn't call EventEmitter.__init__ itself.
    """
    if getattr(CMClient, "_evt_init_patched", False):
        return

    original_init = CMClient.__init__

    def patched_init(self, *args, **kwargs):
        if not hasattr(self, "_listeners"):
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            EventEmitter.__init__(self, loop=loop)
        return original_init(self, *args, **kwargs)

    CMClient.__init__ = patched_init
    CMClient._evt_init_patched = True

_patch_cmclient_eventemitter_init()

# --- Global Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("Bridge")

# --- Path Handling for Standalone Bundling ---
def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

# --- Sony API Constants ---
SONY_AUTH_URL = "https://ca.account.sony.com/api/authz/v3/oauth/authorize"
SONY_TOKEN_URL = "https://ca.account.sony.com/api/authz/v3/oauth/token"
PSN_PRESENCE_URL = "https://m.np.playstation.com/api/userProfile/v1/internal/users/me/basicPresences?type=primary"
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
REDIRECT_URI = "com.scee.psxandroid.scecompcall://redirect"
SCOPE = "psn:mobile.v2.core psn:clientapp"
USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 15_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.7 Mobile/15E148 Safari/604.1"
PSN_CLIENT_AUTH = "Basic MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="

# Set appearance and theme
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

def make_nonsteam_game_id(name: str) -> int:
    """Generate a stable Non-Steam GameID using the standard Steam shortcut formula."""
    crc = zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF
    return ((crc | 0x80000000) << 32) | 0x02000000

class SteamWorker:
    """Isolated Steam Worker using a non-blocking pump to keep Gevent moving"""
    def __init__(self, root_dir):
        self.cmd_q = queue.Queue()
        self.event_q = queue.Queue()
        self.credential_dir = os.path.join(root_dir, "steam_session")
        os.makedirs(self.credential_dir, exist_ok=True)
        
        self.client = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.running = False
        self.login_lock = threading.Lock()
        self.two_factor_result = None # Will be gevent.event.AsyncResult
        # Prevent concurrent login attempts
        self.login_data = {} 
        self.machine_id = None
        self.last_status_text = ""
        self.last_sent_ts = 0
        self.node_proc = None
        self.node_ready = False
        self.node_logged_in = False
        self.node_login_result = None
        self.node_backend_rel_dir = "node-steam-session-master"
        self.node_backend_path = get_resource_path(os.path.join(self.node_backend_rel_dir, "python_bridge_backend.js"))
        self.node_backend_cwd = os.path.dirname(self.node_backend_path)
        self.node_suppress_fail_events = False

    def start(self):
        if not self.running:
            self.running = True
            self._start_node_backend()
            self.thread.start()

    def _start_node_backend(self):
        if self.node_proc and self.node_proc.poll() is None:
            return True
        if not os.path.exists(self.node_backend_path):
            logger.warning(f"Worker: Node backend script not found: {self.node_backend_path}")
            return False
        node_exe = self._resolve_node_executable()
        if not node_exe:
            logger.error("Worker: Could not resolve Node.js executable.")
            return False

        try:
            self.node_proc = subprocess.Popen(
                [node_exe, self.node_backend_path],
                cwd=self.node_backend_cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except Exception as e:
            logger.error(f"Worker: Failed to start Node backend: {e}")
            self.node_proc = None
            return False

        threading.Thread(target=self._node_reader_loop, daemon=True).start()
        return True

    def _resolve_node_executable(self):
        bundled_node = get_resource_path(os.path.join("node", "node.exe"))
        if os.path.exists(bundled_node):
            return bundled_node
        return "node"

    def _send_node_command(self, cmd, data=None):
        if not (self.node_proc and self.node_proc.poll() is None and self.node_proc.stdin):
            return False
        try:
            payload = {"cmd": cmd, "data": data or {}}
            self.node_proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.node_proc.stdin.flush()
            return True
        except Exception as e:
            logger.error(f"Worker: Node command send failed ({cmd}): {e}")
            return False

    def _node_reader_loop(self):
        proc = self.node_proc
        if not proc or not proc.stdout:
            return
        try:
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    logger.info(f"Node: {line}")
                    continue

                event = msg.get("event")
                data = msg.get("data")
                if event == "backend_ready":
                    self.node_ready = True
                    logger.info("Worker: Node backend ready.")
                elif event == "login_success":
                    self.node_logged_in = True
                    if self.node_login_result and not self.node_login_result.ready():
                        self.node_login_result.set(("ok", None))
                    self.event_q.put(("login_success", None))
                elif event == "login_fail":
                    self.node_logged_in = False
                    if self.node_login_result and not self.node_login_result.ready():
                        self.node_login_result.set(("fail", str(data)))
                    if not self.node_suppress_fail_events:
                        self.event_q.put(("login_fail", str(data)))
                elif event == "new_login_key":
                    self.event_q.put(("new_login_key", data))
                elif event in ("need_2fa", "need_2fa_retry", "disconnected"):
                    self.event_q.put((event, data))
                elif event == "backend_error":
                    logger.warning(f"Worker: Node backend error: {data}")
                else:
                    logger.info(f"Worker: Node event {event}: {data}")
        except Exception as e:
            logger.error(f"Worker: Node reader loop error: {e}")

    def _node_login(self, user, pw, refresh_token):
        if not self._start_node_backend():
            return (False, "Node backend unavailable")
        self.node_login_result = gevent.event.AsyncResult()
        self.node_suppress_fail_events = True
        ok = self._send_node_command("login", {
            "user": user,
            "password": pw,
            "refresh_token": refresh_token or "",
        })
        if not ok:
            self.node_suppress_fail_events = False
            return (False, "Could not send login command to Node backend")
        try:
            status, info = self.node_login_result.get(timeout=120)
            return (status == "ok", info)
        except gevent.Timeout:
            return (False, "Node login timeout")
        finally:
            self.node_login_result = None
            self.node_suppress_fail_events = False

    def _run(self):
        self.client = SteamClient()
        self.client.set_credential_location(self.credential_dir)
        logger.info(f"Worker: Credential location set to: {self.credential_dir}")

        def handle_machine_auth(msg):
            logger.info("Worker EVENT: Received ClientUpdateMachineAuth! Steam is trying to save a sentry file.")

        def handle_new_key():
            key = self.client.login_key
            logger.info(f"Worker EVENT: Received EVENT_NEW_LOGIN_KEY: {key[:10]}...")
            if key:
                self.event_q.put(("new_login_key", key))

        def handle_all(msg, *args):
            # Print any incoming msg that isn't spammy
            try:
                if hasattr(msg, 'msg'):
                    em = str(msg.msg)
                    if "Multi" not in em and "ClientGamesPlayed" not in em and "ClientUpdate" not in em:
                        pass # too noisy if we log all. Let's log important ones.
                    if "UpdateMachineAuth" in em or "AuthList" in em or "NewLoginKey" in em or "WebAPI" in em:
                        logger.info(f"Worker SNIFF: {em}")
            except: pass

        self.client.on(EMsg.ClientUpdateMachineAuth, handle_machine_auth)
        self.client.on(self.client.EVENT_NEW_LOGIN_KEY, handle_new_key)
        self.client.on(None, handle_all)

        # 1. Command Processing Loop
        def command_processor():
            while self.running:
                try:
                    cmd, data = self.cmd_q.get_nowait()
                    if cmd == "login":
                        self.login_data = data
                        if self.login_lock.acquire(blocking=False):
                            gevent.spawn(self._do_login)
                    elif cmd == "submit_2fa":
                        if self.two_factor_result:
                            self.two_factor_result.set(data["code"])
                        self._send_node_command("submit_2fa", {"code": data.get("code", "")})
                    elif cmd == "logout":
                        if self.client: self.client.disconnect()
                        self.node_logged_in = False
                        self._send_node_command("logout")
                    elif cmd == "set_status":
                        if isinstance(data, dict):
                            gevent.spawn(self._do_set_status, data.get("game_name", ""), bool(data.get("force", False)))
                        else:
                            gevent.spawn(self._do_set_status, data)
                    elif cmd == "shutdown":
                        try:
                            self._do_set_status("", True)
                        except Exception:
                            pass
                        try:
                            if self.client:
                                self.client.disconnect()
                        except Exception:
                            pass
                        self.node_logged_in = False
                        self._send_node_command("shutdown")
                        self.running = False
                except queue.Empty:
                    gevent.sleep(0.1)
                except Exception as e:
                    logger.error(f"Worker Cmd Error: {e}")

        gevent.spawn(command_processor)

        # 2. Presence Monitor (Stop the Tango)
        self.last_synced_game = None
        
        # 3. Non-Blocking Event Pump
        logger.info("Worker: Event pump started.")
        last_captured_key = None
        while self.running:
            try:
                self.client.sleep(0.1)
                
                # Check connection status for GUI
                if self.client.logged_on and not self.client.connected:
                    self.event_q.put(("disconnected", None))
            except Exception as e:
                logger.error(f"Worker Pump Error: {e}")
                gevent.sleep(1)

    def shutdown(self):
        try:
            self.cmd_q.put(("shutdown", None))
        except Exception as e:
            logger.error(f"Worker shutdown queue error: {e}")

    def _send_games_played(self, games):
        if not self.client or not self.client.connected: return
        msg = MsgProto(EMsg.ClientGamesPlayed)
        self.client.send(msg, {"games_played": games})

    def _do_login(self):
        user = str(self.login_data.get("user") or "").strip()
        pw = str(self.login_data.get("password") or "").strip()
        refresh_token = self.login_data.get("steam_refresh_token")
        self.two_factor_result = gevent.event.AsyncResult()
        
        # Masked password debug
        p_dbg = f"{pw[0]}...{pw[-1]}" if len(pw) > 2 else "***"
        logger.info(f"Worker: Login Target: {user} | Pass: {len(pw)} chars ({p_dbg})")
        try:
            if refresh_token:
                logger.info("Worker: Trying Node backend login with saved refresh_token...")
                node_ok, node_err = self._node_login(user, pw, refresh_token)
                if node_ok:
                    logger.info("Worker: Node backend login successful with saved refresh_token.")
                    return
                logger.warning(f"Worker: Node backend refresh-token login failed: {node_err}")

            if user and pw:
                logger.info("Worker: Trying Node backend login with username/password...")
                node_ok, node_err = self._node_login(user, pw, "")
                if node_ok:
                    logger.info("Worker: Node backend login successful with username/password.")
                    return
                logger.warning(f"Worker: Node backend password login failed: {node_err}")

            self.event_q.put(("login_fail", "Steam login failed (refresh_token and password path)."))

        except Exception as e:
            logger.error(f"Worker Login Error: {e}")
            self.event_q.put(("login_fail", str(e)))
        finally:
            self.two_factor_result = None
            try: self.login_lock.release()
            except: pass

    def _do_set_status(self, game_name, force=False):
        """ Updates the game status carefully to avoid flickering """
        # Normalized game name
        gn = (game_name or "").strip()
        
        # Only update if it actually changed to stop the "Tango"
        if not force and hasattr(self, "last_synced_game") and self.last_synced_game == gn:
            return

        try:
            if not gn:
                logger.info("Worker: Clearing status.")
                self._send_games_played([])
                if self.node_logged_in:
                    self._send_node_command("set_status", {"game_name": "", "game_id": 0})
            else:
                text = f"PS5: {gn}"
                gid = make_nonsteam_game_id(text)
                logger.info(f"Worker: Status Update -> {text}")
                self._send_games_played([{"game_id": int(gid), "game_extra_info": text}])
                if self.node_logged_in:
                    self._send_node_command("set_status", {"game_name": gn, "game_id": int(gid)})
            
            self.last_synced_game = gn
            self.last_sent_ts = time.time()
        except Exception as e:
            logger.error(f"Worker Status Error: {e}")

class BridgeApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        # Use dynamic path for config/sessions to support standalone EXE
        self.app_data_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
        self.config_path = os.path.join(self.app_data_dir, "config.json")
        
        self.steam_initialized = False
        self.psn_initialized = False
        self.psn_verifying = False
        self.is_bridge_running = False
        self.is_closing = False
        self.psn_access_token = None
        
        self.steam_worker = SteamWorker(self.app_data_dir)
        self.steam_worker.start()
        
        self.steam_user = ""
        self.steam_pass = ""
        self.steam_refresh_token = ""
        self.settings_window = None

        self.title("PS5-to-Steam Bridge V2")
        self.geometry("500x600")
        self.resizable(False, False)
        self.setup_ui()
        self.load_config()
        self.poll_worker_events()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
        if self.steam_user and self.steam_pass:
            self.steam_status.configure(text="Steam: 🟡 Auto-Login...", text_color="orange")
            self.steam_worker.cmd_q.put(("login", {
                "user": self.steam_user, 
                "password": self.steam_pass, 
                "steam_refresh_token": getattr(self, "steam_refresh_token", "")
            }))

        self.after(5000, self.update_status)

    def setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.header_label = ctk.CTkLabel(
            self, text="PS5 ↔ Steam Bridge", 
            font=ctk.CTkFont(family="Inter", size=28, weight="bold"),
            text_color="#3b8ed0"
        )
        self.header_label.grid(row=0, column=0, padx=20, pady=(40, 30))

        self.status_frame = ctk.CTkFrame(self, fg_color="#1a1a1a", corner_radius=15, border_width=1, border_color="#333333")
        self.status_frame.grid(row=1, column=0, padx=40, pady=10, sticky="ew")
        self.steam_status = ctk.CTkLabel(self.status_frame, text="Steam: ⚪ Disconnected", font=ctk.CTkFont(size=15, weight="normal"), text_color="#888888")
        self.steam_status.pack(pady=(15, 5))
        self.psn_status = ctk.CTkLabel(self.status_frame, text="PSN: ⚪ Disconnected", font=ctk.CTkFont(size=15, weight="normal"), text_color="#888888")
        self.psn_status.pack(pady=(5, 15))

        self.config_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.config_frame.grid(row=2, column=0, padx=40, pady=20, sticky="ew")
        self.config_frame.grid_columnconfigure(0, weight=1)
        btn_style = {"height": 35, "corner_radius": 8, "font": ctk.CTkFont(size=13)}
        ctk.CTkButton(self.config_frame, text="1. Login to PlayStation.com", command=lambda: webbrowser.open("https://www.playstation.com/"), fg_color="#2c2c2c", hover_color="#3d3d3d", **btn_style).grid(row=0, column=0, padx=0, pady=(0, 10), sticky="ew")
        ctk.CTkButton(self.config_frame, text="2. Get NPSSO Token", command=lambda: webbrowser.open("https://ca.account.sony.com/api/v1/ssocookie"), fg_color="#2c2c2c", hover_color="#3d3d3d", **btn_style).grid(row=1, column=0, padx=0, pady=0, sticky="ew")
        self.npsso_token_entry = ctk.CTkEntry(self.config_frame, placeholder_text="Enter NPSSO Token...", show="*", height=45, corner_radius=10, border_color="#444444", fg_color="#121212", font=ctk.CTkFont(size=13))
        self.npsso_token_entry.grid(row=2, column=0, padx=0, pady=20, sticky="ew")
        self.verify_button = ctk.CTkButton(self.config_frame, text="Verify Connection", command=self.verify_psn, height=40, corner_radius=10, font=ctk.CTkFont(size=14, weight="bold"), fg_color="#1f538d", hover_color="#296ab3")
        self.verify_button.grid(row=3, column=0, padx=0, pady=0, sticky="ew")

        self.button_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.button_frame.grid(row=3, column=0, padx=40, pady=(10, 30), sticky="ew")
        self.button_frame.grid_columnconfigure(0, weight=1)
        self.start_button = ctk.CTkButton(self.button_frame, text="Start Bridge", command=self.toggle_bridge, state="disabled", fg_color="#2d8a2d", hover_color="#36a136", height=60, corner_radius=12, font=ctk.CTkFont(size=18, weight="bold"))
        self.start_button.grid(row=0, column=0, padx=0, pady=(0, 15), sticky="ew")
        ctk.CTkButton(self.button_frame, text="Account Settings", command=self.open_settings, height=40, corner_radius=10, fg_color="#333333", hover_color="#444444", font=ctk.CTkFont(size=14)).grid(row=1, column=0, padx=0, pady=0, sticky="ew")

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.steam_user = config.get("steam_user", "")
                    self.steam_pass = config.get("steam_pass", "")
                    self.steam_refresh_token = config.get("steam_refresh_token", "")
                    
                    n = config.get("npsso", "")
                    if n: self.npsso_token_entry.insert(0, n)
            except Exception as e: logger.error(e)

    def _internal_save(self):
        config = {
            "steam_user": self.steam_user, 
            "steam_pass": self.steam_pass, 
            "steam_refresh_token": getattr(self, "steam_refresh_token", ""),
            "npsso": self.npsso_token_entry.get().strip()
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as f: 
                json.dump(config, f, indent=4, ensure_ascii=False)
            token_status = "Captured" if getattr(self, "steam_refresh_token", "") else "None"
            logger.info(f"GUI: Config saved (Token: {token_status})")
        except Exception as e: logger.error(f"Save Error: {e}")

    def poll_worker_events(self):
        try:
            while True:
                event, data = self.steam_worker.event_q.get_nowait()
                if event == "login_success":
                    self.steam_initialized = True
                    self.steam_status.configure(text="Steam: 🟢 Connected (Session Active)", text_color="#47d147")
                    if self.settings_window and self.settings_window.winfo_exists(): self.settings_window.destroy()
                    self.check_enable_start()
                elif event == "new_login_key":
                    if data != getattr(self, "steam_refresh_token", ""):
                        logger.info("GUI: NEW JWT refresh_token stored (ASF Model).")
                        self.steam_refresh_token = data
                        self._internal_save()
                elif event in ("need_2fa", "need_2fa_retry"):
                    t_type = data if (data in ("email", "app")) else "app" 
                    title = "Steam Guard"
                    msg = "Enter Code from Mobile App:" if t_type == "app" else "Enter Code from Email:"
                    if event == "need_2fa_retry": msg = f"Invalid Code! {msg}"
                    
                    code = simpledialog.askstring(title, msg, parent=self)
                    if code:
                        self.steam_worker.cmd_q.put(("submit_2fa", {"code": code, "type": t_type}))
                elif event == "login_fail":
                    logger.error(f"GUI: Steam Login Fail: {data}")
                    if "Invalid Username" in str(data):
                         self.steam_refresh_token = ""
                    self.steam_initialized = False
                    self.steam_status.configure(text=f"Steam: 🔴 {data}", text_color="#ff4d4d")
                elif event == "disconnected":
                    self.steam_initialized = False
                    self.steam_status.configure(text="Steam: 🔴 Disconnected", text_color="#ff4d4d")
        except queue.Empty: pass
        self.after(100, self.poll_worker_events)

    def verify_psn(self):
        if self.psn_verifying: return
        n = self.npsso_token_entry.get().strip()
        if not n: return
        self.psn_verifying = True
        logger.info("GUI: Verifying PSN...")
        self.psn_status.configure(text="PSN: 🟡 Verifying...", text_color="orange")
        threading.Thread(target=self._threaded_verify_psn, args=(n,), daemon=True).start()

    def _psn_auth(self, npsso):
        params = {"access_type": "offline", "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "scope": SCOPE}
        resp = requests.get(SONY_AUTH_URL, params=params, cookies={"npsso": npsso}, headers={"User-Agent": USER_AGENT}, allow_redirects=False, timeout=30)
        if resp.status_code != 302: return None
        loc = resp.headers.get("Location", "")
        if "code=" not in loc: return None
        grant = loc.split("code=")[1].split("&")[0]
        data = {"grant_type": "authorization_code", "code": grant, "redirect_uri": REDIRECT_URI, "token_format": "jwt"}
        headers = {"Authorization": PSN_CLIENT_AUTH, "User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"}
        resp = requests.post(SONY_TOKEN_URL, data=data, headers=headers, timeout=30)
        return resp.json().get("access_token")

    def _threaded_verify_psn(self, npsso):
        try:
            token = self._psn_auth(npsso)
            if token:
                self.psn_access_token = token
                self.psn_initialized = True
                self.after(0, lambda: self.psn_status.configure(text="PSN: 🟢 Connected", text_color="#47d147"))
                self.after(0, self._internal_save)
            else:
                self.psn_initialized = False
                self.after(0, lambda: self.psn_status.configure(text="PSN: 🔴 Error (NPSSO?)", text_color="#ff4d4d"))
        except Exception as e:
            logger.error(f"PSN Error: {e}")
            self.psn_initialized = False
            self.after(0, lambda: self.psn_status.configure(text="PSN: 🔴 Network Error", text_color="#ff4d4d"))
        finally:
            self.psn_verifying = False
            self.after(0, self.check_enable_start)

    def check_enable_start(self):
        state = "normal" if self.steam_initialized and self.psn_initialized else "disabled"
        self.start_button.configure(state=state)

    def bridge_loop(self):
        logger.info("GUI: Bridge loop active.")
        while self.is_bridge_running:
            try:
                if self.psn_access_token:
                    headers = {"Authorization": f"Bearer {self.psn_access_token}", "Accept": "application/json", "Accept-Language": "en-US", "User-Agent": USER_AGENT}
                    resp = requests.get(PSN_PRESENCE_URL, headers=headers, timeout=30)
                    if resp.status_code == 401:
                        self.psn_access_token = self._psn_auth(self.npsso_token_entry.get().strip())
                        continue
                    data = resp.json()
                    presence = data.get("basicPresence", {})
                    game_list = presence.get("gameTitleInfoList", [])
                    gn = game_list[0].get("titleName") if game_list else None
                    if not gn and presence.get("primaryPlatformInfo", {}).get("onlineStatus") == "online": gn = "Menu / Idle"
                    if not self.is_bridge_running:
                        break
                    logger.info(f"GUI: PSN Game -> {gn or 'Offline'}")
                    self.steam_worker.cmd_q.put(("set_status", gn))
                time.sleep(30)
            except Exception as e:
                logger.error(f"Bridge loop error: {e}")
                time.sleep(10)
        self.steam_worker.cmd_q.put(("set_status", {"game_name": "", "force": True}))

    def update_status(self):
        if not self.psn_initialized and not self.psn_verifying:
            n = self.npsso_token_entry.get().strip()
            if n: self.verify_psn()
        self.after(30000, self.update_status)

    def reset_steam_session(self):
        """ Clears all session data and forces a fresh login """
        if messagebox.askyesno("Reset Steam", "This will log you out and clear all cached Steam session files. Continue?"):
            self.steam_refresh_token = ""
            self.steam_worker.cmd_q.put(("logout", None))
            try:
                import shutil
                sdir = os.path.join(self.app_data_dir, "steam_session")
                if os.path.exists(sdir):
                    shutil.rmtree(sdir)
                os.makedirs(sdir, exist_ok=True)
            except Exception as e:
                logger.error(f"Failed to clear session dir: {e}")
            
            self._internal_save()
            self.steam_status.configure(text="Steam: 🟡 Resetting...", text_color="orange")
            messagebox.showinfo("Reset Complete", "Steam session cleared. Please log in again via settings.")
            if self.settings_window: self.settings_window.destroy()

    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.focus(); return
        self.settings_window = ctk.CTkToplevel(self)
        self.settings_window.title("Steam Credentials")
        self.settings_window.geometry("400x380")
        self.settings_window.attributes('-topmost', True)
        self.settings_window.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.settings_window, text="Steam Login", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, pady=(30, 20))
        ctk.CTkLabel(
            self.settings_window, text="Use your Steam ACCOUNT NAME (not display name)", 
            font=ctk.CTkFont(size=11), text_color="orange"
        ).grid(row=1, column=0, pady=(0, 10))
        
        u_e = ctk.CTkEntry(self.settings_window, width=280, placeholder_text="Account Name", height=40)
        u_e.insert(0, self.steam_user)
        u_e.grid(row=2, column=0, pady=5)
        
        p_e = ctk.CTkEntry(self.settings_window, width=280, show="*", placeholder_text="Password", height=40)
        p_e.insert(0, self.steam_pass)
        p_e.grid(row=3, column=0, pady=5)
        def do_login():
            self.steam_user = u_e.get().strip()
            self.steam_pass = p_e.get().strip()
            self.steam_refresh_token = ""
            self._internal_save()
            self.steam_status.configure(text="Steam: 🟡 Handshake...", text_color="orange")
            self.steam_worker.cmd_q.put(("login", {
                "user": self.steam_user, 
                "password": self.steam_pass
            }))

        ctk.CTkButton(self.settings_window, text="Login & Save", command=do_login, fg_color="#1f538d", height=40, width=280, font=ctk.CTkFont(weight="bold")).grid(row=4, column=0, pady=(20, 10))
        ctk.CTkButton(self.settings_window, text="Reset Steam Session", command=self.reset_steam_session, fg_color="#333333", height=30, width=280).grid(row=5, column=0, pady=(0, 10))
        ctk.CTkButton(self.settings_window, text="Cancel", command=self.settings_window.destroy, fg_color="transparent", border_width=1, height=30, width=280).grid(row=6, column=0, pady=0)

    def toggle_bridge(self):
        if not self.is_bridge_running:
            self.is_bridge_running = True
            self.start_button.configure(text="Stop Bridge", fg_color="#8b0000", hover_color="#a10000")
            threading.Thread(target=self.bridge_loop, daemon=True).start()
        else:
            self.is_bridge_running = False
            self.steam_worker.cmd_q.put(("set_status", {"game_name": "", "force": True}))
            self.start_button.configure(text="Start Bridge", fg_color="#2d8a2d", hover_color="#36a136")

    def on_close(self):
        if self.is_closing:
            return
        self.is_closing = True
        self.is_bridge_running = False
        try:
            self.steam_worker.cmd_q.put(("set_status", {"game_name": "", "force": True}))
            self.steam_worker.shutdown()
        except Exception as e:
            logger.error(f"GUI close error: {e}")
        self.after(300, self.destroy)

if __name__ == "__main__":
    app = BridgeApp()
    app.mainloop()


