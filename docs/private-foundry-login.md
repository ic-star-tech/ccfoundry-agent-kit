# Develop against a private Foundry

Dev Board supports an existing account on your Foundry instance. GitHub is
optional. This page covers developer login and claims; customer source
packaging, private registry deployment and dependency mirrors are described in
[Customer projects](customer-projects.md) and
[Intranet dependency setup](intranet-dependencies.md).

## Server and account

Use a Foundry release that provides developer sessions.
The required endpoint is `POST /api/developer/session`; older builds are rejected
with an upgrade message. An active local account must already exist. Dev Board
does not create accounts or approve agents. On current Foundry releases an
administrator must issue the invite and approve the agent. Until then the agent
keeps polling and shows `PENDING`; it does not need restarting.

Start Dev Board using the normal launcher. In Guided setup, step 3:

1. Enter the exact Foundry URL, for example `https://foundry.intranet.example`.
2. Keep **Foundry account (private network)** selected.
3. Enter that instance's username and password and click **Sign in to Foundry**.
4. Select a local agent source, then **Request Foundry ticket**. A source can
   receive a claim before its local runtime is started. Continue with the normal
   debug, discovery and approval workflow.

Notification preference sync and remote retirement use the same developer
session. Remote retirement follows the agent's recorded Foundry registration;
if that address differs, sign in to that instance first. Existing local retirement
behavior still reports a remote failure separately from local cleanup.

To test the approved agent in Foundry's browser app, sign in there separately.
Dev Board's developer session does not create a Foundry browser login session.
GitHub device/popup login remains available by explicitly selecting GitHub.

## Network and TLS

Both your browser and the Dev Board API need appropriate private network access.
The API makes the Foundry connection. Use HTTPS when accessing Dev Board remotely,
since the browser sends the password to the Dev Board API once during sign-in.
Use a trusted Dev Board host/process and its existing origin/access controls.

Certificate and hostname verification remain enabled. For a private CA, set
`CCFOUNDRY_DEV_BOARD_CA_BUNDLE=/absolute/path/to/ca-bundle.pem` in the Dev Board
process environment before starting it. The explicit bundle applies to developer
login, claim requests, notification preferences, retirement and developer route
probes. It does not configure agent model clients or other deployment tools.
An invalid bundle fails closed; certificates are never silently ignored.

Loopback HTTP is supported for local development. Existing
`CCFOUNDRY_ALLOW_INSECURE_REMOTE_HTTP=true` is an explicit opt-in for HTTP on a
private network; credentials on that connection are unencrypted. Prefer HTTPS.
These developer requests do not inherit proxy environment variables or follow
redirects. Use the final installation URL. Login exchanges are never replayed;
claim/retire route aliases are tried only on 404/405, not after an uncertain write.

## Session behavior

The Dev Board process exchanges the password for a normal Foundry access token,
then immediately exchanges that for a distinct developer credential. Passwords
and normal access tokens are not retained. The developer credential is held only
in process memory, with a maximum 15-minute lifetime. The browser receives a
random session handle bound to the exact scheme, hostname, port and installation
path. It holds this handle in memory, not localStorage or sessionStorage.

Changing the URL or login method, signing out, expiration, refreshing the page
or restarting Dev Board requires signing in again. Sign-out forgets this Dev
Board session; it does not log out all Foundry clients or retire agents. A single
Dev Board process owns its session cache; do not distribute requests for a
session across independent API workers.

Foundry rechecks account activation, UUID and session version. Logout-all,
account deactivation and password/session revocation reject outstanding developer
credentials and unredeemed claims. The agent's separately scoped discovery claim
still persists in its bootstrap state by design; the login credential, password
and Dev Board session handle do not. Claim lifetime and existing approved-agent
resources are governed by Foundry's own policy, not by the short login session.

## Validation

API tests use real loopback HTTP/TLS peers and temporary source directories.
They check credential exchange, source-only claims, notifications, retirement,
no GitHub discovery, redirects, origin/path binding, expiry, malformed sessions,
private CA and hostname verification, safe errors and absence of login credentials
in persisted files. They do not replace a test against a real Foundry deployment
or model call.

Browser tests exercise the built React application with fixture API responses:

```sh
npm ci --prefix apps/agent-dev-board-web
npm run build --prefix apps/agent-dev-board-web
npm ci --prefix tests/browser
npm exec --prefix tests/browser -- playwright install chromium
npm test --prefix tests/browser
```

They cover success, rejection, expiry, target changes with a pending login,
claim failure and switching login methods. CI runs them after the Web build;
an earlier failed Web audit/build means these tests are skipped there.
