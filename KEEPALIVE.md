# Keeping the free server awake

The site runs on Render's free tier, which **sleeps after about 15 minutes with
no visitors**. The next visit then waits up to a minute while the container
starts, and on a slow connection that looks like the site is broken.

A free uptime pinger fixes this by making one request every 10 minutes so the
service never sees 15 minutes of quiet.

## Setup (about 3 minutes, no account needed for cron-job.org)

1. Go to **https://cron-job.org** and create a free account.
2. **Create cronjob**.
3. Fill in:

   | Field | Value |
   |---|---|
   | Title | `graduation-photo-finder keepalive` |
   | URL | `https://graduation-photo-finder.onrender.com/api/health` |
   | Schedule | Every **10 minutes** |
   | Request method | `GET` |
   | Timeout | 60 seconds |

4. Save. The first run happens immediately; after that it keeps the service up.

## Why `/api/health`

- It is the cheapest endpoint: it returns a tiny JSON body and does no
  inference.
- Render counts it as traffic, which is all that is needed to prevent sleep.
- It also tells you the service is actually healthy, not just responding.

## What it costs

Nothing for the pinger. On Render, a free instance gets **750 instance-hours
per month**, and a month is roughly 730 hours — so keeping one free service
awake around the clock just fits, with little slack. If Render ever restarts
the service, it comes back within the same allowance.

If the allowance is exhausted, the service is suspended until the month rolls
over. In that case, lower the frequency to every 12–14 minutes, or accept the
sleep.

## Verify it is working

Open the site after leaving it alone for an hour. If it responds in a couple of
seconds instead of showing the Render wake screen, the keepalive is doing its
job.

You can also ask the service directly:

```bash
curl -s https://graduation-photo-finder.onrender.com/api/health
```

```json
{"ok": true, "faces": 82620, "dims": 512, "model": "arcface w600k_r50",
 "models_loaded": true, "stats_storage": "database"}
```

`models_loaded: true` means both models were loaded at startup, so the first
visitor does not pay for that.

## Alternative: pay for the smallest plan

Render Starter is $7/month, never sleeps, and has a faster CPU. Measured warm
searches there would be a few seconds rather than ~15. The keepalive is the
free option; this is the one that makes it feel like a normal website.
