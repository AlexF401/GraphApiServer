# Step-by-step setup guide

This guide takes you from a fresh server to a working email service, one step at a time. You don't need to know Docker. Copy each command exactly as shown.

**What is Docker, in one paragraph?** Docker runs an app inside a sealed box called a *container*, which already has the right version of Python and everything else the app needs. You don't install Python or any libraries yourself. You install Docker once, and then one command builds and starts the service. The same steps work on every server.

**Linux/Mac or Windows?** Wherever the commands differ, both versions are shown:
- **Linux / Mac** commands go in the Terminal (or your SSH window).
- **Windows PowerShell** commands go in PowerShell.

> **Windows tip:** in PowerShell, `curl` is *not* the real curl. It's a shortcut to `Invoke-WebRequest`, and the Linux-style examples fail with errors like `Cannot bind parameter 'Headers'`. Use the PowerShell versions below, or type `curl.exe` (with `.exe`) to get the real curl.

---

## What you need before you start

- **A server or computer that stays on.** For a small cloud server, these steps assume Ubuntu Linux (22.04 or 24.04). Windows or Mac with Docker Desktop also works.
- **A way to type commands on it.** For a cloud server, that's usually SSH, e.g. `ssh youruser@your-server-ip`.
- **An app registration in Microsoft Entra (Azure).** [Step 3](#step-3-set-up-the-app-registration-in-microsoft-entra) shows exactly what to set.
- **The username and password of the mailbox that should send the emails.** You sign in with it once in [step 6](#step-6-sign-in-to-microsoft).

---

## Step 1: Install Docker

### On Ubuntu Linux

Run these one at a time. When `sudo` asks for a password, type your server password (nothing shows while you type; that's normal).

```bash
sudo apt update
sudo apt install -y git curl
curl -fsSL https://get.docker.com | sudo sh
```

The last command is Docker's official installer. It takes a minute or two.

Next, allow your user to run Docker without typing `sudo` every time:

```bash
sudo usermod -aG docker $USER
```

**Now log out and log back in** (type `exit`, then SSH in again). This step is easy to miss, and Docker will say "permission denied" until you do it.

Check that it worked:

```bash
docker --version
docker compose version
```

You should see two version numbers. If you do, Docker is ready.

### On Windows or Mac

1. Download and install **Docker Desktop** from <https://www.docker.com/products/docker-desktop/>.
2. Open Docker Desktop and wait until it says it is running. Keep it running while you use the service.
3. Install Git from <https://git-scm.com/downloads> if you don't already have it.
4. Open **PowerShell** (Windows) or **Terminal** (Mac) and check that Docker works:

```powershell
docker --version
docker compose version
```

---

## Step 2: Download the code

**Linux / Mac:**

```bash
cd ~
git clone https://github.com/AlexF401/GraphApiServer.git
cd GraphApiServer
```

**Windows PowerShell:**

```powershell
cd $HOME
git clone https://github.com/AlexF401/GraphApiServer.git
cd GraphApiServer
```

You're now in the project folder. All the remaining commands are run from here. If you open a new window later, run `cd ~/GraphApiServer` first (or `cd $HOME\GraphApiServer` in PowerShell).

---

## Step 3: Set up the app registration in Microsoft Entra

Sign in to <https://entra.microsoft.com> and go to **Applications → App registrations**. Pick your app, or click **New registration** to make one: give it a name, choose "Accounts in this organizational directory only", leave Redirect URI empty, and click Register.

The service signs in **as a person** (the sending mailbox). Microsoft calls this *delegated* permission. These are the settings it needs:

### a) Turn on device sign-in

Go to **Authentication**. At the bottom, under **Advanced settings**, set **Allow public client flows** to **Yes**, then click **Save**.

(This allows the "enter a code at microsoft.com/devicelogin" sign-in. No client secret is needed.)

### b) Add the Mail.Send permission

1. Go to **API permissions → Add a permission → Microsoft Graph → Delegated permissions**.
2. Search for **Mail.Send**, tick it, and click **Add permissions**. `User.Read` is usually already there; keep it.
3. If you're an admin, click **Grant admin consent for <your organisation>**. If you're not, that's usually fine: you'll be asked to approve the permission when you sign in. If your organisation blocks this, ask an admin to grant consent.

### c) Copy two IDs

On the app's **Overview** page, copy these somewhere handy:

| What | Where it is on the Overview page |
| --- | --- |
| **Tenant ID** | "Directory (tenant) ID" |
| **Client ID** | "Application (client) ID" |

> **Using app-only (application) permissions instead?** Set `AUTH_MODE=application` in step 4. You'll need the **Application** type of `Mail.Send` with admin consent, a client secret (**Certificates & secrets**, copy the **Value**), and `SENDER_ADDRESS`. Skip step 6 in that case.

---

## Step 4: Create your settings file

The service reads its settings from a file called `.env`. Make one from the example.

**Linux / Mac:**

```bash
cp .env.example .env
```

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

### Make an API key for each app

Every app that sends email through this service gets its own secret key. Run this once for each app, and copy each result somewhere safe.

**Linux / Mac:**

```bash
openssl rand -hex 32
```

**Windows PowerShell:**

```powershell
-join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Max 256) })
```

### Fill in the file

Open the file in a simple text editor:
- **Linux:** `nano .env`
- **Mac:** `open -e .env`
- **Windows:** `notepad .env`

Change these lines to your own values, and leave the rest as they are:

```ini
TENANT_ID=paste-your-tenant-id
CLIENT_ID=paste-your-client-id
AUTH_MODE=delegated
API_KEYS=website:paste-first-key,billing:paste-second-key
```

Rules for this file:
- No spaces around `=`, and no quote marks around the values.
- In `API_KEYS`, each app is written as `name:key`, with commas between apps and no spaces. The name is just a label of your choice (it appears in the logs).
- Leave `CLIENT_SECRET` and `SENDER_ADDRESS` empty. They're only for `AUTH_MODE=application`.

**Save and exit nano:** press `Ctrl+O`, then `Enter` to save, then `Ctrl+X` to exit. In Notepad, use **File → Save**.

Keep `.env` private. Git is already set up to ignore it, so it won't be uploaded anywhere.

---

## Step 5: Start the service

This command is the same on every system:

```bash
docker compose up -d --build
```

- `up` starts the service, `--build` builds it first, and `-d` runs it in the background so it keeps running after you close the window.
- The first run takes a few minutes while it downloads and builds. Later runs are quick.
- The service starts again on its own if the server (or Docker Desktop) restarts.

Check that it's running:

```bash
docker compose ps
```

The `STATUS` column should say `Up`. After about 30 seconds it should also say `(healthy)`.

---

## Step 6: Sign in to Microsoft

You only do this **once per server**. The sign-in is saved, so the service stays signed in through restarts and updates.

Run this (it's the same on every system):

```bash
docker compose exec graphapiserver python -m app.signin
```

It shows something like this:

```
======================================================================
  1. On any computer or phone, open:  https://login.microsoft.com/device
  2. Enter this code:                 ABCD1234
  3. Sign in with the mailbox that should SEND the emails.
======================================================================
Waiting for you to finish (the code expires in about 15 minutes)...
```

1. Open that web address in any browser. It can be on your own PC or phone; it doesn't have to be the server.
2. Type in the code.
3. Sign in with the **mailbox that should send the emails** (e.g. `noreply@yourcompany.com`), not necessarily your own account. Accept the permissions prompt if one appears.
4. Go back to the terminal. After a few seconds it says `Signed in as noreply@yourcompany.com`.

All emails are sent **from the account you signed in with**.

### Check who it's signed in as

Any time you want to check the sign-in:

```bash
docker compose exec graphapiserver python -m app.signin --status
```

You'll see either `Signed in as ...` or `NOT signed in`.

### Good to know

- **If the service isn't signed in, emails are not lost.** They wait in the queue and go out automatically as soon as someone signs in. The logs show a reminder every few minutes.
- **The sign-in can stop working** if the mailbox's password is changed or reset, if an admin revokes sessions, or if the service sends nothing for about 90 days. Your organisation's security policies can also make it expire sooner. When that happens, emails wait in the queue and `--status` shows `NOT signed in`. Just run the sign-in command again.
- **To switch to a different sending mailbox,** run the sign-in command again and sign in with the other account. It replaces the old one.
- **To sign out completely:** `docker compose exec graphapiserver python -m app.signin --sign-out`

---

## Step 7: Check that it works

In the commands below:
- Replace `YOUR-API-KEY` with one of the keys from step 4.
- Replace `you@yourcompany.com` with your own address.

### 1. Health check

**Linux / Mac:**

```bash
curl http://localhost:8000/health
```

**Windows PowerShell:**

```powershell
Invoke-RestMethod http://localhost:8000/health
```

You should see `status: ok` and `signed_in: true`. (On Linux it looks like `{"status":"ok","signed_in":true,"queue":{...}}`. PowerShell shows the same information as a small table.) If `signed_in` is `false`, go back to step 6.

### 2. Check the sign-in through the API

**Linux / Mac:**

```bash
curl -H "X-API-Key: YOUR-API-KEY" http://localhost:8000/auth/status
```

**Windows PowerShell:**

```powershell
Invoke-RestMethod -Uri http://localhost:8000/auth/status -Headers @{ "X-API-Key" = "YOUR-API-KEY" }
```

This shows `signed_in` and the `account` emails are sent from.

### 3. Send yourself a test email

**Linux / Mac:**

```bash
curl -X POST http://localhost:8000/emails \
  -H "X-API-Key: YOUR-API-KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": ["you@yourcompany.com"], "subject": "Test from the email service", "body": "<p>It works!</p>"}'
```

**Windows PowerShell:**

```powershell
$headers = @{ "X-API-Key" = "YOUR-API-KEY" }
$body = '{"to": ["you@yourcompany.com"], "subject": "Test from the email service", "body": "<p>It works!</p>"}'
$result = Invoke-RestMethod -Method Post -Uri http://localhost:8000/emails -Headers $headers -ContentType "application/json" -Body $body
$result
```

You'll get back an `id`, which means the email has been queued. Within a few seconds it should arrive in your inbox.

### 4. Check what happened to it

**Linux / Mac** (replace `THE-ID` with the id you got back):

```bash
curl -H "X-API-Key: YOUR-API-KEY" http://localhost:8000/emails/THE-ID
```

**Windows PowerShell** (this reuses `$headers` and `$result` from the previous step):

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/emails/$($result.id)" -Headers $headers
```

In a new PowerShell window, set `$headers` again and paste the id in directly: `.../emails/THE-ID`.

What the `status` means:
- `sent`: it worked.
- `failed`: `last_error` explains why. See [Troubleshooting](#troubleshooting).
- `queued` with a `last_error`: it hit a temporary problem, or nobody is signed in. It will go out automatically once the problem clears.

### 5. Send a test email with an attachment (optional)

**Linux / Mac:**

```bash
FILE_B64=$(base64 -w0 report.pdf)    # on Mac: base64 -i report.pdf
curl -X POST http://localhost:8000/emails \
  -H "X-API-Key: YOUR-API-KEY" -H "Content-Type: application/json" \
  -d "{\"to\": [\"you@yourcompany.com\"], \"subject\": \"Attachment test\", \"body\": \"See attached\", \"attachments\": [{\"name\": \"report.pdf\", \"content_type\": \"application/pdf\", \"content_base64\": \"$FILE_B64\"}]}"
```

**Windows PowerShell:**

```powershell
$headers = @{ "X-API-Key" = "YOUR-API-KEY" }
$file = [Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\report.pdf"))
$body = @{
  to = @("you@yourcompany.com"); subject = "Attachment test"; body = "See attached"
  attachments = @(@{ name = "report.pdf"; content_type = "application/pdf"; content_base64 = $file })
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri http://localhost:8000/emails -Headers $headers -ContentType "application/json" -Body $body
```

---

## Step 8: Point your apps at it

Your apps send a `POST` request to `http://YOUR-SERVER:8000/emails` with their `X-API-Key` header. The README's [Sending an email](../README.md#sending-an-email) section lists all the fields, including attachments and scheduled sending.

If an app runs **on a different machine**, it has to be able to reach port 8000 on this server:
- If both machines are on the same private network (e.g. the same cloud VPC), allow port 8000 from that network in your cloud provider's firewall settings.
- If the app reaches the server **over the internet**, don't open port 8000 to everyone. Put the service behind HTTPS first, using a reverse proxy such as [Caddy](https://caddyserver.com/docs/quick-starts/reverse-proxy), so the API keys aren't sent in plain text.

---

## Everyday commands

Run these from the `GraphApiServer` folder. The `docker compose` commands are the same everywhere.

| I want to... | Command |
| --- | --- |
| See if it's running | `docker compose ps` |
| Check who it's signed in as | `docker compose exec graphapiserver python -m app.signin --status` |
| Sign in (or switch mailbox) | `docker compose exec graphapiserver python -m app.signin` |
| Watch the logs live (`Ctrl+C` to stop watching) | `docker compose logs -f` |
| Restart it (e.g. after editing `.env`) | `docker compose up -d` |
| Stop it | `docker compose down` |
| Start it again | `docker compose up -d` |
| Update to the latest code | `git pull` then `docker compose up -d --build` |

Commands that differ between systems:

| I want to... | Linux / Mac | Windows PowerShell |
| --- | --- | --- |
| See only failures in the logs | `docker compose logs \| grep -i failed` | `docker compose logs \| Select-String -Pattern failed` |
| Health check | `curl http://localhost:8000/health` | `Invoke-RestMethod http://localhost:8000/health` |
| List failed emails for an app | `curl -H "X-API-Key: KEY" "http://localhost:8000/emails?status=failed"` | `Invoke-RestMethod -Uri "http://localhost:8000/emails?status=failed" -Headers @{ "X-API-Key" = "KEY" }` |
| Resend a failed email | `curl -X POST -H "X-API-Key: KEY" http://localhost:8000/emails/ID/retry` | `Invoke-RestMethod -Method Post -Uri http://localhost:8000/emails/ID/retry -Headers @{ "X-API-Key" = "KEY" }` |

**About your data:** queued emails, the failure log and the saved Microsoft sign-in are stored in a Docker *volume*, a storage area that Docker manages for you. They survive restarts, updates and `docker compose down`. **Never run `docker compose down -v`**, because the `-v` deletes that storage: the queue *and* the sign-in.

---

## Setting up another server

Repeat steps 1 to 7 on the new server, including the sign-in in step 6. Each server keeps its own queue and its own sign-in. You can copy your `.env` file to the new server instead of typing it again, for example with `scp .env youruser@new-server:~/GraphApiServer/` after step 2.

---

## Troubleshooting

| What you see | What it means and how to fix it |
| --- | --- |
| PowerShell: `Cannot bind parameter 'Headers'` | You used the Linux `curl` example in PowerShell. Use the PowerShell version, or type `curl.exe` instead of `curl`. |
| `permission denied while trying to connect to the Docker daemon socket` | Linux: you haven't logged out and back in since step 1. Do that, or put `sudo` in front of the command. |
| `docker: command not found` / `docker compose` is not a command | Docker isn't installed, or Docker Desktop isn't running. Repeat step 1. |
| `service "graphapiserver" is not running` | Start it first with `docker compose up -d`, and make sure you're in the `GraphApiServer` folder. |
| `Failed to connect to localhost port 8000` / `Unable to connect to the remote server` | The service isn't running. Run `docker compose ps`, then `docker compose logs` to see why. |
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | Something else is already using port 8000. In `docker-compose.yml`, change `"8000:8000"` to e.g. `"8080:8000"`, then use port 8080 instead. |
| Service keeps restarting, logs mention `Field required` or `validation error` | A setting is missing or misspelled in `.env`. Compare it with `.env.example`. |
| `401` / `missing or invalid X-API-Key` | The key in your request doesn't match any key in `API_KEYS`. Check for typos or spaces. After changing `.env`, run `docker compose up -d`. |
| `signed_in: false`, or emails stay `queued` with "not signed in" | Nobody is signed in, or the sign-in expired. Run the sign-in command from step 6. |
| Sign-in: `AADSTS7000218` / "must contain client_assertion or client_secret" | **Allow public client flows** is off. Turn it on (step 3a), wait a minute, then sign in again. |
| Sign-in: `AADSTS700016` or `AADSTS90002`, or "not a valid application identifier" | `CLIENT_ID` or `TENANT_ID` is wrong. Copy both again from the app's Overview page, then run `docker compose up -d` before signing in again. |
| Sign-in: "Need admin approval" / `AADSTS65001` | Your organisation needs an admin to approve the app. Ask them to click **Grant admin consent** on the app's API permissions page (step 3b). |
| Sign-in: `AADSTS50076` / `AADSTS50079` (MFA) | Complete the multi-factor prompt in the browser. Device sign-in supports MFA. |
| `last_error` mentions `403` / `ErrorAccessDenied` / `Access is denied` | The signed-in mailbox isn't allowed to send, or `Mail.Send` isn't granted. Check step 3b, and that the account has an Exchange Online mailbox. |
| `last_error` mentions `MailboxNotEnabledForRESTAPI` / `ErrorInvalidUser` | The account you signed in with doesn't have an Exchange Online mailbox (or licence). Sign in with a mailbox account. |
| `413` when sending | The attachments add up to more than 3 MB. |
| The email shows `sent` but never arrived | Check your spam folder, and the mailbox's Sent Items folder if you set `SAVE_TO_SENT_ITEMS=true`. |

Still stuck? Run `docker compose logs --tail 100` and keep the output. It contains the exact error.
