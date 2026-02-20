const SteamUser = require('steam-user');
const path = require('path');
const fs = require('fs');

const dataDir = path.join(__dirname, '..', 'steam_session');

const client = new SteamUser({
  dataDirectory: dataDir,
  autoRelogin: false,
  renewRefreshTokens: true,
  language: 'english'
});

let pendingGuardCallback = null;

function emit(event, data) {
  const msg = { event, data };
  process.stdout.write(JSON.stringify(msg) + '\n');
}

function login(details) {
  const accountName = String(details.user || '').trim();
  const password = String(details.password || '').trim();
  const refreshToken = String(details.refresh_token || '').trim();

  const logon = {};
  if (refreshToken) {
    // steam-user rejects accountName together with refreshToken
    logon.refreshToken = refreshToken;
  } else if (password) {
    if (!accountName) {
      emit('login_fail', 'Missing username for password-based Node backend login');
      return;
    }

    logon.accountName = accountName;
    logon.password = password;

    // Explicitly load machine auth token for email-guard persistence.
    const machinePath = path.join(dataDir, `machineAuthToken.${accountName}.txt`);
    if (fs.existsSync(machinePath)) {
      try {
        const token = fs.readFileSync(machinePath, 'utf8').trim();
        if (token) {
          logon.machineAuthToken = token;
          emit('backend_info', `Loaded machineAuthToken (${token.length} chars) for ${accountName}`);
        }
      } catch (err) {
        emit('backend_error', `Failed to read machineAuthToken: ${err && err.message ? err.message : String(err)}`);
      }
    } else {
      emit('backend_info', `No machineAuthToken file found for ${accountName}`);
    }
  } else {
    emit('login_fail', 'Missing credentials (password or refresh_token) for Node backend login');
    return;
  }

  try {
    client.logOn(logon);
  } catch (err) {
    emit('login_fail', `Node logOn exception: ${err && err.message ? err.message : String(err)}`);
  }
}

function setStatus(details) {
  const gameName = String(details.game_name || '').trim();
  const gameId = Number(details.game_id || 0);

  if (!client.steamID) {
    return;
  }

  if (!gameName) {
    client.gamesPlayed([]);
    return;
  }

  if (Number.isFinite(gameId) && gameId > 0) {
    client.gamesPlayed([{ game_id: gameId, game_extra_info: `PS5: ${gameName}` }]);
  } else {
    client.gamesPlayed([`PS5: ${gameName}`]);
  }
}

client.on('loggedOn', () => {
  client.setPersona(SteamUser.EPersonaState.Online);
  emit('login_success', null);
});

client.on('error', (err) => {
  const info = err && err.eresult ? `${err.message || 'Steam error'} (EResult ${err.eresult})` : (err && err.message ? err.message : String(err));
  emit('login_fail', info);
});

client.on('refreshToken', (token) => {
  emit('new_login_key', token);
});

client.on('machineAuthToken', (token) => {
  emit('backend_info', `Received new machineAuthToken (${(token || '').length} chars)`);
});

client.on('steamGuard', (domain, callback, lastCodeWrong) => {
  pendingGuardCallback = callback;
  const kind = domain ? 'email' : 'app';
  emit(lastCodeWrong ? 'need_2fa_retry' : 'need_2fa', kind);
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
      emit('backend_error', `Invalid JSON command: ${line.slice(0, 80)}`);
      continue;
    }

    const cmd = msg.cmd;
    const data = msg.data || {};

    if (cmd === 'login') {
      login(data);
    } else if (cmd === 'submit_2fa') {
      const code = String((data && data.code) || '').trim();
      if (!pendingGuardCallback) {
        emit('backend_error', 'No pending steamGuard callback for submitted code');
      } else {
        const cb = pendingGuardCallback;
        pendingGuardCallback = null;
        cb(code);
      }
    } else if (cmd === 'set_status') {
      setStatus(data);
    } else if (cmd === 'logout') {
      client.logOff();
    } else if (cmd === 'shutdown') {
      try {
        client.logOff();
      } catch {}
      process.exit(0);
    } else {
      emit('backend_error', `Unknown command: ${String(cmd)}`);
    }
  }
});

emit('backend_ready', { dataDir });

process.on('uncaughtException', (err) => {
  emit('login_fail', `Node uncaught exception: ${err && err.message ? err.message : String(err)}`);
});

