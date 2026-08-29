const SteamUser = require('steam-user');
const path = require('path');
const fs = require('fs');

const dataDir = process.env.STEAM_SESSION_DIR
  ? path.resolve(process.env.STEAM_SESSION_DIR)
  : path.join(__dirname, '..', 'steam_session');

fs.mkdirSync(dataDir, { recursive: true });

const client = new SteamUser({
  dataDirectory: dataDir,
  autoRelogin: true,
  renewRefreshTokens: true,
  language: 'english'
});

let pendingGuardCallback = null;

function emit(event, data) {
  process.stdout.write(JSON.stringify({ event, data }) + '\n');
}

function log(message) {
  process.stderr.write(String(message) + '\n');
}

function login(details) {
  const accountName = String(details.user || '').trim();
  const password = String(details.password || '').trim();
  const refreshToken = String(details.refresh_token || '').trim();

  const logon = {};
  if (refreshToken) {
    logon.refreshToken = refreshToken;
  } else if (password) {
    if (!accountName) {
      emit('login_fail', 'Missing Steam account name');
      return;
    }

    logon.accountName = accountName;
    logon.password = password;

    const machinePath = path.join(dataDir, `machineAuthToken.${accountName}.txt`);
    if (fs.existsSync(machinePath)) {
      try {
        const token = fs.readFileSync(machinePath, 'utf8').trim();
        if (token) {
          logon.machineAuthToken = token;
        }
      } catch (err) {
        log(`Failed to read machineAuthToken: ${err.message || err}`);
      }
    }
  } else {
    emit('login_fail', 'Missing Steam password or saved session');
    return;
  }

  try {
    client.logOn(logon);
  } catch (err) {
    emit('login_fail', `Steam logOn failed: ${err.message || err}`);
  }
}

function setStatus(details) {
  const gameName = String(details.game_name || '').trim();
  if (!client.steamID) {
    return;
  }
  client.gamesPlayed(gameName ? [`PS5: ${gameName}`] : []);
}

client.on('loggedOn', () => {
  client.setPersona(SteamUser.EPersonaState.Online);
  emit('login_success', null);
});

client.on('error', (err) => {
  const info = err && err.eresult
    ? `${err.message || 'Steam error'} (EResult ${err.eresult})`
    : (err && err.message ? err.message : String(err));
  emit('login_fail', info);
});

client.on('refreshToken', (token) => {
  emit('new_login_key', token);
});

client.on('steamGuard', (domain, callback, lastCodeWrong) => {
  pendingGuardCallback = callback;
  emit(lastCodeWrong ? 'need_2fa_retry' : 'need_2fa', domain ? 'email' : 'app');
});

client.on('disconnected', (eresult, msg) => {
  emit('disconnected', `${msg || 'Disconnected'} (${eresult || 'n/a'})`);
});

let buffer = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buffer += chunk;
  while (true) {
    const idx = buffer.indexOf('\n');
    if (idx < 0) {
      break;
    }

    const line = buffer.slice(0, idx).trim();
    buffer = buffer.slice(idx + 1);
    if (!line) {
      continue;
    }

    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      log(`Invalid JSON command: ${line.slice(0, 80)}`);
      continue;
    }

    const cmd = msg.cmd;
    const data = msg.data || {};

    if (cmd === 'login') {
      login(data);
    } else if (cmd === 'submit_2fa') {
      const code = String((data && data.code) || '').trim();
      if (!pendingGuardCallback) {
        log('No pending Steam Guard callback');
      } else {
        const cb = pendingGuardCallback;
        pendingGuardCallback = null;
        cb(code);
      }
    } else if (cmd === 'set_status') {
      setStatus(data);
    } else if (cmd === 'logout') {
      try {
        client.logOff();
      } catch {}
    } else if (cmd === 'shutdown') {
      try {
        client.logOff();
      } catch {}
      process.exit(0);
    } else {
      log(`Unknown command: ${String(cmd)}`);
    }
  }
});

emit('backend_ready', { dataDir });

process.on('uncaughtException', (err) => {
  emit('login_fail', `Steam backend crashed: ${err.message || err}`);
});
