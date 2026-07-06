# Sermon Explainer - Frontend

A single-page web app: fill in a form, submit a job, watch progress, download
the finished video. No build step, no framework - plain HTML/CSS/JavaScript,
which is why it deploys to Vercel with zero configuration.

## Files

| File | What it does |
|---|---|
| `index.html` | The page structure - the form, the progress screen, the results screen |
| `style.css` | All the visual styling |
| `app.js` | All the logic - submitting jobs, polling status, showing results/errors |
| `config.js` | The ONE setting you'll update once your Render backend is live |

## Testing this locally, right now, before Render exists

Since your backend isn't deployed yet, you can still see and click through
this page today, in two steps:

1. **Run your existing local backend** (the one from our earlier work), so
   something is actually listening on `http://localhost:8000`:
   ```
   uvicorn api:app --host 0.0.0.0 --port 8000
   ```
2. **Serve this frontend folder** with any simple local web server - for
   example, from inside this folder:
   ```
   python -m http.server 5500
   ```
   Then open `http://localhost:5500` in your browser.

`config.js` already points at `http://localhost:8000` by default, so this
should "just work" for local testing without changing anything.

## Deploying to Vercel

Once you're ready to put this online:

1. Put this folder in its own Git repository (or a folder within one).
2. Go to vercel.com, sign in, and choose **"Add New Project"**.
3. Import that repository. Vercel will detect this as a static site
   automatically - no build command or framework selection needed.
4. Click Deploy. You'll get a live URL like `https://your-project.vercel.app`.

## The one thing you MUST do before this works for real

Open `config.js` and change:
```js
const API_BASE_URL = "http://localhost:8000";
```
to your actual Render backend URL, e.g.:
```js
const API_BASE_URL = "https://your-backend-name.onrender.com";
```
Then redeploy (Vercel redeploys automatically on every push to your repo).

**Important:** until your Render backend is live and this value is updated,
the page will load fine but every "Generate" click will fail with a
"couldn't reach the server" message - that's expected, not a bug, since
there's nothing at `localhost:8000` once this is on the public internet.

## What's already handled in this frontend (for your reference)

- Source supports YouTube URL, browser file upload (mp3/video), or a
  backend-accessible local media path (for example: input/my_sermon.mp3)
- Output duration target is based on source length: over 1 hour targets
  about 10 minutes, and 1 hour or less targets about 5-6 minutes
- Shows the "I have permission" checkbox only when the source looks like a
  web link (not needed for local file references)
- Client-side clamping of intro/outro duration to 1-10 seconds
- Blank social links are simply left out of what gets sent
- Remembers an in-progress job if you refresh the page (via browser storage),
  so you don't lose track of a job that's still running
- Distinguishes retryable errors (shows a Retry button) from non-retryable
  ones (doesn't offer a misleading Retry)
- Warns before submission that generation consumes paid API credits
- Reminds you to download the finished video promptly, since your backend's
  hosting plan may not keep generated files indefinitely
- Shows a friendly "waking up the server" message if the backend doesn't
  respond right away - this is expected behavior on Render's free tier
  after 15 minutes of inactivity, not an error

## Upload support note

Browser upload requires the backend `POST /uploads` endpoint. The frontend
automatically uploads the selected file first, then submits the returned
server path as `source` when creating the job.
