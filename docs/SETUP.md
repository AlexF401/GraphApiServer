# Step-by-step setup guide

This guide takes you from a fresh server to a working email service, one step at a time. You don't need to know Docker. Copy each command exactly as shown.

**What is Docker, in one paragraph?** Docker runs an app inside a sealed box called a *container*, which already has the right version of Python and everything else the app needs. You don't install Python or any libraries yourself. You install Docker once, and then one command builds and starts the service. The same steps work on every server.

---

## What you need before you start

- **A server or computer that stays on.** These steps assume Ubuntu Linux (22.04 or 24.04), which is the most common choice for a small cloud server. Windows and Mac notes are in [step 1](#step-1-install-docker).
- **A way to type commands on it.** For a cloud server, that's usually SSH, e.g. `ssh youruser@your-server-ip`. Everything below is typed into that terminal window.
- **From Microsoft Entra (Azure):** the details of the app registration that has the `Mail.Send` permission. [Step 3](#step-3-collect-your-microsoft-details) shows where to find each one:
  - Tenant ID
  - Client ID (also called Application ID)
  - A client secret
  - The mailbox address emails should come from

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
4. Use **PowerShell** (Windows) or **Terminal** (Mac) for the commands below. Where a command differs on Windows, a PowerShell version is shown.

---

## Step 2: Download the code

```bash
cd ~
git clone https://github.com/AlexF401/GraphApiServer.git
cd GraphApiServer
```

You're now in the project folder. All the remaining commands are run from here. If you open a new terminal later, run `cd ~/GraphApiServer` first.

---

## Step 3: Collect your Microsoft details

Sign in to <https://entra.microsoft.com> (or the Azure portal) and go to **Applications → App registrations → your app**.

| What you need | Where to find it |
| --- | --- |
| **Tenant ID** | The app's **Overview** page, "Directory (tenant) ID" |
| **Client ID** | The app's **Overview** page, "Application (client) ID" |
| **Client secret** | **Certificates & secrets → Client secrets → New client secret**. Copy the **Value** column (not the "Secret ID") straight away, because it is only shown once. Note the expiry date: when it expires, sending stops until you create a new secret and put it in `.env`. |
| **Sender address** | The mailbox emails should come from, e.g. `noreply@yourcompany.com`. It must be a real mailbox or shared mailbox in your Microsoft 365 tenant. |

Also check **API permissions**. `Mail.Send` should be listed with the type **Application** (not Delegated), and the status column should show a green tick saying it has been granted for your organisation. If it doesn't, click **Grant admin consent**.

---

## Step 4: Create your settings file

The service reads its settings from a file called `.env`. Make one from the example:

```bash
cp .env.example .env
```

### Make an API key for each app

Every app that sends email through this service gets its own secret key. Run this once for each app, and copy each result somewhere safe:

```bash
openssl rand -hex 32
```

On Windows PowerShell, use this instead:

```powershell
-join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Max 256) })
```

### Fill in the file

Open the file in a simple text editor:

```bash
nano .env
```

(On Windows: `notepad .env`. On Mac: `open -e .env`.)

Change these lines to your own values, and leave the rest as they are:

```ini
TENANT_ID=paste-your-tenant-id
CLIENT_ID=paste-your-client-id
CLIENT_SECRET=paste-your-client-secret-value
SENDER_ADDRESS=noreply@yourcompany.com
API_KEYS=website:paste-first-key,billing:paste-second-key
```

Rules for this file:
- No spaces around `=`, and no quote marks around the values.
- In `API_KEYS`, each app is written as `name:key`, with commas between apps and no spaces. The name is just a label of your choice (it appears in the logs).

**Save and exit nano:** press `Ctrl+O`, then `Enter` to save, then `Ctrl+X` to exit.

Keep `.env` private. It contains your Microsoft secret, and Git is already set up to ignore it, so it won't be uploaded anywhere.

---

## Step 5: Start the service

```bash
docker compose up -d --build
```

- `up` starts the service, `--build` builds it first, and `-d` runs it in the background so it keeps running after you close the terminal.
- The first run takes a few minutes while it downloads and builds. Later runs are quick.
- The service starts again on its own if the server reboots.

Check that it's running:

```bash
docker compose ps
```

The `STATUS` column should say `Up`. After about 30 seconds it should also say `(healthy)`.

---

## Step 6: Check that it works

### 1. Health check

```bash
curl http://localhost:8000/health
```

You should get something like:

```json
{"status":"ok","queue":{"queued":0,"sending":0,"sent":0,"failed":0}}
```

### 2. Send yourself a test email

Replace `YOUR-API-KEY` with one of the keys from step 4, and `you@yourcompany.com` with your own address.

**Linux / Mac:**

```bash
curl -X POST http://localhost:8000/emails \
  -H "X-API-Key: YOUR-API-KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": ["you@yourcompany.com"], "subject": "Test from the email service", "body": "<p>It works!</p>"}'
```

**Windows PowerShell:**

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/emails `
  -Headers @{ "X-API-Key" = "YOUR-API-KEY" } -ContentType "application/json" `
  -Body '{"to": ["you@yourcompany.com"], "subject": "Test from the email service", "body": "<p>It works!</p>"}'
```

You'll get back an `id`, which means the email has been queued. Within a few seconds it should arrive in your inbox.

### 3. Check what happened to it

Replace `THE-ID` with the id you got back:

```bash
curl -H "X-API-Key: YOUR-API-KEY" http://localhost:8000/emails/THE-ID
```

- `"status": "sent"`: it worked.
- `"status": "failed"`: `last_error` explains why. See [Troubleshooting](#troubleshooting).
- `"status": "queued"` with a `last_error`: it hit a temporary problem and will try again automatically.

---

## Step 7: Point your apps at it

Your apps send a `POST` request to `http://YOUR-SERVER:8000/emails` with their `X-API-Key` header. The README's [Sending an email](../README.md#sending-an-email) section lists all the fields, including attachments and scheduled sending.

If an app runs **on a different machine**, it has to be able to reach port 8000 on this server:
- If both machines are on the same private network (e.g. the same cloud VPC), allow port 8000 from that network in your cloud provider's firewall settings.
- If the app reaches the server **over the internet**, don't open port 8000 to everyone. Put the service behind HTTPS first, using a reverse proxy such as [Caddy](https://caddyserver.com/docs/quick-starts/reverse-proxy), so the API keys aren't sent in plain text.

---

## Everyday commands

Run these from the `GraphApiServer` folder.

| I want to... | Command |
| --- | --- |
| See if it's running | `docker compose ps` |
| Watch the logs live (`Ctrl+C` to stop watching) | `docker compose logs -f` |
| See only failures in the logs | `docker compose logs \| grep -i failed` |
| List failed emails for an app | `curl -H "X-API-Key: KEY" "http://localhost:8000/emails?status=failed"` |
| Resend a failed email | `curl -X POST -H "X-API-Key: KEY" http://localhost:8000/emails/ID/retry` |
| Restart it (e.g. after editing `.env`) | `docker compose up -d` |
| Stop it | `docker compose down` |
| Start it again | `docker compose up -d` |
| Update to the latest code | `git pull` then `docker compose up -d --build` |

**About your data:** queued emails and the failure log are stored in a Docker *volume*, a storage area that Docker manages for you. They survive restarts, updates and `docker compose down`. **Never run `docker compose down -v`**, because the `-v` deletes that storage and everything in the queue with it.

---

## Setting up another server

Repeat steps 1 to 6 on the new server. Each server keeps its own separate queue. You can copy your `.env` file to the new server instead of typing it again, for example with `scp .env youruser@new-server:~/GraphApiServer/` after step 2.

---

## Troubleshooting

| What you see | What it means and how to fix it |
| --- | --- |
| `permission denied while trying to connect to the Docker daemon socket` | You haven't logged out and back in since step 1. Do that, or put `sudo` in front of the command. |
| `docker: command not found` / `docker compose` is not a command | Docker isn't installed, or Docker Desktop isn't running. Repeat step 1. |
| `curl: (7) Failed to connect to localhost port 8000` | The service isn't running. Run `docker compose ps`, then `docker compose logs` to see why. |
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | Something else is already using port 8000. In `docker-compose.yml`, change `"8000:8000"` to e.g. `"8080:8000"`, then use port 8080 instead. |
| Service keeps restarting, logs mention `Field required` or `validation error` | A setting is missing or misspelled in `.env`. Compare it with `.env.example`. |
| `401` / `missing or invalid X-API-Key` | The key in your request doesn't match any key in `API_KEYS`. Check for typos or spaces. After changing `.env`, run `docker compose up -d`. |
| `last_error` mentions `AADSTS7000215` or `invalid_client` | The client secret is wrong or has expired. Copy the secret's **Value** (not the Secret ID) into `.env`, or create a new secret. |
| `last_error` mentions `AADSTS700016` or `AADSTS90002` | The client ID or tenant ID is wrong. Copy both again from the app's Overview page. |
| `last_error` mentions `403` / `ErrorAccessDenied` / `Access is denied` | `Mail.Send` isn't an **Application** permission with admin consent, or a mailbox restriction policy is blocking the sender address. See step 3. |
| `last_error` mentions `404` / `ErrorInvalidUser` / `MailboxNotEnabledForRESTAPI` | `SENDER_ADDRESS` isn't a real mailbox in your tenant, or the mailbox has no Exchange Online licence. |
| `413` when sending | The attachments add up to more than 3 MB. |
| The email shows `sent` but never arrived | Check your spam folder, and the mailbox's Sent Items folder if you set `SAVE_TO_SENT_ITEMS=true`. |

Still stuck? Run `docker compose logs --tail 100` and keep the output. It contains the exact error.
